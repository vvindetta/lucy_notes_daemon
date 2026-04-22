from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class NotificationEvent:
    """
    A single "remote repository changed" event.

    - repo_root: absolute local path of the repository to refresh
    - platform:  'github' | 'gitea' | 'gitlab' | 'forgejo' | ...
    - ref:       branch ref (e.g. 'refs/heads/main'), if known
    - source:    'webhook' | 'rss'
    - raw:       provider-specific payload for debugging/logging
    """

    repo_root: str
    platform: str
    source: str
    ref: Optional[str] = None
    raw: Optional[dict] = None


PushCallback = Callable[[NotificationEvent], None]


@dataclass
class NotificationConfig:
    """
    Per-repository monitoring configuration.

    Fields
    - repo_root: absolute local path of the repository to pull into
    - platform : 'github' | 'gitea' | 'gitlab' | 'forgejo'
    - transport: 'webhook' | 'rss'
    - feed_url : URL of the RSS/Atom feed (for transport='rss')
    - poll_interval_sec: seconds between RSS polls (transport='rss')
    - secret   : shared secret for webhook signature validation
    - branch   : branch name to filter events on (optional)
    - extra    : provider-specific options
    """

    repo_root: str
    platform: str
    transport: str
    feed_url: Optional[str] = None
    poll_interval_sec: float = 300.0
    secret: Optional[str] = None
    branch: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)


class NotificationProvider(ABC):
    """
    Base class for repository-update notification providers.

    Lifecycle:
        start()  - begin monitoring (spawn threads / server)
        stop()   - request shutdown
        join()   - block until shutdown is complete

    On each update, the provider calls `callback(NotificationEvent)`.
    Providers must:
    - Be safe to call `start()` then `stop()` concurrently.
    - Never block `start()`; do async work in a background thread.
    - Rate-limit their own calls so they do not overwhelm the callback.
    """

    name: str = "abstract"

    def __init__(self, callback: PushCallback) -> None:
        self._callback = callback

    @abstractmethod
    def add_repository(self, config: NotificationConfig) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def join(self, timeout: Optional[float] = None) -> None:
        """Default: nothing to join. Providers with threads override this."""
        return None
