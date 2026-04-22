from __future__ import annotations

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
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


def _config(transport: str, **kwargs) -> NotificationConfig:
    base = dict(
        repo_root="/r",
        platform="github",
        transport=transport,
        secret="s",
        extra={"webhook_id": "github:o/n"},
        feed_url="https://x/y",
    )
    base.update(kwargs)
    return NotificationConfig(**base)


def test_build_providers_groups_configs_by_transport():
    providers = build_providers(
        [
            _config("rss", feed_url="https://a/b"),
            _config("rss", feed_url="https://c/d", repo_root="/r2"),
            _config("webhook"),
        ],
        callback=lambda _e: None,
    )
    by_name = {p.name for p in providers}
    assert by_name == {"rss", "webhook"}


def test_register_provider_allows_adding_new_transport():
    class DummyProvider(NotificationProvider):
        name = "dummy"
        added: list = []

        def __init__(self, callback: PushCallback) -> None:
            super().__init__(callback)

        def add_repository(self, config: NotificationConfig) -> None:
            DummyProvider.added.append(config.repo_root)

        def start(self) -> None: ...

        def stop(self) -> None: ...

    try:
        register_provider("dummy", DummyProvider)
        providers = build_providers(
            [_config("dummy", repo_root="/custom")],
            callback=lambda _e: None,
        )
        assert len(providers) == 1
        assert DummyProvider.added == ["/custom"]
    finally:
        PROVIDER_REGISTRY.pop("dummy", None)


def test_build_providers_rejects_unknown_transport():
    import pytest

    with pytest.raises(ValueError):
        build_providers(
            [_config("telegram")],
            callback=lambda _e: None,
        )


def test_registry_contains_builtin_providers_after_build():
    build_providers([], callback=lambda _e: None)
    assert PROVIDER_REGISTRY["rss"] is RSSProvider
    assert PROVIDER_REGISTRY["webhook"] is WebhookProvider
