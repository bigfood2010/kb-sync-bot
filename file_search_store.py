import io
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

from google import genai

from sync_plan import build_sync_plan

STORE_DISPLAY_NAME = "kb-sync-bot"
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
CHARS_PER_TOKEN = 3.5
POLL_SECONDS = 2
UPLOAD_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class StoredDocument:
    slug: str
    content_hash: str
    name: str


class Store:
    """Keeps one store document per Article, in step with the given Articles."""

    def __init__(self, documents):
        self._documents = documents

    def sync(self, articles, deadline=None):
        # Expects the complete Article list: anything missing from it is deleted.
        # Uploads stop after the monotonic deadline. Deferred Articles stay changed, so the next run picks them up.
        local_hashes = {article.slug: article.content_hash for article in articles}
        stored = defaultdict(list)
        for document in self._documents.list():
            stored[document.slug].append(document)
        plan = build_sync_plan(
            local_hashes=local_hashes,
            stored_hashes={slug: _current_hash(copies, local_hashes.get(slug)) for slug, copies in stored.items()},
        )
        if len(plan.deleted) * 2 > len(stored):
            # An empty or broken fetch must not wipe the store.
            raise RuntimeError(f"Refusing to delete {len(plan.deleted)} of {len(stored)} stored Articles")
        by_slug = {article.slug: article for article in articles}
        for slug in plan.added + plan.updated:
            if deadline is not None and time.monotonic() > deadline:
                plan.deferred.append(slug)
                continue
            try:
                self._documents.upload(by_slug[slug])
            except TimeoutError:
                raise
            except Exception as error:
                print(f"Warning: upload failed for {slug}: {error}", file=sys.stderr)
                plan.failed.append(slug)
                continue
            for document in stored[slug]:
                self._documents.delete(document.name)  # only after the new copy is live
        pending = set(plan.deferred + plan.failed)
        plan.added = [slug for slug in plan.added if slug not in pending]
        plan.updated = [slug for slug in plan.updated if slug not in pending]
        for slug in plan.skipped:
            # Leftovers of an interrupted update: an extra copy of the same Article.
            duplicates = [document for document in stored[slug] if document.content_hash != local_hashes[slug]]
            duplicates += [document for document in stored[slug] if document.content_hash == local_hashes[slug]][1:]
            for document in duplicates:
                self._documents.delete(document.name)
        for slug in plan.deleted:
            for document in stored[slug]:
                self._documents.delete(document.name)
        plan.estimated_chunks = sum(_estimate_chunk_count(by_slug[slug].markdown) for slug in plan.added + plan.updated)
        return plan


class GeminiDocuments:
    def __init__(self, client, store_name):
        self.client = client
        self.store_name = store_name

    def list(self):
        documents = []
        for document in self.client.file_search_stores.documents.list(parent=self.store_name, config={"page_size": 20}):
            metadata = {item.key: item.string_value for item in document.custom_metadata or []}
            if "slug" in metadata:
                documents.append(StoredDocument(metadata["slug"], metadata.get("sha256"), document.name))
        return documents

    def upload(self, article):
        operation = self.client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=self.store_name,
            file=io.BytesIO(article.markdown.encode()),
            config={
                "display_name": f"{article.slug}.md",
                "mime_type": "text/markdown",
                "custom_metadata": [
                    {"key": "slug", "string_value": article.slug},
                    {"key": "sha256", "string_value": article.content_hash},
                    {"key": "url", "string_value": article.url},
                ],
                "chunking_config": {
                    "white_space_config": {
                        "max_tokens_per_chunk": CHUNK_SIZE_TOKENS,
                        "max_overlap_tokens": CHUNK_OVERLAP_TOKENS,
                    }
                },
            },
        )
        give_up_at = time.monotonic() + UPLOAD_TIMEOUT_SECONDS
        while not operation.done:
            if time.monotonic() > give_up_at:
                raise RuntimeError(f"Upload for {article.slug} still running after {UPLOAD_TIMEOUT_SECONDS} seconds")
            time.sleep(POLL_SECONDS)
            operation = self.client.operations.get(operation)
        if operation.error:
            raise RuntimeError(f"Upload failed for {article.slug}: {operation.error}")

    def delete(self, document_name):
        self.client.file_search_stores.documents.delete(name=document_name, config={"force": True})


def open_gemini_documents():
    api_key = os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("API_KEY (or GEMINI_API_KEY) is not set")
    client = genai.Client(api_key=api_key)
    for store in client.file_search_stores.list():
        if store.display_name == STORE_DISPLAY_NAME:
            return GeminiDocuments(client, store.name)
    return GeminiDocuments(client, client.file_search_stores.create(config={"display_name": STORE_DISPLAY_NAME}).name)


def _estimate_chunk_count(markdown):
    tokens = len(markdown) / CHARS_PER_TOKEN
    if tokens <= CHUNK_SIZE_TOKENS:
        return 1
    return int(-(-(tokens - CHUNK_OVERLAP_TOKENS) // (CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS)))


def _current_hash(documents, local_hash):
    hashes = [document.content_hash for document in documents]
    return local_hash if local_hash in hashes else hashes[0]
