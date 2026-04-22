from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationEvent,
    NotificationProvider,
    PushCallback,
)

logger = logging.getLogger(__name__)

# RFC 4287 (Atom) and RSS 2.0 tag names used by GitHub/Gitea/GitLab/Forgejo.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass
class _FeedEntry:
    entry_id: str
    updated: Optional[str]


def parse_feed_entries(body_text: str) -> List[_FeedEntry]:
    """
    Parse an Atom or RSS 2.0 document and return its entries.

    Only the subset of fields needed to detect "something changed" is parsed:
    entry id/guid and updated/pubDate. Unknown formats yield an empty list.
    """
    try:
        root = ET.fromstring(body_text)
    except ET.ParseError:
        return []

    # Atom: <feed><entry>...</entry></feed>
    if root.tag == f"{_ATOM_NS}feed" or root.tag.endswith("}feed"):
        entries: List[_FeedEntry] = []
        for item in root.findall(f"{_ATOM_NS}entry"):
            entry_id = _text(item, f"{_ATOM_NS}id")
            updated = _text(item, f"{_ATOM_NS}updated") or _text(
                item, f"{_ATOM_NS}published"
            )
            if entry_id:
                entries.append(_FeedEntry(entry_id=entry_id, updated=updated))
        return entries

    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    channel = root.find("channel")
    if channel is not None:
        entries = []
        for item in channel.findall("item"):
            entry_id = _text(item, "guid") or _text(item, "link")
            updated = _text(item, "pubDate")
            if entry_id:
                entries.append(_FeedEntry(entry_id=entry_id, updated=updated))
        return entries

    return []


def _text(element: ET.Element, path: str) -> Optional[str]:
    child = element.find(path)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


class _FeedFetcher:
    """Small wrapper around urllib so we can monkeypatch in tests."""

    def fetch(self, url: str, timeout: float) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "lucy-notes-daemon/rss (+https://codeberg.org)"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")


class RSSProvider(NotificationProvider):
    """
    Polls RSS/Atom feeds to detect repository updates.

    Security / fairness:
    - Each repo has its own `poll_interval_sec`; the provider never polls a
      single URL more than once per interval.
    - Per-URL `latest_entry_id`s are remembered so the first successful poll
      only triggers a pull for entries newer than that baseline.
    - Failures (network / parse) are logged and retried on the next tick.
    """

    name = "rss"

    def __init__(
        self,
        callback: PushCallback,
        fetcher: Optional[_FeedFetcher] = None,
        tick_seconds: float = 1.0,
    ) -> None:
        super().__init__(callback)
        self._configs: List[NotificationConfig] = []
        self._fetcher = fetcher or _FeedFetcher()
        self._tick_seconds = tick_seconds

        self._last_poll_at: Dict[str, float] = {}
        self._seen_entry_ids: Dict[str, set[str]] = {}
        self._baseline_done: Dict[str, bool] = {}

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_repository(self, config: NotificationConfig) -> None:
        if not config.feed_url:
            raise ValueError(
                f"rss provider requires feed_url for repo {config.repo_root}"
            )
        self._configs.append(config)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="lucy-rss-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info("rss provider watching %d feed(s)", len(self._configs))

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def poll_once(self) -> None:
        """Fetch any feeds whose interval has elapsed. Safe to call in tests."""
        now = time.monotonic()
        for cfg in self._configs:
            url = cfg.feed_url or ""
            if not url:
                continue
            last = self._last_poll_at.get(url, 0.0)
            if last > 0.0 and (now - last) < cfg.poll_interval_sec:
                continue
            self._last_poll_at[url] = now
            self._poll_feed(cfg)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("rss poll_once raised")
            self._stop_event.wait(self._tick_seconds)

    def _poll_feed(self, cfg: NotificationConfig) -> None:
        url = cfg.feed_url or ""
        try:
            body = self._fetcher.fetch(url, timeout=15.0)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            logger.warning("rss fetch failed | url=%s | err=%s", url, err)
            return

        entries = parse_feed_entries(body)
        if not entries:
            logger.debug("rss feed empty or unparseable | url=%s", url)
            return

        seen = self._seen_entry_ids.setdefault(url, set())
        new_entries = [e for e in entries if e.entry_id not in seen]

        if not self._baseline_done.get(url, False):
            # First successful poll: establish baseline without firing events.
            seen.update(e.entry_id for e in entries)
            self._baseline_done[url] = True
            return

        if not new_entries:
            return

        for entry in new_entries:
            seen.add(entry.entry_id)
        try:
            self._callback(
                NotificationEvent(
                    repo_root=cfg.repo_root,
                    platform=cfg.platform,
                    source="rss",
                    ref=None,
                    raw={"url": url, "new_entries": [e.entry_id for e in new_entries]},
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("rss callback raised")
