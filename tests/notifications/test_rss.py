from __future__ import annotations

from typing import List

import pytest

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationEvent,
)
from lucy_notes_manager.notifications.rss import RSSProvider, parse_feed_entries


ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>example</title>
  <entry>
    <id>urn:entry:1</id>
    <updated>2026-04-20T10:00:00Z</updated>
  </entry>
  <entry>
    <id>urn:entry:2</id>
    <updated>2026-04-21T10:00:00Z</updated>
  </entry>
</feed>
""".strip()


RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>example</title>
    <item>
      <guid>g-1</guid>
      <pubDate>Mon, 20 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <guid>g-2</guid>
      <pubDate>Tue, 21 Apr 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
""".strip()


def test_parse_feed_entries_parses_atom_ids():
    entries = parse_feed_entries(ATOM_SAMPLE)
    assert [e.entry_id for e in entries] == ["urn:entry:1", "urn:entry:2"]


def test_parse_feed_entries_parses_rss_guids():
    entries = parse_feed_entries(RSS_SAMPLE)
    assert [e.entry_id for e in entries] == ["g-1", "g-2"]


def test_parse_feed_entries_returns_empty_on_unknown_xml():
    assert parse_feed_entries("<html><body/></html>") == []


def test_parse_feed_entries_returns_empty_on_invalid_xml():
    assert parse_feed_entries("<<not xml>>") == []


class _StubFetcher:
    def __init__(self, bodies: List[str]):
        self._bodies = list(bodies)
        self.calls: List[tuple[str, float]] = []

    def fetch(self, url: str, timeout: float) -> str:
        self.calls.append((url, timeout))
        if not self._bodies:
            raise AssertionError("fetcher called more times than bodies provided")
        return self._bodies.pop(0)


def test_rss_provider_does_not_fire_on_baseline_poll():
    received: List[NotificationEvent] = []
    fetcher = _StubFetcher([ATOM_SAMPLE])
    provider = RSSProvider(callback=received.append, fetcher=fetcher)
    provider.add_repository(
        NotificationConfig(
            repo_root="/r",
            platform="github",
            transport="rss",
            feed_url="https://example/atom",
            poll_interval_sec=1.0,
        )
    )

    provider.poll_once()

    assert received == []
    assert fetcher.calls == [("https://example/atom", 15.0)]


def test_rss_provider_fires_on_new_entries_after_baseline():
    received: List[NotificationEvent] = []
    atom_after = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>example</title>
  <entry>
    <id>urn:entry:1</id>
    <updated>2026-04-20T10:00:00Z</updated>
  </entry>
  <entry>
    <id>urn:entry:2</id>
    <updated>2026-04-21T10:00:00Z</updated>
  </entry>
  <entry>
    <id>urn:entry:3</id>
    <updated>2026-04-22T10:00:00Z</updated>
  </entry>
</feed>
""".strip()
    fetcher = _StubFetcher([ATOM_SAMPLE, atom_after])
    provider = RSSProvider(callback=received.append, fetcher=fetcher)
    provider.add_repository(
        NotificationConfig(
            repo_root="/r",
            platform="github",
            transport="rss",
            feed_url="https://example/atom",
            poll_interval_sec=0.0,  # no throttle
        )
    )

    provider.poll_once()  # baseline
    provider.poll_once()  # sees entry:3

    assert len(received) == 1
    event = received[0]
    assert event.repo_root == "/r"
    assert event.platform == "github"
    assert event.source == "rss"
    assert "urn:entry:3" in (event.raw or {}).get("new_entries", [])


def test_rss_provider_throttles_per_interval(monkeypatch):
    received: List[NotificationEvent] = []
    fetcher = _StubFetcher([ATOM_SAMPLE])
    provider = RSSProvider(callback=received.append, fetcher=fetcher)
    provider.add_repository(
        NotificationConfig(
            repo_root="/r",
            platform="github",
            transport="rss",
            feed_url="https://example/atom",
            poll_interval_sec=600.0,  # 10 min
        )
    )

    provider.poll_once()
    provider.poll_once()
    provider.poll_once()

    # Second and third ticks are skipped by the interval guard.
    assert len(fetcher.calls) == 1


def test_rss_provider_requires_feed_url():
    provider = RSSProvider(callback=lambda _e: None)
    with pytest.raises(ValueError):
        provider.add_repository(
            NotificationConfig(
                repo_root="/r",
                platform="github",
                transport="rss",
                feed_url=None,
            )
        )
