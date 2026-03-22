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
            self._render_search_page(query_params=query)
            return
        if parsed.path == "/status":
            self._render_status_page(query_params=query)
            return
        self._send_html(404, self._layout("Not Found", "<h2>Not Found</h2>", active_nav=""))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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
        self.send_header("Location", f"/status?id={crawler_id}&flash=started")
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
                <article class="result">
                  <a class="title" href="{html.escape(hit["url"])}" target="_blank" rel="noreferrer">{html.escape(hit["title"])}</a>
                  <div class="url">{html.escape(hit["url"])}</div>
                  <div class="snippet">{html.escape(hit["snippet"])}</div>
                </article>
                """
            )
        results_html = "".join(rows) if rows else ("<p class='muted'>No results.</p>" if q.strip() else "")
        body = f"""
        <section class="panel search-hero">
          <h1 class="brand-search">Search</h1>
          <form method="get" action="/search" class="search-controls">
            <input name="q" value="{html.escape(q)}" placeholder="Search the index..." />
            <button type="submit">Search</button>
          </form>
          <form method="get" action="/search" class="domain-filter-row">
            <input type="hidden" name="q" value="{html.escape(q)}" />
            <input name="domain" value="{html.escape(domain)}" placeholder="Optional domain filter, e.g. wikipedia.org" />
            <button type="submit">Apply Domain Filter</button>
          </form>
        </section>
        <section class="panel">{results_html}</section>
        """
        self._send_html(200, self._layout("Search", body, active_nav="search"))

    def _render_status_page(self, query_params: dict[str, list[str]]) -> None:
        crawler_id = query_params.get("id", [""])[0].strip()
        state_filter = query_params.get("state", ["all"])[0]
        domain_filter = query_params.get("domain", [""])[0]
        text_filter = query_params.get("q", [""])[0]
        flash = query_params.get("flash", [""])[0]
        overview = self.manager.get_overview()
        jobs = self.manager.filter_crawlers(state=state_filter, domain=domain_filter, query=text_filter)

        rows = []
        for job in jobs:
            rows.append(
                f"""
                <tr>
                  <td><a href="/status?id={html.escape(job["crawler_id"])}#detail">{html.escape(job["crawler_id"])}</a></td>
                  <td><span class="state state-{html.escape(job["state"])}">{html.escape(job["state"])}</span></td>
                  <td class="trace-url">{html.escape(job["origin"])}</td>
                  <td>{_format_dt(job["created_at"])}</td>
                  <td>
                    <form method="post" action="/crawler/delete" style="display:inline;">
                      <input type="hidden" name="crawler_id" value="{html.escape(job["crawler_id"])}" />
                      <button type="submit" class="action-btn danger-btn">Delete</button>
                    </form>
                  </td>
                </tr>
                """
            )
        detail = ""
        if crawler_id:
            status = self.manager.get_crawler_status(crawler_id)
            if status is None:
                detail = f"<section class='panel'><p>Crawler not found: {html.escape(crawler_id)}</p></section>"
            else:
                detail = self._crawler_detail_html(status)

        filters = f"""
        <form method="get" action="/status" class="status-filters">
          <input name="q" value="{html.escape(text_filter)}" placeholder="Search by origin..." />
          <input name="domain" value="{html.escape(domain_filter)}" placeholder="Domain filter..." />
          <select name="state">
            {self._state_option("all", state_filter)}
            {self._state_option("running", state_filter)}
            {self._state_option("completed", state_filter)}
            {self._state_option("failed", state_filter)}
            {self._state_option("interrupted", state_filter)}
          </select>
          <button type="submit">Filter</button>
        </form>
        """
        body = f"""
        {self._flash_html(flash)}
        {self._overview_cards_html(overview)}
        <section class="panel">
          <div class="panel-title-row"><h2>Status</h2><div class="muted">Last update: <span id="last-update">{html.escape(_format_dt(overview['updated_at']))}</span></div></div>
          <form method="post" action="/data/clear" style="margin-bottom:10px;">
            <button type="submit" class="danger-btn">Clear All Data</button>
          </form>
          {filters}
          <table><thead><tr><th>ID</th><th>State</th><th>Origin</th><th>Created</th><th>Actions</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
        </section>
        {detail}
        """
        self._send_html(200, self._layout("Status", body, active_nav="status", crawler_id=crawler_id or None))

    def _state_option(self, value: str, current: str) -> str:
        selected = "selected" if value == current else ""
        return f"<option {selected} value='{value}'>{value.title()}</option>"

    def _read_form(self) -> dict[str, list[str]]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
        return parse_qs(raw, keep_blank_values=True)

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
            rows.append(
                f"<tr><td><a href='/status?id={html.escape(item.get('crawler_id', ''))}#detail'>{html.escape(item.get('crawler_id', ''))}</a></td><td>{html.escape(item.get('state', ''))}</td><td class='trace-url'>{html.escape(item.get('origin', ''))}</td><td>{_format_dt(item.get('created_at'))}</td></tr>"
            )
        return (
            "<section class='panel'><h3>Recently Created Crawlers</h3>"
            "<table><thead><tr><th>ID</th><th>State</th><th>Origin</th><th>Created</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    def _crawler_detail_html(self, status: dict[str, Any]) -> str:
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
            <h2>Crawler Details</h2>
            <div class="detail-actions">
              <label class="live-toggle"><input type="checkbox" id="auto-refresh-toggle" checked /> Auto Refresh</label>
              <button type="button" id="refresh-detail-btn" class="action-btn">Refresh Now</button>
            </div>
          </div>
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
            "started": "Crawler started successfully.",
            "deleted": "Crawler deleted.",
            "delete_failed": "Delete failed: crawler may still be running.",
            "cleared": "All data cleared.",
            "clear_blocked": "Stop running crawlers before clearing data.",
        }
        if flash not in text_map:
            return ""
        return f"<div id='toast' class='toast'>{html.escape(text_map[flash])}</div>"

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
              margin-left: 6px;
              font-size: 12px;
              border-radius: 9px;
            }}
            .danger-btn {{
              background: linear-gradient(135deg, #a21212, #dc3030);
            }}
            .info-row {{
              font-size: 13px;
              color: var(--muted);
            }}
            .search-hero {{
              text-align: center;
            }}
            .brand-search {{
              font-size: 46px;
              margin: 6px 0 12px;
              letter-spacing: -1px;
            }}
            .result {{
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 12px;
              margin-bottom: 10px;
              background: color-mix(in srgb, var(--panel) 94%, #fff 6%);
            }}
            .title {{
              color: color-mix(in srgb, var(--primary) 80%, #1a0dab 20%);
              text-decoration: none;
              font-size: 20px;
              font-weight: 600;
            }}
            .url {{
              color: var(--ok);
              margin-top: 4px;
              font-size: 13px;
            }}
            .snippet {{
              color: var(--muted);
              margin-top: 6px;
              line-height: 1.45;
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
            .mobile-nav {{
              display: none;
            }}
            .detail-actions {{
              display: flex;
              align-items: center;
              gap: 10px;
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
              .search-controls {{
                grid-template-columns: 1fr;
              }}
              .domain-filter-row {{
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
                }} catch (_e) {{}}
              }}
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
