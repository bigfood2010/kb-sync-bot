import io
import time

STORE_DISPLAY_NAME = "kb-sync-bot"
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
POLL_SECONDS = 2


def get_or_create_store(client):
    for store in client.file_search_stores.list():
        if store.display_name == STORE_DISPLAY_NAME:
            return store.name
    return client.file_search_stores.create(config={"display_name": STORE_DISPLAY_NAME}).name


def read_stored_documents(client, store_name):
    """Returns slug -> (content hash, document name)."""
    documents = {}
    for document in client.file_search_stores.documents.list(parent=store_name, config={"page_size": 20}):
        metadata = {item.key: item.string_value for item in document.custom_metadata or []}
        if "slug" in metadata:
            documents[metadata["slug"]] = (metadata.get("sha256"), document.name)
    return documents


def apply_sync_plan(client, store_name, plan, articles, stored_documents):
    """articles: slug -> (markdown, content hash, url)."""
    for slug in plan.added + plan.updated:
        _upload_document(client, store_name, slug, *articles[slug])
        if slug in stored_documents:
            _delete_document(client, stored_documents[slug][1])  # only after the new copy is live
    for slug in plan.deleted:
        _delete_document(client, stored_documents[slug][1])


def _upload_document(client, store_name, slug, markdown, content_hash, url):
    operation = client.file_search_stores.upload_to_file_search_store(
        file_search_store_name=store_name,
        file=io.BytesIO(markdown.encode()),
        config={
            "display_name": f"{slug}.md",
            "mime_type": "text/markdown",
            "custom_metadata": [
                {"key": "slug", "string_value": slug},
                {"key": "sha256", "string_value": content_hash},
                {"key": "url", "string_value": url},
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
        operation = client.operations.get(operation)
    if operation.error:
        raise RuntimeError(f"Upload failed for {slug}: {operation.error}")


def _delete_document(client, document_name):
    client.file_search_stores.documents.delete(name=document_name, config={"force": True})
