import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

import help_center
from file_search_store import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS, Store, open_gemini_documents

# The platform never stops a hung job. 0 disables the limit.
TIMEOUT_SECONDS = int(os.environ.get("TIMEOUT_SECONDS", 15 * 60))
# Uploads stop this long before the hard limit, so the run summary still gets published.
SUMMARY_MARGIN_SECONDS = 120
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "data/articles"))


def main():
    if TIMEOUT_SECONDS < 0 or 0 < TIMEOUT_SECONDS <= SUMMARY_MARGIN_SECONDS:
        raise SystemExit(f"TIMEOUT_SECONDS must be 0 or above {SUMMARY_MARGIN_SECONDS}")
    deadline = time.monotonic() + TIMEOUT_SECONDS - SUMMARY_MARGIN_SECONDS if TIMEOUT_SECONDS else None
    documents = open_gemini_documents()
    articles = scrape_articles_to_markdown()
    plan = Store(documents).sync(articles, deadline)
    summary = build_run_summary(plan, articles)
    print(json.dumps(summary, indent=2))
    publish_run_summary(summary)
    if plan.failed:
        raise SystemExit(f"{len(plan.failed)} uploads failed")


def scrape_articles_to_markdown():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    articles = [help_center.to_article(raw) for raw in help_center.fetch_published_articles()]
    for article in articles:
        (OUTPUT_DIR / f"{article.slug}.md").write_text(article.markdown)
    return articles


def build_run_summary(plan, articles):
    return {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "articles_fetched": len(articles),
        "added": len(plan.added),
        "updated": len(plan.updated),
        "skipped": len(plan.skipped),
        "deleted": len(plan.deleted),
        "deferred": len(plan.deferred),
        "failed": len(plan.failed),
        "files_embedded": len(plan.added) + len(plan.updated),
        "chunks_embedded_estimated": plan.estimated_chunks,
        "chunk_config": {"max_tokens_per_chunk": CHUNK_SIZE_TOKENS, "max_overlap_tokens": CHUNK_OVERLAP_TOKENS},
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
