# kb-sync-bot

support.optisigns.com → Markdown → Gemini File Search, synced daily by content hash.

```text
main
  scrape_articles_to_markdown      Zendesk API, all pages, fail on any page error
    article_to_markdown            # Title + "Article URL:" header, tables/code/links kept
  read_stored_documents            slug → (sha256, doc) from customMetadata
  build_sync_plan                  added | updated | skipped | deleted
  apply_sync_plan                  update = upload new, then delete old
  publish_run_summary              last_run.json → public gist
```

```text
help_center.py        scrape + HTML→Markdown
sync_plan.py          pure diff logic, chunk estimate
file_search_store.py  Gemini store I/O
main.py               daily job entrypoint, 15 min self-timeout
ask.py                OptiBot (verbatim system prompt) over the store
```

## Run

```bash
cp .env.sample .env                                   # API_KEY=<Gemini key>
docker build -t kb-sync-bot . && docker run --rm --env-file .env kb-sync-bot
python ask.py "How do I add a YouTube video?"
pytest -q
```

First full load (415 articles) exceeds 15 min on free tier: run once with `TIMEOUT_SECONDS=0`.

## Decisions

| | Choice | Why |
|---|---|---|
| Model | Gemini File Search, `gemini-3.1-flash-lite` | Free storage. Assistants API sunset 2026-08-26. 3.8-flash free tier = 20 RPD |
| Chunking | whitespace, 512 tokens, 64 overlap | Median article ≈1,140 tokens → 2–3 chunks; one how-to section per chunk |
| Chunk count | `chunks_embedded_estimated` = chars / 3.5 | Gemini exposes no chunk count. Ratio measured via `countTokens` on 40 articles (±25%) |
| Delta | SHA-256 of Markdown | Full fetch is 5 requests; `updated_at` saves nothing |
| Delete | only after every page fetched | Partial fetch must not look like deletions |
| Host | DigitalOcean App Platform scheduled job, smallest size | ≈$0.05/month worst case |

## Evidence

First load: 415 articles, 411 then 4 (one article carried a 125 KB inline base64 image, now dropped). Re-run on unchanged content:

```json
{ "articles_fetched": 415, "added": 0, "updated": 0, "skipped": 415, "deleted": 0, "files_embedded": 0 }
```

`pytest -q`: 8 passed (live store test runs when `API_KEY` is set).

## Logs

Daily run summary: https://gist.github.com/bigfood2010/4b0d782f317072fcc0b69381125bded8 (revisions = history)

## Sample answer

![How do I add a YouTube video?](docs/answer.png)
