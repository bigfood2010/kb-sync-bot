import os
import re
from unittest import mock

import pytest

import help_center
from file_search_store import Store, StoredDocument
from help_center import Article

ARTICLE = {
    "title": "Add a YouTube video",
    "html_url": "https://support.optisigns.com/hc/en-us/articles/123-add-a-youtube-video",
    "draft": False,
    "outdated": False,
    "body": (
        "<h2>Steps</h2><p>Open <a href='#setup'>setup</a>.</p>"
        "<table><tr><td>Key</td><td>Value</td></tr><tr><td>a</td><td>1</td></tr></table>"
        "<pre><code>docker run app</code></pre>"
        "<iframe src='https://www.youtube.com/embed/x'></iframe>"
        "<script>alert(1)</script>"
        "<img src='data:image/png;base64,AAAA' alt='QR code'>"
        "<img src='https://cdn.example.com/a.png' alt='screen'>"
        "<img src='https://cdn.example.com/b.png' alt='mceclip0.png'>"
        "<img src='https://cdn.example.com/c.png'>"
        "<h3>&nbsp;</h3><p>Click&nbsp;Save</p>"
    ),
}


def test_markdown_keeps_structure_and_drops_noise():
    markdown = help_center.article_to_markdown(ARTICLE)
    assert markdown.startswith(
        "# Add a YouTube video\n\nArticle URL: https://support.optisigns.com/hc/en-us/articles/123"
    )
    assert "## Steps" in markdown
    assert "[setup](#setup)" in markdown
    assert "| Key | Value |" in markdown
    assert "docker run app" in markdown and "```" in markdown
    assert "[Embedded content](https://www.youtube.com/embed/x)" in markdown
    assert "alert(1)" not in markdown
    assert "base64" not in markdown and "QR code" in markdown
    assert "![screen](https://cdn.example.com/a.png)" in markdown
    assert "b.png" not in markdown and "c.png" not in markdown
    assert "Click Save" in markdown and "\xa0" not in markdown
    assert not re.search(r"^#{1,6}\s*$", markdown, re.MULTILINE)


def test_slug_is_last_url_segment():
    assert help_center.article_slug(ARTICLE) == "123-add-a-youtube-video"


class InMemoryDocuments:
    def __init__(self, *documents, fail_upload_for=None):
        self.documents = {document.name: document for document in documents}
        self.fail_upload_for = fail_upload_for
        self.calls = []

    def list(self):
        return list(self.documents.values())

    def upload(self, article):
        self.calls.append(("upload", article.slug))
        if article.slug == self.fail_upload_for:
            raise RuntimeError("upload failed")
        name = f"{article.slug}@{article.content_hash}"
        self.documents[name] = StoredDocument(article.slug, article.content_hash, name)

    def delete(self, name):
        self.calls.append(("delete", name))
        del self.documents[name]


def _article(slug, content_hash, markdown="text"):
    return Article(slug, markdown, content_hash, f"https://example.com/{slug}")


def _stored(slug, content_hash):
    return StoredDocument(slug, content_hash, f"{slug}@{content_hash}")


def test_sync_adds_updates_skips_and_deletes():
    documents = InMemoryDocuments(_stored("same", "1"), _stored("changed", "old"), _stored("gone", "9"))
    plan = Store(documents).sync([_article("same", "1"), _article("changed", "new"), _article("fresh", "2")])
    assert (plan.added, plan.updated, plan.skipped, plan.deleted) == (["fresh"], ["changed"], ["same"], ["gone"])
    assert sorted(documents.documents) == ["changed@new", "fresh@2", "same@1"]


def test_update_uploads_new_copy_before_deleting_old_one():
    documents = InMemoryDocuments(_stored("changed", "old"))
    Store(documents).sync([_article("changed", "new")])
    assert documents.calls == [("upload", "changed"), ("delete", "changed@old")]


def test_failed_upload_keeps_old_copy():
    documents = InMemoryDocuments(_stored("changed", "old"), fail_upload_for="changed")
    plan = Store(documents).sync([_article("changed", "new")])
    assert plan.failed == ["changed"] and list(documents.documents) == ["changed@old"]


def test_second_sync_of_same_articles_skips_everything():
    documents = InMemoryDocuments()
    articles = [_article("a", "1"), _article("b", "2")]
    Store(documents).sync(articles)
    plan = Store(documents).sync(articles)
    assert plan.skipped == ["a", "b"] and not (plan.added or plan.updated or plan.deleted)


def test_estimated_chunks_cover_uploaded_articles_only():
    documents = InMemoryDocuments(_stored("same", "1"))
    plan = Store(documents).sync([_article("same", "1", "x" * 3500), _article("long", "2", "x" * 3500)])
    assert plan.estimated_chunks == 3  # 1000 tokens: (1000 - 64) / 448 = 2.09, ceil 3


class _FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.headers = status, body, {"Retry-After": "0"}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _FakeSession:
    def __init__(self, responses):
        self.responses, self.headers = list(responses), {}

    def get(self, url, timeout):
        return self.responses.pop(0)


def test_sync_removes_copies_left_by_an_interrupted_update():
    documents = InMemoryDocuments(_stored("a", "old"), _stored("a", "new"), StoredDocument("a", "new", "a@new-2"))
    plan = Store(documents).sync([_article("a", "new")])
    assert plan.skipped == ["a"] and [document.content_hash for document in documents.list()] == ["new"]


def test_sync_refuses_to_wipe_store_on_empty_fetch():
    documents = InMemoryDocuments(_stored("a", "1"), _stored("b", "2"))
    with pytest.raises(RuntimeError):
        Store(documents).sync([])
    assert len(documents.documents) == 2


def test_failed_upload_does_not_stop_other_articles():
    documents = InMemoryDocuments(_stored("gone", "9"), _stored("keep", "1"), _stored("a", "old"), fail_upload_for="a")
    plan = Store(documents).sync([_article("a", "new"), _article("b", "1"), _article("keep", "1")])
    assert plan.failed == ["a"] and plan.added == ["b"] and plan.deleted == ["gone"]
    assert sorted(documents.documents) == ["a@old", "b@1", "keep@1"]


def test_fetch_retries_server_errors_and_date_retry_after():
    dated = _FakeResponse(503)
    dated.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    session = _FakeSession([dated, _FakeResponse(200, {"articles": [ARTICLE], "next_page": None})])
    with mock.patch("time.sleep") as sleep:
        assert len(help_center.fetch_published_articles("p1", session)) == 1
    sleep.assert_called_once_with(5)


def test_sync_defers_uploads_after_deadline():
    documents = InMemoryDocuments(_stored("changed", "old"))
    plan = Store(documents).sync([_article("changed", "new"), _article("fresh", "1")], deadline=0)
    assert plan.deferred == ["fresh", "changed"] and not (plan.added or plan.updated)
    assert documents.calls == [] and list(documents.documents) == ["changed@old"]


def test_cleanup_skips_code_blocks():
    body = "<pre><code>a\xa0b\n###\n</code></pre><img alt='My Screen (1).PNG' src='x'>"
    article = {"title": "T", "html_url": "u", "body": body}
    markdown = help_center.article_to_markdown(article)
    assert "a\xa0b\n###" in markdown and "Screen" not in markdown


def test_fetch_retries_rate_limit_and_follows_pages():
    page1 = {"articles": [ARTICLE], "next_page": "p2"}
    page2 = {"articles": [dict(ARTICLE, draft=True)], "next_page": None}
    session = _FakeSession([_FakeResponse(429), _FakeResponse(200, page1), _FakeResponse(200, page2)])
    assert [a["title"] for a in help_center.fetch_published_articles("p1", session)] == ["Add a YouTube video"]


def test_fetch_fails_instead_of_returning_partial_list():
    failures = [_FakeResponse(500)] * help_center.MAX_RETRIES
    session = _FakeSession([_FakeResponse(200, {"articles": [ARTICLE], "next_page": "p2"}), *failures])
    with pytest.raises(RuntimeError):
        help_center.fetch_published_articles("p1", session)


@pytest.mark.skipif(not (os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY")), reason="no Gemini key")
def test_live_store_is_reachable():
    from file_search_store import open_gemini_documents

    assert open_gemini_documents().store_name.startswith("fileSearchStores/")
