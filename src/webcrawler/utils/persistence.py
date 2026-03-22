from __future__ import annotations

import json
from pathlib import Path

from webcrawler.models import CrawlTask
from webcrawler.types import StateStore


class JsonStateStore(StateStore):
    def __init__(self, checkpoint_path: Path) -> None:
        self.checkpoint_path = checkpoint_path
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, seen: set[str], pending: list[CrawlTask]) -> None:
        payload = {
            "seen": sorted(seen),
            "pending": [
                {
                    "url": task.url,
                    "depth": task.depth,
                    "origin": task.origin,
                    "parent_url": task.parent_url,
                }
                for task in pending
            ],
        }
        self.checkpoint_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2))

    def load(self) -> tuple[set[str], list[CrawlTask]]:
        if not self.checkpoint_path.exists():
            return set(), []

        raw = json.loads(self.checkpoint_path.read_text())
        seen = set(raw.get("seen", []))
        pending = [
            CrawlTask(
                url=item["url"],
                depth=int(item["depth"]),
                origin=item.get("origin"),
                parent_url=item.get("parent_url"),
            )
            for item in raw.get("pending", [])
        ]
        return seen, pending
