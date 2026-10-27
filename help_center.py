import hashlib
import re
import time

import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

ARTICLES_URL = "https://support.optisigns.com/api/v2/help_center/en-us/articles.json?per_page=100"
MAX_RETRIES = 5


def fetch_published_articles(url=ARTICLES_URL, session=None):
    # Raises on any failed page: a partial list would make missing articles look deleted.
    session = session or requests.Session()
    session.headers["User-Agent"] = "kb-sync-bot/1.0"
    articles = []
    while url:
        page = _get_page(session, url)
        articles += [a for a in page["articles"] if not a["draft"] and not a["outdated"]]
        url = page.get("next_page")
    return articles


def _get_page(session, url):
    for _ in range(MAX_RETRIES):
        response = session.get(url, timeout=30)
        if response.status_code != 429:
            break
        time.sleep(int(response.headers.get("Retry-After", 5)))
    response.raise_for_status()
    return response.json()


class _HelpCenterConverter(MarkdownConverter):
    def convert_img(self, element, text, parent_tags):
        if element.get("src", "").startswith("data:"):  # inline base64 carries no text
            return element.get("alt", "")
        return super().convert_img(element, text, parent_tags)

    def convert_iframe(self, element, text, parent_tags):
        source = element.get("src", "")
        return f"\n\n[Embedded content]({source})\n\n" if source else ""


def article_to_markdown(article):
    soup = BeautifulSoup(article["body"] or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    converter = _HelpCenterConverter(heading_style="ATX", bullets="-", table_infer_header=True)
    body = re.sub(r"\n{3,}", "\n\n", converter.convert_soup(soup)).strip()
    return f"# {article['title']}\n\nArticle URL: {article['html_url']}\n\n{body}\n"


def article_slug(article):
    return article["html_url"].rstrip("/").rsplit("/", 1)[-1]


def content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()
