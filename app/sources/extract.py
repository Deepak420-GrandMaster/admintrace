"""Turning a fetched page into something a model can read.

Nothing here executes anything. It is a tolerant tag reader over the markup:
scripts, styles, navigation, headers, footers and cookie banners are dropped,
and what is left is the text a reader would see.

The metadata matters as much as the text. A claim taken from a live page has
to carry where it came from and when, and "where" means the page's own
canonical URL — not the URL we happened to request, which may have been an
old domain that redirected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

#: Containers whose text is furniture, not content.
_DROP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe",
              "nav", "header", "footer", "form", "select", "button"}

#: Class or id fragments that mark furniture even on a <div>.
_DROP_HINTS = ("cookie", "consent", "banner", "navbar", "nav-", "menu",
               "breadcrumb", "footer", "header", "sidebar", "social",
               "newsletter", "popup", "modal", "skip-link", "search-form")

_BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2",
               "h3", "h4", "h5", "h6", "td", "th", "blockquote", "pre"}

_DATE = re.compile(
    r"(\d{4}-\d{2}-\d{2})"
    r"|(\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|"
    r"septembre|octobre|novembre|décembre)\s+\d{4})",
    re.IGNORECASE,
)


@dataclass
class Page:
    url: str = ""
    canonical_url: str = ""
    title: str = ""
    text: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    #: Absolute URL -> the words that pointed at it. A menu entry reading
    #: "Admissions" is the strongest signal a site gives about what a page is,
    #: and it is lost if only the href is kept.
    anchors: dict[str, str] = field(default_factory=dict)
    language: str = ""
    updated: str = ""
    description: str = ""

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def is_usable(self) -> bool:
        """Enough real text to be worth citing.

        Guards the activation path: an error page, a consent wall or a
        JavaScript shell parses fine and says nothing, and must never
        replace a good stored version.
        """
        return self.word_count >= 40 and bool(self.title)


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.canonical = ""
        self.language = ""
        self.description = ""
        self.updated = ""
        self.headings: list[str] = []
        self.links: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._chunks: list[str] = []
        self._drop_depth = 0
        self._in_title = False
        self._heading: str | None = None
        self._anchor: list[str] | None = None
        self._anchor_href = ""

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _attr(attrs, name):
        for key, value in attrs:
            if key.lower() == name:
                return value or ""
        return ""

    def _is_furniture(self, tag: str, attrs) -> bool:
        if tag in _DROP_TAGS:
            return True
        marker = f"{self._attr(attrs, 'class')} {self._attr(attrs, 'id')} " \
                 f"{self._attr(attrs, 'role')}".lower()
        return any(hint in marker for hint in _DROP_HINTS)

    # -- parsing ----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._drop_depth:
            if tag == "a":
                href = self._attr(attrs, "href")
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    self._anchor_href = href
                    self._anchor = []
            if tag not in ("br", "img", "meta", "link", "input"):
                self._drop_depth += 1
            return
        if self._is_furniture(tag, attrs):
            self._drop_depth = 1
            return

        if tag == "html":
            self.language = self._attr(attrs, "lang")[:5]
        elif tag == "title":
            self._in_title = True
        elif tag == "link" and self._attr(attrs, "rel").lower() == "canonical":
            self.canonical = self._attr(attrs, "href")
        elif tag == "meta":
            name = (self._attr(attrs, "name") or self._attr(attrs, "property")).lower()
            content = self._attr(attrs, "content")
            if name == "description" and not self.description:
                self.description = " ".join(content.split())[:400]
            elif name in ("article:modified_time", "og:updated_time",
                          "last-modified", "dcterms.modified") and not self.updated:
                self.updated = content[:40]
            elif name == "og:url" and not self.canonical:
                self.canonical = content
        elif tag == "a":
            href = self._attr(attrs, "href")
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self.links.append(href)
                self._anchor_href = href
                self._anchor = []
        elif tag in ("h1", "h2", "h3"):
            self._heading = ""

        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            words = " ".join("".join(self._anchor).split())[:120]
            if words and self._anchor_href:
                self.anchors.append((self._anchor_href, words))
                if self._anchor_href not in self.links:
                    self.links.append(self._anchor_href)
            self._anchor = None
            self._anchor_href = ""
        if self._drop_depth:
            self._drop_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            text = " ".join(self._heading.split())
            if text:
                self.headings.append(text)
            self._heading = None
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor.append(data)
        if self._drop_depth:
            return
        if self._in_title:
            self.title += data
            return
        if self._heading is not None:
            self._heading += data
        self._chunks.append(data)

    @property
    def text(self) -> str:
        raw = unescape("".join(self._chunks))
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        kept, seen = [], set()
        for line in lines:
            # Single words and repeated lines are menus and chrome.
            if len(line) < 3 or (len(line.split()) < 3 and line in seen):
                continue
            if line in seen:
                continue
            seen.add(line)
            kept.append(line)
        return "\n".join(kept).strip()


def extract(html: str, url: str = "") -> Page:
    reader = _Reader()
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 - malformed markup is normal on the web
        pass

    canonical = reader.canonical or url
    if canonical and url and not canonical.startswith("http"):
        canonical = urljoin(url, canonical)

    text = reader.text
    updated = reader.updated
    if not updated:
        found = _DATE.search(text[:4000])
        if found:
            updated = found.group(0)

    base = url or canonical
    links = []
    anchors: dict[str, str] = {}
    for href in reader.links[:600]:
        absolute = urljoin(base, href) if base else href
        if absolute.startswith("https://"):
            links.append(absolute)
    for href, words in reader.anchors[:600]:
        absolute = urljoin(base, href) if base else href
        if absolute.startswith("https://") and absolute not in anchors:
            anchors[absolute] = words

    return Page(
        url=url,
        canonical_url=canonical,
        title=" ".join(unescape(reader.title).split())[:300],
        text=text,
        headings=reader.headings[:60],
        links=list(dict.fromkeys(links))[:300],
        anchors=anchors,
        language=reader.language,
        updated=updated,
        description=reader.description,
    )


def same_site(url: str, other: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == (urlsplit(other).hostname or "").lower()
