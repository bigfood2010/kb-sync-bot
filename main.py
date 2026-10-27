import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from google import genai

import file_search_store
import help_center
from sync_plan import build_sync_plan, estimate_chunk_count

# The platform never stops a hung job. 0 disables the limit.
TIMEOUT_SECONDS = int(os.environ.get("TIMEOUT_SECONDS", 15 * 60))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "data/articles"))


def main():
    api_key = os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("API_KEY (or GEMINI_API_KEY) is not set")

    articles = scrape_articles_to_markdown()
    client = genai.Client(api_key=api_key)
    store_name = file_search_store.get_or_create_store(client)
    stored_documents = file_search_store.read_stored_documents(client, store_name)

    plan = build_sync_plan(
        local_hashes={slug: article[1] for slug, article in articles.items()},
        stored_hashes={slug: document[0] for slug, document in stored_documents.items()},
    )
    file_search_store.apply_sync_plan(client, store_name, plan, articles, stored_documents)

    summary = build_run_summary(plan, articles)
    print(json.dumps(summary, indent=2))
    publish_run_summary(summary)


def scrape_articles_to_markdown():
    """Returns slug -> (markdown, content hash, url) and writes each <slug>.md."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    articles = {}
    for article in help_center.fetch_published_articles():
        slug = help_center.article_slug(article)
        markdown = help_center.article_to_markdown(article)
        (OUTPUT_DIR / f"{slug}.md").write_text(markdown)
        articles[slug] = (markdown, help_center.content_hash(markdown), article["html_url"])
    return articles


def build_run_summary(plan, articles):
    uploaded_slugs = plan.added + plan.updated
    chunk_size = file_search_store.CHUNK_SIZE_TOKENS
    chunk_overlap = file_search_store.CHUNK_OVERLAP_TOKENS
    return {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "articles_fetched": len(articles),
        "added": len(plan.added),
        "updated": len(plan.updated),
        "skipped": len(plan.skipped),
        "deleted": len(plan.deleted),
        "files_embedded": len(uploaded_slugs),
        "chunks_embedded_estimated": sum(
            estimate_chunk_count(articles[slug][0], chunk_size, chunk_overlap) for slug in uploaded_slugs
        ),
        "chunk_config": {"max_tokens_per_chunk": chunk_size, "max_overlap_tokens": chunk_overlap},
    }


def publish_run_summary(summary):
    gist_id, gist_token = os.environ.get("GIST_ID"), os.environ.get("GIST_TOKEN")
    if not (gist_id and gist_token):
        return
    try:
        response = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"Bearer {gist_token}", "Accept": "application/vnd.github+json"},
            json={"files": {"last_run.json": {"content": json.dumps(summary, indent=2)}}},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"Warning: run summary not published to gist: {error}", file=sys.stderr)


def _raise_timeout(signal_number, frame):
    raise TimeoutError(f"Run exceeded {TIMEOUT_SECONDS} seconds")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(TIMEOUT_SECONDS)
    main()
