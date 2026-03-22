from __future__ import annotations

import html as html_lib
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


def normalize_url(raw_url: str) -> str | None:
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:  # noqa: BLE001
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    params = parse_qsl(parsed.query, keep_blank_values=True)
    params.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(params, doseq=True)

    cleaned = (scheme, netloc, path, "", query, "")
    return urlunparse(cleaned)


class _LinkTitleParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.in_title = False
        self.title_chunks: list[str] = []

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_chunks).split())[:200]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                absolute = urljoin(self.base_url, href)
                normalized = normalize_url(absolute)
                if normalized is not None:
                    self.links.append(normalized)
        elif tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_chunks.append(data)


def extract_links_and_title(base_url: str, html: str) -> tuple[list[str], str]:
    parser = _LinkTitleParser(base_url=base_url)
    parser.feed(html)
    return list(dict.fromkeys(parser.links)), parser.title or base_url


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def as_text(self) -> str:
        merged = " ".join(self._chunks)
        normalized = " ".join(merged.split())
        return html_lib.unescape(normalized)


def html_to_text(raw_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(raw_html)
    return parser.as_text()
