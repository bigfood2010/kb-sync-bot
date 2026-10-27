import hashlib
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

ARTICLES_URL = "https://support.optisigns.com/api/v2/help_center/en-us/articles.json?per_page=100"
MAX_RETRIES = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT_SECONDS = 60
IMAGE_FILE_NAME = re.compile(r".*\.(png|jpe?g|gif|webp|svg)", re.IGNORECASE)
CODE_FENCE = re.compile(r"(```.*?```)", re.DOTALL)
EMPTY_HEADING = re.compile(r"^#{1,6}[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Article:
    slug: str
    markdown: str
    content_hash: str
    url: str


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
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(5)
            continue
        if response.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
            break
        time.sleep(_retry_wait_seconds(response))
    response.raise_for_status()
    return response.json()


def _retry_wait_seconds(response):
    # Retry-After may also be an HTTP date. Those fall back to the default wait.
    value = response.headers.get("Retry-After", "")
    return min(int(value), MAX_RETRY_WAIT_SECONDS) if value.isdigit() else 5


class _HelpCenterConverter(MarkdownConverter):
    def convert_img(self, element, text, parent_tags):
        alt = element.get("alt", "").strip()
        if not alt or IMAGE_FILE_NAME.fullmatch(alt):  # screenshots without a description add no searchable text
            return ""
        if element.get("src", "").startswith("data:"):
            return alt
        return super().convert_img(element, text, parent_tags)

    def convert_iframe(self, element, text, parent_tags):
        source = element.get("src", "")
        return f"\n\n[Embedded content]({source})\n\n" if source else ""


def article_to_markdown(article):
    soup = BeautifulSoup(article["body"] or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    converter = _HelpCenterConverter(heading_style="ATX", bullets="-", table_infer_header=True)
    parts = CODE_FENCE.split(converter.convert_soup(soup))
    parts[::2] = [EMPTY_HEADING.sub("", part.replace("\xa0", " ")) for part in parts[::2]]  # odd parts are code
    body = re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
    return f"# {article['title']}\n\nArticle URL: {article['html_url']}\n\n{body}\n"


def article_slug(article):
    return article["html_url"].rstrip("/").rsplit("/", 1)[-1]


def content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def to_article(raw_article):
    markdown = article_to_markdown(raw_article)
    return Article(article_slug(raw_article), markdown, content_hash(markdown), raw_article["html_url"])
