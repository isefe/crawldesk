from __future__ import annotations

import asyncio
import gzip
import zlib
from urllib.request import Request, urlopen

from webcrawler.config import AppConfig
from webcrawler.models import CrawlResult, CrawlTask
from webcrawler.utils.url import extract_links_and_title


class HttpFetcher:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def fetch(self, task: CrawlTask) -> CrawlResult:
        return await asyncio.to_thread(self._fetch_sync, task)

    def _fetch_sync(self, task: CrawlTask) -> CrawlResult:
        request = Request(
            task.url,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept-Encoding": "gzip, deflate, identity",
            },
        )
        try:
            with urlopen(request, timeout=self.config.request_timeout_seconds) as resp:
                status_code = int(resp.status)
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    return CrawlResult(
                        url=task.url,
                        depth=task.depth,
                        status_code=status_code,
                        content="",
                        content_type=content_type,
                        title=task.url,
                        links=[],
                        error="unsupported-content-type",
                    )

                data = resp.read()
                content_encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in content_encoding:
                    data = gzip.decompress(data)
                elif "deflate" in content_encoding:
                    data = zlib.decompress(data)

                charset = resp.headers.get_content_charset() or "utf-8"
                text = data.decode(charset, errors="replace")
                links, title = extract_links_and_title(task.url, text)
                return CrawlResult(
                    url=task.url,
                    depth=task.depth,
                    status_code=status_code,
                    content=text[: self.config.max_content_chars],
                    content_type=content_type,
                    title=title,
                    links=links,
                )
        except Exception as exc:  # noqa: BLE001
            return CrawlResult(
                url=task.url,
                depth=task.depth,
                status_code=None,
                content="",
                content_type=None,
                title=task.url,
                links=[],
                error=str(exc),
            )
