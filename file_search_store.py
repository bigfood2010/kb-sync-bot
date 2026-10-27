import io
import os
import time
from dataclasses import dataclass

from google import genai

from sync_plan import build_sync_plan

STORE_DISPLAY_NAME = "kb-sync-bot"
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
CHARS_PER_TOKEN = 3.5
POLL_SECONDS = 2


@dataclass(frozen=True)
class StoredDocument:
    slug: str
    content_hash: str
    name: str


class Store:
    """Keeps one store document per Article, in step with the given Articles."""

    def __init__(self, documents):
        self._documents = documents

    def sync(self, articles):
        # Expects the complete Article list: anything missing from it is deleted.
        stored = {document.slug: document for document in self._documents.list()}
        plan = build_sync_plan(
            local_hashes={article.slug: article.content_hash for article in articles},
            stored_hashes={slug: document.content_hash for slug, document in stored.items()},
        )
        by_slug = {article.slug: article for article in articles}
        for slug in plan.added + plan.updated:
            self._documents.upload(by_slug[slug])
            if slug in stored:
                self._documents.delete(stored[slug].name)  # only after the new copy is live
        for slug in plan.deleted:
            self._documents.delete(stored[slug].name)
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
        while not operation.done:
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
