from __future__ import annotations

import html
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from webcrawler.config import AppConfig
from webcrawler.web.manager import CrawlerManager


def _as_int(raw: str | None, default: int, minimum: int = 1) -> int:
    try:
        value = int(raw or "")
    except ValueError:
        value = default
    return max(minimum, value)


def _as_float(raw: str | None, default: float, minimum: float = 0.1) -> float:
    try:
        value = float(raw or "")
    except ValueError:
        value = default
    return max(minimum, value)


def _favicon_data_uri() -> str:
    return "data:image/svg+xml," + (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<text y='50' x='5' font-size='48'>🕷️</text></svg>"
    )


def _format_dt(raw: str | None) -> str:
    if not raw:
        return "-"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:  # noqa: BLE001
        return raw


def _human_event_name(event_type: str) -> str:
    mapping = {
        "queue_enqueue": "Added to queue",
        "queue_restored": "Restored from checkpoint",
        "queue_drop_backpressure": "Dropped due to backpressure",
        "visit_start": "Visit started",
        "visit_done": "Visit completed",
        "visit_error": "Visit failed",
    }
    return mapping.get(event_type, event_type)


class CrawlerWebHandler(BaseHTTPRequestHandler):
    manager: CrawlerManager
    default_config: AppConfig

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/search/random":
            word = self.manager.random_word()
            if word is None:
                self._send_json(404, {"error": "no-indexed-content"})
                return
            self._send_json(200, {"word": word})
            return
        if parsed.path == "/crawler/list":
            crawlers = self.manager.list_crawlers()
            self._send_json(200, {"crawlers": crawlers, "count": len(crawlers)})
            return
        if parsed.path == "/crawler/stats":
            self._send_json(200, self.manager.get_overview())
            return
        if parsed.path.startswith("/crawler/status/"):
            crawler_id = parsed.path.removeprefix("/crawler/status/").strip()
            status = self.manager.get_crawler_status(crawler_id) if crawler_id else None
            if status is None:
                self._send_json(404, {"error": "crawler-not-found"})
                return
            self._send_json(200, status)
            return
        if parsed.path == "/api/overview":
            self._send_json(200, self.manager.get_overview())
            return
        if parsed.path == "/api/status":
            crawler_id = query.get("id", [""])[0].strip()
            status = self.manager.get_crawler_status(crawler_id) if crawler_id else None
            if status is None:
                self._send_json(404, {"error": "crawler-not-found"})
                return
            self._send_json(200, status)
            return
        if parsed.path in {"/", "/crawler/new"}:
            self._render_new_crawler_page(flash=query.get("flash", [""])[0])
            return
        if parsed.path == "/search":
            if self._is_api_search_request(query):
                self._handle_search_api(query)
                return
            self._render_search_page(query_params=query)
            return
        if parsed.path == "/status":
            self._render_status_page(flash=query.get("flash", [""])[0])
            return
        if parsed.path.startswith("/status/"):
            crawler_id = parsed.path.removeprefix("/status/").strip()
            self._render_crawler_status_page(crawler_id=crawler_id, flash=query.get("flash", [""])[0])
            return
        self._send_html(404, self._layout("Not Found", "<h2>Not Found</h2>", active_nav=""))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/crawler/pause":
            form = self._read_form()
            crawler_id = (form.get("crawler_id", [""])[0] or "").strip()
            ok = self.manager.pause_crawler(crawler_id) if crawler_id else False
            self.send_response(303)
            self.send_header(
                "Location",
                f"/status/{crawler_id}?flash=" + ("paused" if ok else "pause_failed"),
            )
            self.end_headers()
            return
        if parsed.path == "/crawler/resume":
            form = self._read_form()
            crawler_id = (form.get("crawler_id", [""])[0] or "").strip()
            ok = self.manager.resume_crawler(crawler_id) if crawler_id else False
            self.send_response(303)
            self.send_header(
                "Location",
                f"/status/{crawler_id}?flash=" + ("resumed" if ok else "resume_failed"),
            )
            self.end_headers()
            return
        if parsed.path == "/crawler/stop":
            form = self._read_form()
            crawler_id = (form.get("crawler_id", [""])[0] or "").strip()
            ok = self.manager.stop_crawler(crawler_id) if crawler_id else False
            self.send_response(303)
            self.send_header(
                "Location",
                f"/status/{crawler_id}?flash=" + ("stopped" if ok else "stop_failed"),
            )
            self.end_headers()
            return
        if parsed.path == "/crawler/create":
            payload = self._read_json()
            crawler_id = self.manager.create_crawler(
                origin=str(payload.get("origin") or self.default_config.origin_url).strip(),
                max_depth=_as_int(str(payload.get("max_depth") or ""), self.default_config.max_depth, minimum=0),
                max_pages=_as_int(str(payload.get("max_urls_to_visit") or ""), self.default_config.max_pages, minimum=1),
                requests_per_second=_as_float(
                    str(payload.get("hit_rate") or ""),
                    self.default_config.requests_per_second,
                    minimum=0.1,
                ),
                queue_capacity=_as_int(
                    str(payload.get("max_queue_capacity") or ""),
                    self.default_config.queue_capacity,
                    minimum=1,
                ),
            )
            self._send_json(201, {"crawler_id": crawler_id, "state": "running"})
            return
        if parsed.path == "/crawler/clear":
            ok = self.manager.clear_all_data()
            self._send_json(200, {"ok": ok})
            return
        if parsed.path.startswith("/crawler/pause/"):
            crawler_id = parsed.path.removeprefix("/crawler/pause/").strip()
            ok = self.manager.pause_crawler(crawler_id) if crawler_id else False
            self._send_json(200 if ok else 404, {"ok": ok, "crawler_id": crawler_id})
            return
        if parsed.path.startswith("/crawler/resume/"):
            crawler_id = parsed.path.removeprefix("/crawler/resume/").strip()
            ok = self.manager.resume_crawler(crawler_id) if crawler_id else False
            self._send_json(200 if ok else 404, {"ok": ok, "crawler_id": crawler_id})
            return
        if parsed.path.startswith("/crawler/stop/"):
            crawler_id = parsed.path.removeprefix("/crawler/stop/").strip()
            ok = self.manager.stop_crawler(crawler_id) if crawler_id else False
            self._send_json(200 if ok else 404, {"ok": ok, "crawler_id": crawler_id})
            return
        if parsed.path.startswith("/crawler/resume-from-files/"):
            crawler_id = parsed.path.removeprefix("/crawler/resume-from-files/").strip()
            ok = self.manager.resume_from_files(crawler_id) if crawler_id else False
            self._send_json(200 if ok else 404, {"ok": ok, "crawler_id": crawler_id})
            return
        if parsed.path == "/data/clear":
            ok = self.manager.clear_all_data()
            self.send_response(303)
            self.send_header("Location", "/status?flash=" + ("cleared" if ok else "clear_blocked"))
            self.end_headers()
            return
        if parsed.path == "/crawler/delete":
            form = self._read_form()
            crawler_id = (form.get("crawler_id", [""])[0] or "").strip()
            ok = self.manager.delete_crawler(crawler_id) if crawler_id else False
            self.send_response(303)
            self.send_header("Location", "/status?flash=" + ("deleted" if ok else "delete_failed"))
            self.end_headers()
            return
        if parsed.path != "/crawler/new":
            self._send_html(404, self._layout("Not Found", "<h2>Not Found</h2>", active_nav=""))
            return

        form = self._read_form()
        crawler_id = self.manager.create_crawler(
            origin=(form.get("origin", [""])[0] or self.default_config.origin_url).strip(),
            max_depth=_as_int(form.get("max_depth", [""])[0], self.default_config.max_depth, minimum=0),
            max_pages=_as_int(form.get("max_pages", [""])[0], self.default_config.max_pages, minimum=1),
            requests_per_second=_as_float(
                form.get("requests_per_second", [""])[0],
                self.default_config.requests_per_second,
                minimum=0.1,
            ),
            queue_capacity=_as_int(
                form.get("queue_capacity", [""])[0],
                self.default_config.queue_capacity,
                minimum=1,
            ),
        )
        self.send_response(303)
        self.send_header("Location", f"/status/{crawler_id}?flash=started")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _render_new_crawler_page(self, flash: str = "") -> None:
        cfg = self.default_config
        overview = self.manager.get_overview()
        recent_jobs = self.manager.list_crawlers()[:10]
        body = f"""
        {self._flash_html(flash)}
        {self._overview_cards_html(overview)}
        <section class="panel">
          <div class="panel-title-row">
            <h2>New Crawler</h2>
          </div>
          <p class="muted">Start a new crawl job.</p>
          <form method="post" action="/crawler/new" class="form-grid">
            <label>Origin URL</label>
            <input name="origin" value="" placeholder="{html.escape(cfg.origin_url)}" />
            <label>Max Depth</label>
            <input name="max_depth" type="number" min="0" value="{cfg.max_depth}" />
            <label>Max Pages</label>
            <input name="max_pages" type="number" min="1" value="{cfg.max_pages}" />
            <label>Requests / Second</label>
            <input name="requests_per_second" type="number" min="0.1" step="0.1" value="{cfg.requests_per_second}" />
            <label>Queue Capacity</label>
            <input name="queue_capacity" type="number" min="1" value="{cfg.queue_capacity}" />
            <div class="info-row">Estimated load: higher Max Pages and Depth increase queue/memory usage.</div>
            <button type="submit">Start Crawler</button>
          </form>
        </section>
        {self._recent_crawlers_panel(recent_jobs)}
        """
        self._send_html(200, self._layout("New Crawler", body, active_nav="new"))

    def _render_search_page(self, query_params: dict[str, list[str]]) -> None:
        q = query_params.get("q", [""])[0]
        domain = query_params.get("domain", [""])[0]
        hits = (
            self.manager.search_with_filters(
                query=q,
                limit=20,
                domain=domain or None,
            )
            if q.strip()
            else []
        )
        rows = []
        for hit in hits:
            rows.append(
                f"""
                <article class="search-hit">
                  <div class="hit-url">{html.escape(hit["url"])}</div>
                  <a class="hit-title" href="{html.escape(hit["url"])}" target="_blank" rel="noreferrer">{html.escape(hit["title"])}</a>
                  <div class="hit-snippet">{html.escape(hit["snippet"])}</div>
                </article>
                """
            )
        results_html = "".join(rows) if rows else ("<p class='muted'>No results.</p>" if q.strip() else "")
        search_shell_class = "search-shell search-shell-home" if not q.strip() else "search-shell"
        body = f"""
        <section class="{search_shell_class}">
          <div class="search-brand">Crawl<span>Search</span></div>
          <form method="get" action="/search" class="search-google-bar">
            <input name="q" value="{html.escape(q)}" placeholder="Search the index..." autocomplete="off" />
            <input type="hidden" name="domain" value="{html.escape(domain)}" />
            <button type="submit">Search</button>
          </form>
          <form method="get" action="/search" class="search-tools">
            <input type="hidden" name="q" value="{html.escape(q)}" />
            <input name="domain" value="{html.escape(domain)}" placeholder="Site filter (optional), e.g. wikipedia.org" />
            <button type="submit">Apply</button>
          </form>
          {("<div class='search-count muted'>Results: " + str(len(hits)) + "</div>") if q.strip() else ""}
        </section>
        <section class="search-results">{results_html}</section>
        """
        self._send_html(200, self._layout("Search", body, active_nav="search"))

    def _render_status_page(self, flash: str = "") -> None:
        overview = self.manager.get_overview()
        jobs = self.manager.list_crawlers()
        cards = []
        for job in jobs:
            crawler_id = str(job["crawler_id"])
            state = str(job["state"])
            cards.append(
                f"""
                <article class="crawler-card">
                  <div class="crawler-card-head">
                    <a class="crawler-id-link" href="/status/{html.escape(crawler_id)}">{html.escape(crawler_id)}</a>
                    <span class="state state-{html.escape(state)}" data-crawler-id="{html.escape(crawler_id)}">{html.escape(state)}</span>
                  </div>
                  <div class="crawler-origin">{html.escape(str(job["origin"]))}</div>
                  <div class="crawler-meta">Created: {html.escape(_format_dt(job.get("created_at")))}</div>
                  <div class="table-actions">{self._crawler_control_buttons_html(crawler_id, state)}</div>
                </article>
                """
            )
        list_html = "".join(cards) or "<p class='muted'>No crawlers yet.</p>"
        body = f"""
        {self._flash_html(flash)}
        {self._overview_cards_html(overview)}
        <section class="panel">
          <div class="panel-title-row"><h2>All Crawlers</h2><div class="muted">Last update: <span id="last-update">{html.escape(_format_dt(overview['updated_at']))}</span></div></div>
          <p class="muted">Each crawler has its own detail page. Click any crawler ID to open it.</p>
          <form method="post" action="/data/clear" style="margin-bottom:14px;">
            <button type="submit" class="danger-btn">Clear All Data</button>
          </form>
          <div class="crawler-grid">{list_html}</div>
        </section>
        """
        self._send_html(200, self._layout("Status", body, active_nav="status"))

    def _render_crawler_status_page(self, *, crawler_id: str, flash: str = "") -> None:
        overview = self.manager.get_overview()
        status = self.manager.get_crawler_status(crawler_id)
        if status is None:
            body = f"""
            {self._flash_html(flash)}
            <section class="panel"><p>Crawler not found: {html.escape(crawler_id)}</p></section>
            """
            self._send_html(404, self._layout("Crawler Not Found", body, active_nav="status"))
            return
        body = f"""
        {self._flash_html(flash)}
        {self._overview_cards_html(overview)}
        {self._crawler_detail_html(status)}
        """
        self._send_html(200, self._layout("Crawler Status", body, active_nav="status", crawler_id=crawler_id))

    def _state_option(self, value: str, current: str) -> str:
        selected = "selected" if value == current else ""
        return f"<option {selected} value='{value}'>{value.title()}</option>"

    def _read_form(self) -> dict[str, list[str]]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
        return parse_qs(raw, keep_blank_values=True)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def _is_api_search_request(self, query_params: dict[str, list[str]]) -> bool:
        return any(key in query_params for key in ("query", "pageLimit", "pageOffset", "sortBy"))

    def _handle_search_api(self, query_params: dict[str, list[str]]) -> None:
        query = query_params.get("query", [""])[0].strip()
        page_limit = _as_int(query_params.get("pageLimit", [""])[0], 10, minimum=1)
        page_offset = _as_int(query_params.get("pageOffset", [""])[0], 0, minimum=0)
        sort_by = query_params.get("sortBy", ["relevance"])[0].strip().lower() or "relevance"
        if sort_by not in {"relevance", "recent"}:
            sort_by = "relevance"

        if not query:
            self._send_json(
                400,
                {
                    "error": "query-required",
                    "message": "Provide query parameter, e.g. /search?query=python&pageLimit=10&pageOffset=0",
                },
            )
            return

        hits = self.manager.search_with_filters(
            query=query,
            limit=page_limit,
            offset=page_offset,
            sort_by=sort_by,
        )
        self._send_json(
            200,
            {
                "query": query,
                "sortBy": sort_by,
                "pageLimit": page_limit,
                "pageOffset": page_offset,
                "results": hits,
                "count": len(hits),
            },
        )

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code: int, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _overview_cards_html(self, overview: dict) -> str:
        cards = [
            ("URLs Visited", overview.get("total_urls_visited", 0)),
            ("Active Crawlers", overview.get("active_crawlers", 0)),
            ("Total Created", overview.get("total_created", 0)),
        ]
        return "<section class='kpis'>" + "".join(
            f"<div class='kpi'><div class='kpi-title'>{html.escape(label)}</div><div class='kpi-value' data-kpi='{html.escape(label)}'>{value}</div></div>"
            for label, value in cards
        ) + "</section>"

    def _recent_crawlers_panel(self, jobs: list[dict[str, Any]]) -> str:
        if not jobs:
            return (
                "<section class='panel'><h3>Recently Created Crawlers</h3>"
                "<p class='muted'>No crawlers yet. Start your first crawler from the form.</p></section>"
            )
        rows = []
        for item in jobs:
            crawler_id = str(item.get("crawler_id", ""))
            state = str(item.get("state", ""))
            rows.append(
                "<tr>"
                f"<td><a href='/status/{html.escape(crawler_id)}'>{html.escape(crawler_id)}</a></td>"
                f"<td><span class='state state-{html.escape(state)}' data-crawler-id='{html.escape(crawler_id)}'>{html.escape(state)}</span></td>"
                f"<td class='trace-url'>{html.escape(str(item.get('origin', '')))}</td>"
                f"<td>{_format_dt(item.get('created_at'))}</td>"
                f"<td><div class='table-actions'>{self._crawler_control_buttons_html(crawler_id, state)}</div></td>"
                "</tr>"
            )
        return (
            "<section class='panel'><h3>Recently Created Crawlers</h3>"
            "<table><thead><tr><th>ID</th><th>State</th><th>Origin</th><th>Created</th><th>Actions</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    def _crawler_detail_html(self, status: dict[str, Any]) -> str:
        crawler_id = str(status.get("crawler_id") or "")
        state = str(status.get("state") or "")
        controls_html = self._crawler_control_buttons_html(crawler_id, state)
        summary_items = [
            ("State", str(status.get("state", "-")), "state"),
            ("Visited", str(status.get("pages_crawled", 0)), "pages_crawled"),
            ("Indexed", str(status.get("pages_indexed", 0)), "pages_indexed"),
            ("Failed", str(status.get("pages_failed", 0)), "pages_failed"),
            ("Queue", str(status.get("queue_size", 0)), "queue_size"),
            ("Current URL", str(status.get("current_url") or "-"), "current_url"),
        ]
        summary_cards = "".join(
            "<div class='kpi'>"
            f"<div class='kpi-title'>{html.escape(label)}</div>"
            f"<div class='kpi-value detail-value detail-{html.escape(key)}'>{html.escape(value)}</div>"
            "</div>"
            for label, value, key in summary_items
        )
        return f"""
        <section class="panel" id="detail">
          <div class="panel-title-row">
            <h2>Crawler {html.escape(crawler_id)}</h2>
            <div class="detail-actions">
              <a href="/status" class="action-link">Back to all crawlers</a>
              <label class="live-toggle"><input type="checkbox" id="auto-refresh-toggle" checked /> Auto Refresh</label>
              <button type="button" id="refresh-detail-btn" class="action-btn">Refresh Now</button>
            </div>
          </div>
          <div class="detail-controls">{controls_html}</div>
          <div class="timeline">
            <div>Created: <span class="detail-created_at">{html.escape(_format_dt(status.get("created_at")))}</span></div>
            <div>Started: <span class="detail-started_at">{html.escape(_format_dt(status.get("started_at")))}</span></div>
            <div>Finished: <span class="detail-finished_at">{html.escape(_format_dt(status.get("finished_at")))}</span></div>
          </div>
          <div class="kpis detail-kpis">{summary_cards}</div>
        </section>
        <section class="panel">
          <h3>Visited URLs</h3>
          <div class="table-scroll">
            <table>
              <thead><tr><th>#</th><th>URL</th><th>Depth</th><th>Status</th></tr></thead>
              <tbody id="visit-trace-body">{self._visit_rows_html(status.get("visit_trace", []))}</tbody>
            </table>
          </div>
        </section>
        <section class="panel" id="event-log">
          <h3>Activity Log</h3>
          <div class="table-scroll">
            <table>
              <thead><tr><th>#</th><th>Event</th><th>URL</th><th>Depth</th><th>Info</th></tr></thead>
              <tbody id="event-log-body">{self._event_rows_html(status.get("event_log", []))}</tbody>
            </table>
          </div>
        </section>
        """

    def _visit_rows_html(self, visit_trace: list[dict[str, Any]]) -> str:
        rows = []
        for idx, item in enumerate(visit_trace, start=1):
            rows.append(
                f"<tr><td>{idx}</td><td class='trace-url'>{html.escape(str(item.get('url', '')))}</td><td>{item.get('depth', '')}</td><td>{item.get('status_code') or '-'}</td></tr>"
            )
        return "".join(rows) or "<tr><td colspan='4' class='muted'>No visited URLs yet.</td></tr>"

    def _event_rows_html(self, event_log: list[dict[str, Any]]) -> str:
        rows = []
        for item in event_log:
            rows.append(
                "<tr>"
                f"<td>{item.get('event_order', '')}</td>"
                f"<td>{html.escape(_human_event_name(str(item.get('event_type', ''))))}</td>"
                f"<td class='trace-url'>{html.escape(str(item.get('url', '')))}</td>"
                f"<td>{item.get('depth', '')}</td>"
                f"<td>{html.escape(str(item.get('source', item.get('error', ''))))}</td>"
                "</tr>"
            )
        return "".join(rows) or "<tr><td colspan='5' class='muted'>No activity yet.</td></tr>"

    def _flash_html(self, flash: str) -> str:
        text_map = {
            "started": "Started.",
            "paused": "Paused.",
            "resumed": "Resumed.",
            "stopped": "Stopped.",
            "pause_failed": "Pause failed.",
            "resume_failed": "Resume failed.",
            "stop_failed": "Stop failed.",
            "deleted": "Deleted.",
            "delete_failed": "Delete failed.",
            "cleared": "All data cleared.",
            "clear_blocked": "Stop running crawlers first.",
        }
        if flash not in text_map:
            return ""
        return f"<div id='toast' class='toast'>{html.escape(text_map[flash])}</div>"

    def _crawler_control_buttons_html(self, crawler_id: str, state: str) -> str:
        cid = html.escape(crawler_id)
        state_key = html.escape((state or "").lower())
        forms: list[str] = []

        if state == "running":
            forms.append(
                "<form method='post' action='/crawler/pause' class='crawler-control-form' style='display:inline;'>"
                f"<input type='hidden' name='crawler_id' value='{cid}' />"
                "<button type='submit' class='action-btn' data-intent='pause'>Pause</button>"
                "</form>"
            )
            forms.append(
                "<form method='post' action='/crawler/stop' class='crawler-control-form' style='display:inline;'>"
                f"<input type='hidden' name='crawler_id' value='{cid}' />"
                "<button type='submit' class='action-btn danger-btn' data-intent='stop'>Stop</button>"
                "</form>"
            )
        elif state in {"paused", "stopped", "interrupted", "failed"}:
            forms.append(
                "<form method='post' action='/crawler/resume' class='crawler-control-form' style='display:inline;'>"
                f"<input type='hidden' name='crawler_id' value='{cid}' />"
                "<button type='submit' class='action-btn' data-intent='resume'>Resume</button>"
                "</form>"
            )

        forms.append(
            "<form method='post' action='/crawler/delete' style='display:inline;'>"
            f"<input type='hidden' name='crawler_id' value='{cid}' />"
            "<button type='submit' class='action-btn danger-btn'>Delete</button>"
            "</form>"
        )
        return (
            f"<div class='control-set' data-crawler-id='{cid}' data-crawler-state='{state_key}'>"
            + "".join(forms)
            + "</div>"
        )

    def _layout(
        self,
        title: str,
        body: str,
        *,
        active_nav: str,
        crawler_id: str | None = None,
    ) -> str:
        now = datetime.utcnow().isoformat()
        return f"""
        <!doctype html>
        <html lang="en" data-theme="dark">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1" />
          <link rel="icon" href="{_favicon_data_uri()}" />
          <title>{html.escape(title)} · CrawlDesk</title>
          <script>
            (function() {{
              try {{
                var key = "crawl_theme";
                var saved = localStorage.getItem(key);
                var theme = (saved === "light" || saved === "dark") ? saved : "dark";
                document.documentElement.setAttribute("data-theme", theme);
              }} catch (_e) {{
                document.documentElement.setAttribute("data-theme", "dark");
              }}
            }})();
          </script>
          <style>
            :root {{
              --bg: #f4f6fb;
              --panel: #ffffff;
              --text: #101826;
              --muted: #5f6b7d;
              --primary: #1559d6;
              --border: #dde4f0;
              --ok: #0f9d58;
              --danger: #c62828;
              --shadow: 0 10px 24px rgba(27, 39, 67, 0.08);
            }}
            html[data-theme="dark"] {{
              --bg: #0f1522;
              --panel: #161f31;
              --text: #eaf0ff;
              --muted: #9aa8c3;
              --primary: #5d9cff;
              --border: #273752;
              --ok: #37d890;
              --danger: #ff7f7f;
              --shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              font-family: "IBM Plex Sans", "Segoe UI", "Helvetica Neue", sans-serif;
              background: radial-gradient(circle at 15% 10%, rgba(92,130,255,.18), transparent 30%), var(--bg);
              color: var(--text);
            }}
            .topbar {{
              position: sticky;
              top: 0;
              z-index: 20;
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 14px;
              padding: 12px 20px;
              border-bottom: 1px solid var(--border);
              background: color-mix(in srgb, var(--panel) 90%, transparent);
              backdrop-filter: blur(8px);
            }}
            .brand {{
              display: flex;
              align-items: center;
              gap: 10px;
              font-weight: 700;
              font-size: 18px;
            }}
            .brand .logo {{
              width: 34px;
              height: 34px;
              border-radius: 10px;
              display: grid;
              place-items: center;
              background: linear-gradient(135deg, #1147ad, #4b93ff);
              color: #fff;
              box-shadow: var(--shadow);
            }}
            .nav {{
              display: flex;
              gap: 8px;
            }}
            .nav a {{
              text-decoration: none;
              color: var(--text);
              padding: 8px 12px;
              border-radius: 10px;
              border: 1px solid transparent;
              font-weight: 600;
            }}
            .nav a.active {{
              border-color: var(--border);
              background: var(--panel);
              color: var(--primary);
            }}
            .top-actions {{
              display: flex;
              align-items: center;
              gap: 10px;
            }}
            .theme-btn {{
              border: 1px solid var(--border);
              background: var(--panel);
              color: var(--text);
              border-radius: 10px;
              padding: 8px 10px;
              cursor: pointer;
            }}
            main {{
              max-width: 1180px;
              margin: 24px auto 80px;
              padding: 0 16px;
            }}
            .kpis {{
              display: grid;
              grid-template-columns: repeat(4, minmax(0, 1fr));
              gap: 10px;
              margin-bottom: 16px;
            }}
            .kpi {{
              background: var(--panel);
              border: 1px solid var(--border);
              border-radius: 14px;
              padding: 14px;
              box-shadow: var(--shadow);
            }}
            .kpi-title {{
              color: var(--muted);
              font-size: 13px;
            }}
            .kpi-value {{
              font-size: 24px;
              font-weight: 700;
              margin-top: 6px;
            }}
            .panel {{
              background: var(--panel);
              border: 1px solid var(--border);
              border-radius: 16px;
              padding: 16px;
              margin-bottom: 14px;
              box-shadow: var(--shadow);
            }}
            .panel-title-row {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 10px;
            }}
            .muted {{
              color: var(--muted);
            }}
            .form-grid, .search-controls, .status-filters {{
              display: grid;
              gap: 10px;
            }}
            .search-controls {{
              grid-template-columns: 1fr auto;
              align-items: center;
            }}
            .domain-filter-row {{
              margin-top: 10px;
              display: grid;
              grid-template-columns: 1fr auto;
              gap: 10px;
              align-items: center;
            }}
            .status-filters {{
              grid-template-columns: 2fr 1fr 1fr auto;
              align-items: center;
              margin-bottom: 10px;
            }}
            input, select, button {{
              width: 100%;
              font: inherit;
              color: var(--text);
              background: color-mix(in srgb, var(--panel) 88%, #fff 12%);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 10px 12px;
            }}
            button {{
              background: linear-gradient(135deg, #1b4fb3, #2d7dff);
              border: none;
              color: #fff;
              cursor: pointer;
              font-weight: 700;
            }}
            button[disabled] {{
              opacity: 0.58;
              cursor: not-allowed;
            }}
            .action-btn {{
              width: auto;
              padding: 6px 10px;
              margin-left: 0;
              font-size: 12px;
              border-radius: 9px;
            }}
            .action-btn.is-loading {{
              opacity: 0.75;
              pointer-events: none;
            }}
            .action-link {{
              color: var(--primary);
              text-decoration: none;
              font-weight: 600;
              font-size: 13px;
            }}
            .action-link:hover {{
              text-decoration: underline;
            }}
            .danger-btn {{
              background: linear-gradient(135deg, #a21212, #dc3030);
            }}
            .info-row {{
              font-size: 13px;
              color: var(--muted);
            }}
            .search-shell {{
              width: min(780px, 100%);
              margin: 22px auto 18px;
              text-align: center;
            }}
            .search-shell-home {{
              margin-top: 90px;
            }}
            .search-brand {{
              font-size: clamp(38px, 8vw, 64px);
              font-weight: 800;
              letter-spacing: -1.6px;
              margin-bottom: 18px;
            }}
            .search-brand span {{
              color: var(--primary);
            }}
            .search-google-bar {{
              display: grid;
              grid-template-columns: 1fr auto;
              align-items: center;
              gap: 10px;
              padding: 10px;
              border-radius: 999px;
              border: 1px solid var(--border);
              background: var(--panel);
              box-shadow: var(--shadow);
            }}
            .search-google-bar input {{
              border: none;
              background: transparent;
              padding: 12px 14px;
              border-radius: 999px;
            }}
            .search-google-bar input:focus {{
              outline: none;
            }}
            .search-google-bar button {{
              width: auto;
              border-radius: 999px;
              padding: 10px 16px;
            }}
            .search-tools {{
              margin-top: 12px;
              display: grid;
              grid-template-columns: 1fr auto;
              gap: 8px;
              align-items: center;
            }}
            .search-tools button {{
              width: auto;
              padding: 10px 14px;
            }}
            .search-count {{
              margin-top: 10px;
              font-size: 13px;
            }}
            .search-results {{
              width: min(860px, 100%);
              margin: 0 auto;
            }}
            .search-hit {{
              border: 1px solid var(--border);
              border-radius: 14px;
              padding: 12px 14px;
              margin-bottom: 10px;
              background: color-mix(in srgb, var(--panel) 94%, #fff 6%);
            }}
            .hit-title {{
              color: color-mix(in srgb, var(--primary) 80%, #1a0dab 20%);
              text-decoration: none;
              font-size: 21px;
              font-weight: 600;
              line-height: 1.3;
              display: block;
              margin-top: 2px;
            }}
            .hit-title:hover {{
              text-decoration: underline;
            }}
            .hit-url {{
              color: var(--ok);
              font-size: 13px;
              word-break: break-all;
            }}
            .hit-snippet {{
              color: var(--muted);
              margin-top: 7px;
              line-height: 1.5;
            }}
            mark {{
              background: color-mix(in srgb, #ffe66b 70%, transparent);
              border-radius: 4px;
              padding: 0 2px;
            }}
            table {{
              width: 100%;
              border-collapse: collapse;
            }}
            th, td {{
              text-align: left;
              border-bottom: 1px solid var(--border);
              padding: 9px;
              vertical-align: top;
            }}
            .trace-url {{
              word-break: break-all;
            }}
            .table-scroll {{
              max-height: 320px;
              overflow: auto;
            }}
            .state {{
              border-radius: 999px;
              padding: 3px 8px;
              font-size: 12px;
              font-weight: 700;
              background: color-mix(in srgb, var(--border) 65%, transparent);
            }}
            .state-running {{ color: #0b5dd8; }}
            .state-completed {{ color: var(--ok); }}
            .state-failed {{ color: var(--danger); }}
            .state-interrupted {{ color: #c57b00; }}
            .state-paused {{ color: #a66a00; }}
            .state-stopped {{ color: #8a3fd4; }}
            .crawler-grid {{
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 12px;
            }}
            .crawler-card {{
              border: 1px solid var(--border);
              border-radius: 14px;
              padding: 12px;
              background: color-mix(in srgb, var(--panel) 94%, #fff 6%);
            }}
            .crawler-card-head {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 8px;
              margin-bottom: 8px;
            }}
            .crawler-id-link {{
              color: var(--primary);
              text-decoration: none;
              font-weight: 700;
            }}
            .crawler-origin {{
              font-size: 13px;
              color: var(--muted);
              word-break: break-word;
              margin-bottom: 8px;
            }}
            .crawler-meta {{
              font-size: 12px;
              color: var(--muted);
              margin-bottom: 10px;
            }}
            .progress-wrap {{
              margin: 10px 0;
            }}
            .progress {{
              height: 10px;
              border-radius: 999px;
              overflow: hidden;
              background: color-mix(in srgb, var(--border) 70%, transparent);
            }}
            .progress span {{
              display: block;
              height: 100%;
              background: linear-gradient(90deg, #2f72f6, #2ec5ff);
            }}
            .timeline {{
              display: grid;
              grid-template-columns: repeat(3, 1fr);
              gap: 10px;
              color: var(--muted);
              font-size: 13px;
            }}
            .toast {{
              padding: 12px 14px;
              border-radius: 12px;
              border: 1px solid color-mix(in srgb, var(--ok) 45%, var(--border) 55%);
              background: color-mix(in srgb, var(--ok) 12%, var(--panel) 88%);
              margin-bottom: 12px;
              font-weight: 600;
            }}
            .toast-inline {{
              position: fixed;
              right: 16px;
              bottom: 16px;
              z-index: 60;
              min-width: 160px;
              max-width: 340px;
            }}
            .mobile-nav {{
              display: none;
            }}
            .detail-actions {{
              display: flex;
              align-items: center;
              gap: 10px;
            }}
            .detail-controls {{
              display: flex;
              flex-wrap: wrap;
              gap: 8px;
              margin-top: 10px;
              margin-bottom: 8px;
            }}
            .table-actions {{
              display: flex;
              flex-wrap: wrap;
              gap: 8px;
            }}
            .live-toggle {{
              display: flex;
              align-items: center;
              gap: 8px;
              color: var(--muted);
              font-size: 13px;
              white-space: nowrap;
            }}
            .live-toggle input {{
              width: auto;
              margin: 0;
            }}
            .detail-kpis {{
              margin-top: 14px;
              margin-bottom: 0;
            }}
            .detail-value {{
              font-size: 18px;
            }}
            .detail-current_url {{
              font-size: 14px;
              line-height: 1.45;
              word-break: break-word;
              overflow-wrap: anywhere;
              white-space: normal;
            }}
            .site-signature {{
              text-align: center;
              color: var(--muted);
              font-size: 13px;
              margin: 32px 0 8px;
              opacity: 0.9;
            }}
            @media (max-width: 1050px) {{
              .kpis {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
              }}
              .crawler-grid {{
                grid-template-columns: 1fr;
              }}
              .search-controls {{
                grid-template-columns: 1fr;
              }}
              .domain-filter-row {{
                grid-template-columns: 1fr;
              }}
              .search-google-bar {{
                grid-template-columns: 1fr;
                border-radius: 16px;
              }}
              .search-tools {{
                grid-template-columns: 1fr;
              }}
              .status-filters {{
                grid-template-columns: 1fr;
              }}
            }}
            @media (max-width: 740px) {{
              .topbar {{
                flex-wrap: wrap;
              }}
              .kpis {{
                grid-template-columns: 1fr;
              }}
              .timeline {{
                grid-template-columns: 1fr;
              }}
              .detail-actions {{
                width: 100%;
                justify-content: space-between;
              }}
              .mobile-nav {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                display: flex;
                justify-content: space-around;
                padding: 10px;
                border-top: 1px solid var(--border);
                background: var(--panel);
                z-index: 30;
              }}
              .mobile-nav a {{
                text-decoration: none;
                color: var(--text);
                font-weight: 600;
                font-size: 14px;
              }}
            }}
          </style>
        </head>
        <body data-page="{html.escape(active_nav)}" data-crawler-id="{html.escape(crawler_id or '')}" data-now="{html.escape(now)}">
          <header class="topbar">
            <div class="brand">
              <div class="logo">🕷️</div>
              <a href="/crawler/new" style="text-decoration:none;color:inherit;">CrawlDesk</a>
            </div>
            <nav class="nav">
              <a class="{ 'active' if active_nav == 'new' else '' }" href="/crawler/new">New Crawler</a>
              <a class="{ 'active' if active_nav == 'search' else '' }" href="/search">Search</a>
              <a class="{ 'active' if active_nav == 'status' else '' }" href="/status">Status</a>
            </nav>
            <div class="top-actions">
              <button class="theme-btn" id="theme-btn" type="button">Switch to Light</button>
            </div>
          </header>
          <main>
            {body}
            <div class="site-signature">Built by istemihan</div>
          </main>
          <nav class="mobile-nav">
            <a href="/crawler/new">New</a>
            <a href="/search">Search</a>
            <a href="/status">Status</a>
          </nav>
          <script>
            (function() {{
              const root = document.documentElement;
              const key = "crawl_theme";
              const themeBtn = document.getElementById("theme-btn");
              function syncThemeButton() {{
                if (!themeBtn) return;
                const current = root.getAttribute("data-theme") || "dark";
                themeBtn.textContent = current === "dark" ? "Switch to Light" : "Switch to Dark";
              }}
              syncThemeButton();
              themeBtn?.addEventListener("click", () => {{
                const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
                root.setAttribute("data-theme", next);
                localStorage.setItem(key, next);
                syncThemeButton();
              }});
              const toast = document.getElementById("toast");
              if (toast) {{
                setTimeout(() => toast.style.display = "none", 2400);
              }}
              function showToast(message) {{
                let el = document.getElementById("inline-toast");
                if (!el) {{
                  el = document.createElement("div");
                  el.id = "inline-toast";
                  el.className = "toast toast-inline";
                  document.body.appendChild(el);
                }}
                el.textContent = message;
                el.style.display = "block";
                clearTimeout(window.__crawlToastTimer);
                window.__crawlToastTimer = setTimeout(() => {{
                  if (el) el.style.display = "none";
                }}, 1600);
              }}

              async function refreshOverview() {{
                try {{
                  const res = await fetch("/api/overview", {{ cache: "no-store" }});
                  if (!res.ok) return;
                  const data = await res.json();
                  const map = {{
                    "URLs Visited": data.total_urls_visited,
                    "Active Crawlers": data.active_crawlers,
                    "Total Created": data.total_created
                  }};
                  Object.keys(map).forEach(k => {{
                    const el = document.querySelector(`[data-kpi="${{k}}"]`);
                    if (el) el.textContent = map[k];
                  }});
                  const lu = document.getElementById("last-update");
                  if (lu) {{
                    const d = new Date(data.updated_at);
                    lu.textContent = isNaN(d.getTime()) ? data.updated_at : d.toLocaleString();
                  }}
                }} catch (_e) {{}}
              }}
              setInterval(refreshOverview, 5000);
              refreshOverview();
              const page = document.body.getAttribute("data-page");
              const crawlerId = document.body.getAttribute("data-crawler-id");
              const autoRefreshToggle = document.getElementById("auto-refresh-toggle");
              const refreshDetailBtn = document.getElementById("refresh-detail-btn");
              const intentText = {{
                pause: "Pausing...",
                resume: "Resuming...",
                stop: "Stopping...",
              }};
              const doneText = {{
                pause: "Paused.",
                resume: "Resumed.",
                stop: "Stopped.",
              }};
              function humanEventName(eventType) {{
                const mapping = {{
                  queue_enqueue: "Added to queue",
                  queue_restored: "Restored from checkpoint",
                  queue_drop_backpressure: "Dropped due to backpressure",
                  visit_start: "Visit started",
                  visit_done: "Visit completed",
                  visit_error: "Visit failed"
                }};
                return mapping[eventType] || eventType || "-";
              }}
              function escapeHtml(value) {{
                return String(value ?? "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#39;");
              }}
              function formatDate(value) {{
                if (!value) return "-";
                const d = new Date(value);
                return isNaN(d.getTime()) ? value : d.toLocaleString();
              }}
              function setDetailValue(key, value) {{
                const el = document.querySelector(`.detail-${{key}}`);
                if (el) el.textContent = value ?? "-";
              }}
              function setStateBadgeClasses(el, state) {{
                if (!el) return;
                const known = ["running", "completed", "failed", "interrupted", "paused", "stopped"];
                known.forEach((name) => el.classList.remove(`state-${{name}}`));
                el.classList.add(`state-${{state}}`);
              }}
              function controlButtonsHtml(id, state) {{
                const cid = escapeHtml(id || "");
                const st = String(state || "").toLowerCase();
                let out = "";
                if (st === "running") {{
                  out += "<form method='post' action='/crawler/pause' class='crawler-control-form' style='display:inline;'>" +
                    `<input type='hidden' name='crawler_id' value='${{cid}}' />` +
                    "<button type='submit' class='action-btn' data-intent='pause'>Pause</button></form>";
                  out += "<form method='post' action='/crawler/stop' class='crawler-control-form' style='display:inline;'>" +
                    `<input type='hidden' name='crawler_id' value='${{cid}}' />` +
                    "<button type='submit' class='action-btn danger-btn' data-intent='stop'>Stop</button></form>";
                }} else if (["paused", "stopped", "interrupted", "failed"].includes(st)) {{
                  out += "<form method='post' action='/crawler/resume' class='crawler-control-form' style='display:inline;'>" +
                    `<input type='hidden' name='crawler_id' value='${{cid}}' />` +
                    "<button type='submit' class='action-btn' data-intent='resume'>Resume</button></form>";
                }}
                out += "<form method='post' action='/crawler/delete' style='display:inline;'>" +
                  `<input type='hidden' name='crawler_id' value='${{cid}}' />` +
                  "<button type='submit' class='action-btn danger-btn'>Delete</button></form>";
                return out;
              }}
              function updateCrawlerControls(id, state) {{
                const nextState = String(state || "").toLowerCase();
                document.querySelectorAll(".control-set").forEach((node) => {{
                  if ((node.getAttribute("data-crawler-id") || "") !== id) return;
                  node.setAttribute("data-crawler-state", nextState);
                  node.innerHTML = controlButtonsHtml(id, nextState);
                }});
                bindCrawlerControlForms();
                document.querySelectorAll(".state[data-crawler-id]").forEach((node) => {{
                  if ((node.getAttribute("data-crawler-id") || "") !== id) return;
                  node.textContent = nextState;
                  setStateBadgeClasses(node, nextState);
                }});
                if (document.body.getAttribute("data-crawler-id") === id) {{
                  setDetailValue("state", nextState);
                }}
              }}
              function renderVisitTrace(items) {{
                const body = document.getElementById("visit-trace-body");
                if (!body) return;
                if (!items || !items.length) {{
                  body.innerHTML = "<tr><td colspan='4' class='muted'>No visited URLs yet.</td></tr>";
                  return;
                }}
                body.innerHTML = items.map((item, index) =>
                  `<tr><td>${{index + 1}}</td><td class="trace-url">${{escapeHtml(item.url || "")}}</td><td>${{item.depth ?? ""}}</td><td>${{item.status_code ?? "-"}}</td></tr>`
                ).join("");
              }}
              function renderEventLog(items) {{
                const body = document.getElementById("event-log-body");
                if (!body) return;
                if (!items || !items.length) {{
                  body.innerHTML = "<tr><td colspan='5' class='muted'>No activity yet.</td></tr>";
                  return;
                }}
                body.innerHTML = items.map(item =>
                  `<tr><td>${{item.event_order ?? ""}}</td><td>${{escapeHtml(humanEventName(item.event_type))}}</td><td class="trace-url">${{escapeHtml(item.url || "")}}</td><td>${{item.depth ?? ""}}</td><td>${{escapeHtml(item.source || item.error || "")}}</td></tr>`
                ).join("");
              }}
              async function refreshCrawlerDetail() {{
                if (!(page === "status" && crawlerId)) return;
                try {{
                  const res = await fetch(`/api/status?id=${{encodeURIComponent(crawlerId)}}`, {{ cache: "no-store" }});
                  if (!res.ok) return;
                  const data = await res.json();
                  updateCrawlerControls(crawlerId, data.state || "");
                  setDetailValue("state", data.state || "-");
                  setDetailValue("pages_crawled", data.pages_crawled ?? 0);
                  setDetailValue("pages_indexed", data.pages_indexed ?? 0);
                  setDetailValue("pages_failed", data.pages_failed ?? 0);
                  setDetailValue("queue_size", data.queue_size ?? 0);
                  setDetailValue("current_url", data.current_url || "-");
                  const createdAt = document.querySelector(".detail-created_at");
                  const startedAt = document.querySelector(".detail-started_at");
                  const finishedAt = document.querySelector(".detail-finished_at");
                  if (createdAt) createdAt.textContent = formatDate(data.created_at);
                  if (startedAt) startedAt.textContent = formatDate(data.started_at);
                  if (finishedAt) finishedAt.textContent = formatDate(data.finished_at);
                  renderVisitTrace(data.visit_trace || []);
                  renderEventLog(data.event_log || []);
                  if (autoRefreshToggle && data.state !== "running") {{
                    autoRefreshToggle.checked = false;
                  }}
                  return data;
                }} catch (_e) {{}}
                return null;
              }}

              async function fetchCrawlerState(id) {{
                try {{
                  const res = await fetch(`/crawler/status/${{encodeURIComponent(id)}}`, {{ cache: "no-store" }});
                  if (!res.ok) return null;
                  const data = await res.json();
                  return String(data.state || "").toLowerCase() || null;
                }} catch (_e) {{
                  return null;
                }}
              }}
              async function waitForStableState(id, intent) {{
                if (intent === "resume") {{
                  return (await fetchCrawlerState(id)) || "running";
                }}
                const attempts = 10;
                for (let i = 0; i < attempts; i += 1) {{
                  const state = await fetchCrawlerState(id);
                  if (state && state !== "running") return state;
                  await new Promise((resolve) => setTimeout(resolve, 220));
                }}
                return (await fetchCrawlerState(id)) || (intent === "pause" ? "paused" : "stopped");
              }}

              async function handleCrawlerControlSubmit(form, ev) {{
                ev.preventDefault();
                const input = form.querySelector('input[name="crawler_id"]');
                const button = form.querySelector("button[type='submit']");
                if (!input || !button) {{
                  form.submit();
                  return;
                }}
                const id = (input.value || "").trim();
                if (!id) {{
                  form.submit();
                  return;
                }}
                const action = form.getAttribute("action") || "";
                let intent = "pause";
                if (action.includes("/resume")) intent = "resume";
                if (action.includes("/stop")) intent = "stop";
                const apiUrl = `/crawler/${{intent}}/${{encodeURIComponent(id)}}`;

                const oldLabel = button.textContent || "";
                button.classList.add("is-loading");
                button.textContent = intentText[intent] || "Working...";
                button.disabled = true;
                showToast(intentText[intent] || "Working...");
                try {{
                  const res = await fetch(apiUrl, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: "{{}}",
                    cache: "no-store",
                  }});
                  const data = await res.json().catch(() => ({{}}));
                  if (res.ok && data.ok) {{
                    const stableState = await waitForStableState(id, intent);
                    updateCrawlerControls(id, stableState);
                    showToast(doneText[intent] || "Done.");
                    if (page === "status" && crawlerId && crawlerId === id) {{
                      await refreshCrawlerDetail();
                    }}
                    return;
                  }}
                  showToast("Action failed.");
                }} catch (_e) {{
                  showToast("Network error.");
                }}
                button.classList.remove("is-loading");
                button.textContent = oldLabel;
                button.disabled = false;
              }}

              function bindCrawlerControlForms() {{
                document.querySelectorAll(".crawler-control-form").forEach((form) => {{
                  if (form.getAttribute("data-bound") === "1") return;
                  form.setAttribute("data-bound", "1");
                  form.addEventListener("submit", (ev) => {{
                    handleCrawlerControlSubmit(form, ev);
                  }});
                }});
              }}
              bindCrawlerControlForms();

              refreshDetailBtn?.addEventListener("click", refreshCrawlerDetail);
              if (page === "status" && crawlerId) {{
                setInterval(() => {{
                  if (autoRefreshToggle?.checked) {{
                    refreshCrawlerDetail();
                  }}
                }}, 4000);
              }}
            }})();
          </script>
        </body>
        </html>
        """


def run_web_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8080) -> None:
    manager = CrawlerManager(config)

    class _Handler(CrawlerWebHandler):
        pass

    _Handler.manager = manager
    _Handler.default_config = config

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"Web UI running at http://{host}:{port}")
    server.serve_forever()
