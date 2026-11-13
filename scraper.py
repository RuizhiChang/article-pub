"""Scrape article lists and full texts from Cambridge Core journal issue pages."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://www.cambridge.org"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
# Front/back matter entries are not articles.
SKIP_SLUG = re.compile(r"cover-and-(front|back)-matter")

_session = requests.Session()
_session.headers.update(HEADERS)


@dataclass
class ArticleRef:
    id: str
    title: str
    url: str
    free: bool = False  # open to read without login ("entitled" on the issue page)


@dataclass
class Article:
    id: str
    url: str
    title: str
    authors: list[str]
    doi: str = ""
    date: str = ""
    volume: str = ""
    issue: str = ""
    keywords: str = ""
    abstract: str = ""
    pdf_url: str = ""
    journal: str = ""
    journal_slug: str = ""
    body_md: str = ""  # full text as Markdown; empty if not openly available
    extra: dict = field(default_factory=dict)


def fetch(url: str, retries: int = 3, delay: float = 2.0) -> str:
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))
    raise RuntimeError("unreachable")


LATEST = "https://www.cambridge.org/core/journals/{journal}/latest-issue"


def list_issue(issue_url: str) -> tuple[str, list[ArticleRef]]:
    """Return ("vol22-issue63", articles) for an issue page, articles in page order."""
    html = fetch(issue_url)
    m = re.search(r"Volume (\d+)\s*-\s*Issue (\d+)", html)
    label = f"vol{m.group(1)}-issue{m.group(2)}" if m else issue_url.rstrip("/").rsplit("/", 1)[-1][:8]
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, ArticleRef] = {}
    for a in soup.select('a[href*="/article/"]'):
        href = a.get("href", "").split("#")[0].split("?")[0]
        m = re.search(r"/core/journals/[^/]+/article/([^/]+)/([0-9A-F]{32})$", href)
        if not m or SKIP_SLUG.search(m.group(1)):
            continue
        art_id = m.group(2)
        title = a.get_text(" ", strip=True)
        if art_id in seen:
            # Prefer the longest anchor text (title link vs. "Access" buttons).
            if len(title) > len(seen[art_id].title):
                seen[art_id].title = title
            continue
        seen[art_id] = ArticleRef(id=art_id, title=title, url=urljoin(BASE, href), free=_is_free(a))
    return label, list(seen.values())


def _is_free(link: Tag) -> bool:
    """Free articles show an "entitled" access status in their listing; paywalled ones "get"."""
    block = link.find_parent("div", class_="product-listing-with-inputs-content")
    status = block.select_one("ul.access-modal li.status") if block else None
    return bool(status and "entitled" in status.get("class", []))


def _meta(soup: BeautifulSoup, name: str) -> list[str]:
    return [m.get("content", "").strip() for m in soup.find_all("meta", attrs={"name": name})]


def _inline(el: Tag) -> str:
    """Text of an element with <em>/<i> as *italic* and <strong>/<b> as **bold**."""
    parts = []
    for node in el.descendants:
        if isinstance(node, str):
            parent = node.parent
            if parent.name in ("sup",) and parent.find_parent("a"):
                continue  # footnote markers
            txt = str(node)
            if parent.name in ("em", "i") and txt.strip():
                txt = f"*{txt.strip()}* "
            elif parent.name in ("strong", "b") and txt.strip():
                txt = f"**{txt.strip()}** "
            parts.append(txt)
    return re.sub(r"\s+", " ", "".join(parts)).replace(" .", ".").replace(" ,", ",").strip()


def _body_markdown(soup: BeautifulSoup) -> str:
    box = soup.select_one("div.content-box")
    if box is None:
        return ""
    # Drop abstract (stored separately), reference lists and figure chrome.
    for sel in (".abstract", ".article-abstract", ".references", ".ref-list", ".circle-list", "script", "style"):
        for el in box.select(sel):
            el.decompose()
    lines: list[str] = []
    for el in box.find_all(["h2", "h3", "h4", "p", "li", "blockquote"]):
        if el.name == "p" and el.find_parent(["li", "blockquote"]):
            continue  # rendered via its li/blockquote
        text = _inline(el)
        if not text:
            continue
        if el.name in ("h2", "h3", "h4"):
            lines.append("#" * (int(el.name[1]) ) + " " + text)
        elif el.name == "blockquote":
            lines.append("> " + text)
        elif el.name == "li":
            lines.append(text if re.match(r"^\d+\.", text) else "- " + text)
        else:
            lines.append(text)
    return "\n\n".join(lines)


def get_article(url: str) -> Article:
    soup = BeautifulSoup(fetch(url), "html.parser")
    one = lambda n: (_meta(soup, n) or [""])[0]  # noqa: E731
    art_id = url.rstrip("/").rsplit("/", 1)[-1]
    # Full text is only rendered (div.body) when the article is free to read; the
    # citation_fulltext_world_readable meta tag is missing on some free articles.
    open_access = bool(soup.select_one("div.content-box div.body")) or bool(
        soup.find("meta", attrs={"name": "citation_fulltext_world_readable"}))
    return Article(
        id=art_id,
        url=url,
        title=one("citation_title"),
        journal=one("citation_journal_title"),
        journal_slug=(re.search(r"/core/journals/([^/]+)/", url) or [None, ""])[1],
        authors=_meta(soup, "citation_author"),
        doi=one("citation_doi"),
        date=one("citation_online_date") or one("citation_publication_date"),
        volume=one("citation_volume"),
        issue=one("citation_issue"),
        keywords=one("citation_keywords"),
        abstract=BeautifulSoup(one("citation_abstract"), "html.parser").get_text(" ", strip=True),
        pdf_url=one("citation_pdf_url"),
        body_md=_body_markdown(soup) if open_access else "",
    )
