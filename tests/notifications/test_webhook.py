from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from typing import List

import pytest

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationEvent,
)
from lucy_notes_manager.notifications.webhook import (
    RepoEntry,
    WebhookProvider,
    _ParsedRequest,
    detect_platform,
    extract_ref,
    extract_repo_identifier,
    verify_github,
    verify_gitlab,
)


def test_detect_platform_picks_github_from_event_header():
    assert detect_platform({"X-GitHub-Event": "push"}) == "github"
    assert detect_platform({"x-gitea-event": "push"}) == "gitea"
    assert detect_platform({"X-Forgejo-Event": "push"}) == "forgejo"
    assert detect_platform({"X-Gitlab-Event": "Push Hook"}) == "gitlab"
    assert detect_platform({"X-Other": "x"}) is None


def test_extract_repo_identifier_handles_github_and_gitlab_payloads():
    github_payload = {"repository": {"full_name": "owner/name"}}
    gitlab_payload = {"repository": {"path_with_namespace": "owner/name"}}
    assert extract_repo_identifier("github", github_payload) == "github:owner/name"
    assert extract_repo_identifier("gitlab", gitlab_payload) == "gitlab:owner/name"
    assert extract_repo_identifier("github", {}) is None


def test_extract_ref_returns_ref_or_none():
    assert extract_ref({"ref": "refs/heads/main"}) == "refs/heads/main"
    assert extract_ref({}) is None
    assert extract_ref({"ref": None}) is None


def _make_parsed(body: bytes) -> _ParsedRequest:
    return _ParsedRequest(
        platform="github",
        event="push",
        ref="refs/heads/main",
        repo_identifier="github:owner/name",
        body_bytes=body,
        payload=json.loads(body.decode("utf-8")) if body else {},
    )


def _make_entry(secret: str) -> RepoEntry:
    cfg = NotificationConfig(
        repo_root="/repo",
        platform="github",
        transport="webhook",
        secret=secret,
        extra={"webhook_id": "github:owner/name"},
    )
    return RepoEntry(config=cfg, identifier="github:owner/name")


def test_verify_github_accepts_matching_signature():
    body = b'{"ref":"refs/heads/main"}'
    secret = "s3cret"
    sig = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    parsed = _make_parsed(body)
    entry = _make_entry(secret)
    assert verify_github(parsed, entry, {"X-Hub-Signature-256": sig})


def test_verify_github_rejects_wrong_signature():
    body = b'{"ref":"refs/heads/main"}'
    parsed = _make_parsed(body)
    entry = _make_entry("s3cret")
    assert not verify_github(
        parsed, entry, {"X-Hub-Signature-256": "sha256=00deadbeef"}
    )


def test_verify_github_rejects_missing_signature():
    body = b'{"ref":"refs/heads/main"}'
    parsed = _make_parsed(body)
    entry = _make_entry("s3cret")
    assert not verify_github(parsed, entry, {})


def test_verify_github_rejects_when_no_secret_configured():
    body = b"{}"
    parsed = _make_parsed(body)
    entry = _make_entry("")
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert not verify_github(parsed, entry, {"X-Hub-Signature-256": sig})


def test_verify_gitlab_accepts_matching_token():
    entry = _make_entry("tok3n")
    parsed = _make_parsed(b"{}")
    assert verify_gitlab(parsed, entry, {"X-Gitlab-Token": "tok3n"})
    assert not verify_gitlab(parsed, entry, {"X-Gitlab-Token": "nope"})
    assert not verify_gitlab(parsed, entry, {})


# ---------------------------------------------------------------------------
# Integration: boot the HTTP server and post a real request.
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def running_webhook():
    received: List[NotificationEvent] = []
    port = _find_free_port()
    provider = WebhookProvider(
        callback=received.append,
        host="127.0.0.1",
        port=port,
        path="/hook",
    )
    provider.add_repository(
        NotificationConfig(
            repo_root="/repo",
            platform="github",
            transport="webhook",
            secret="s3cret",
            extra={"webhook_id": "github:owner/name"},
        )
    )
    provider.start()

    # Wait briefly for the server to start accepting connections.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/hook", timeout=0.2
            ):
                break
        except urllib.error.HTTPError:
            break
        except urllib.error.URLError:
            time.sleep(0.05)

    try:
        yield provider, received, port
    finally:
        provider.stop()
        provider.join(timeout=2.0)


def _post(port: int, headers: dict, body: bytes) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def test_webhook_provider_accepts_valid_github_push(running_webhook):
    provider, received, port = running_webhook

    body = json.dumps(
        {"ref": "refs/heads/main", "repository": {"full_name": "owner/name"}}
    ).encode("utf-8")
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    status = _post(
        port,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
        body=body,
    )

    assert status == 200
    # Give the handler a tick to append.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not received:
        time.sleep(0.01)

    assert len(received) == 1
    event = received[0]
    assert event.repo_root == "/repo"
    assert event.platform == "github"
    assert event.source == "webhook"
    assert event.ref == "refs/heads/main"


def test_webhook_provider_rejects_bad_signature(running_webhook):
    _provider, received, port = running_webhook
    body = json.dumps(
        {"ref": "refs/heads/main", "repository": {"full_name": "owner/name"}}
    ).encode("utf-8")

    status = _post(
        port,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
        body=body,
    )
    assert status == 401
    assert received == []


def test_webhook_provider_404_for_unknown_repo(running_webhook):
    _provider, received, port = running_webhook
    body = json.dumps(
        {"ref": "refs/heads/main", "repository": {"full_name": "other/repo"}}
    ).encode("utf-8")
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    status = _post(
        port,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
        body=body,
    )
    assert status == 404
    assert received == []


def test_webhook_provider_ignores_non_push_events(running_webhook):
    _provider, received, port = running_webhook
    body = b"{}"
    status = _post(
        port,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
        },
        body=body,
    )
    assert status == 204
    assert received == []


def test_webhook_provider_requires_webhook_id_in_extra():
    provider = WebhookProvider(callback=lambda _e: None)
    with pytest.raises(ValueError):
        provider.add_repository(
            NotificationConfig(
                repo_root="/r",
                platform="github",
                transport="webhook",
                secret="x",
                extra={},
            )
        )
