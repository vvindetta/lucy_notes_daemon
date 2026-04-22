"""
Repository-update notification subsystem.

Providers monitor a remote repository (GitHub, Gitea, GitLab, Forgejo, ...)
and invoke a callback when new commits are available. The `Git` module uses
this to trigger a `pull` without relying on file-open polling.

Supported transports:
- webhook: a local HTTP server that receives push events.
- rss    : periodic polling of a provider RSS/Atom feed.

The public entry points are:
- `NotificationProvider`   base class
- `NotificationConfig`     per-repository configuration
- `PROVIDER_REGISTRY`      name -> provider class
- `build_providers()`      helper to construct providers from a config list
"""

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationEvent,
    NotificationProvider,
    PushCallback,
)
from lucy_notes_manager.notifications.registry import (
    PROVIDER_REGISTRY,
    build_providers,
    register_provider,
)
from lucy_notes_manager.notifications.rss import RSSProvider
from lucy_notes_manager.notifications.webhook import WebhookProvider

__all__ = [
    "NotificationConfig",
    "NotificationEvent",
    "NotificationProvider",
    "PROVIDER_REGISTRY",
    "PushCallback",
    "RSSProvider",
    "WebhookProvider",
    "build_providers",
    "register_provider",
]
