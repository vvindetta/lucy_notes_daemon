from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, List, Type

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationProvider,
    PushCallback,
)

PROVIDER_REGISTRY: Dict[str, Type[NotificationProvider]] = {}


def register_provider(name: str, provider_cls: Type[NotificationProvider]) -> None:
    """
    Register a notification provider so it can be selected via config.

    Providers are keyed by transport name: 'webhook', 'rss', ... Adding a new
    transport is a one-line call; provider-specific platform handling is
    resolved inside the provider itself (see webhook.PlatformAdapter).
    """
    PROVIDER_REGISTRY[name] = provider_cls


def build_providers(
    configs: List[NotificationConfig],
    callback: PushCallback,
    *,
    webhook_host: str = "127.0.0.1",
    webhook_port: int = 8765,
    webhook_path: str = "/webhook",
) -> List[NotificationProvider]:
    """
    Group configs by transport and build one provider instance per transport.

    This coalesces repositories that share the same transport (e.g. a single
    webhook HTTP server serves multiple repos).
    """
    from lucy_notes_manager.notifications.rss import RSSProvider
    from lucy_notes_manager.notifications.webhook import WebhookProvider

    if "webhook" not in PROVIDER_REGISTRY:
        register_provider("webhook", WebhookProvider)
    if "rss" not in PROVIDER_REGISTRY:
        register_provider("rss", RSSProvider)

    grouped: Dict[str, List[NotificationConfig]] = defaultdict(list)
    for cfg in configs:
        grouped[cfg.transport].append(cfg)

    providers: List[NotificationProvider] = []
    for transport, transport_configs in grouped.items():
        provider_cls = PROVIDER_REGISTRY.get(transport)
        if provider_cls is None:
            raise ValueError(
                f"Unknown notification transport: {transport!r}. "
                f"Known: {sorted(PROVIDER_REGISTRY)}"
            )

        provider = _construct(
            provider_cls,
            callback,
            webhook_host=webhook_host,
            webhook_port=webhook_port,
            webhook_path=webhook_path,
        )
        for cfg in transport_configs:
            provider.add_repository(cfg)
        providers.append(provider)
    return providers


def _construct(
    provider_cls: Type[NotificationProvider],
    callback: PushCallback,
    *,
    webhook_host: str,
    webhook_port: int,
    webhook_path: str,
) -> NotificationProvider:
    # Webhook provider wants host/port/path; RSS provider does not.
    try:
        return provider_cls(
            callback=callback,
            host=webhook_host,
            port=webhook_port,
            path=webhook_path,
        )
    except TypeError:
        return provider_cls(callback=callback)
