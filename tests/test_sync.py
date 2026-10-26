import os

import pytest

import help_center
from sync_plan import build_sync_plan, estimate_chunk_count

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
    ),
}


def test_markdown_keeps_structure_and_drops_noise():
    markdown = help_center.article_to_markdown(ARTICLE)
    assert markdown.startswith("# Add a YouTube video\n\nArticle URL: https://support.optisigns.com/hc/en-us/articles/123")
    assert "## Steps" in markdown
    assert "[setup](#setup)" in markdown
    assert "| Key | Value |" in markdown
    assert "docker run app" in markdown and "```" in markdown
    assert "[Embedded content](https://www.youtube.com/embed/x)" in markdown
    assert "alert(1)" not in markdown


def test_slug_is_last_url_segment():
    assert help_center.article_slug(ARTICLE) == "123-add-a-youtube-video"


def test_sync_plan_puts_each_article_in_one_bucket():
    plan = build_sync_plan({"a": "1", "b": "2", "c": "3"}, {"b": "2", "c": "old", "gone": "9"})
    assert (plan.added, plan.updated, plan.skipped, plan.deleted) == (["a"], ["c"], ["b"], ["gone"])


def test_sync_plan_on_empty_store_adds_everything():
    plan = build_sync_plan({"a": "1"}, {})
    assert plan.added == ["a"] and not plan.deleted


def test_chunk_estimate_matches_worked_examples():
    assert estimate_chunk_count("x" * 350, 512, 64) == 1          # 100 tokens
    assert estimate_chunk_count("x" * 3500, 512, 64) == 3         # 1000 tokens: (1000-64)/448 = 2.09, ceil 3


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


def test_fetch_retries_rate_limit_and_follows_pages():
    page1 = {"articles": [ARTICLE], "next_page": "p2"}
    page2 = {"articles": [dict(ARTICLE, draft=True)], "next_page": None}
    session = _FakeSession([_FakeResponse(429), _FakeResponse(200, page1), _FakeResponse(200, page2)])
    assert [a["title"] for a in help_center.fetch_published_articles("p1", session)] == ["Add a YouTube video"]


def test_fetch_fails_instead_of_returning_partial_list():
    session = _FakeSession([_FakeResponse(200, {"articles": [ARTICLE], "next_page": "p2"}), _FakeResponse(500)])
    with pytest.raises(RuntimeError):
        help_center.fetch_published_articles("p1", session)


@pytest.mark.skipif(not (os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY")), reason="no Gemini key")
def test_live_store_is_reachable():
    from google import genai

    import file_search_store

    client = genai.Client(api_key=os.environ.get("API_KEY") or os.environ.get("GEMINI_API_KEY"))
    assert file_search_store.get_or_create_store(client).startswith("fileSearchStores/")
