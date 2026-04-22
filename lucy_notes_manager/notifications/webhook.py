from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional

from lucy_notes_manager.notifications.base import (
    NotificationConfig,
    NotificationEvent,
    NotificationProvider,
    PushCallback,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ParsedRequest:
    platform: str
    event: str
    ref: Optional[str]
    repo_identifier: Optional[str]
    body_bytes: bytes
    payload: dict


SignatureVerifier = Callable[["_ParsedRequest", "RepoEntry", dict], bool]


@dataclass
class RepoEntry:
    config: NotificationConfig
    # Provider-agnostic identifier used to route incoming webhooks to a repo.
    # Format: "<platform>:<owner>/<name>" or raw path from config.extra["webhook_id"].
    identifier: str


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _verify_hmac_sha256(body_bytes: bytes, secret: str, signature_header: str) -> bool:
    """
    GitHub/Gitea/Forgejo format: 'sha256=<hex>' (HMAC-SHA256 of body).
    """
    if not signature_header or "=" not in signature_header:
        return False
    algo, _, hex_digest = signature_header.partition("=")
    if algo.lower() != "sha256":
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, hex_digest.strip())


def _header(headers: dict, name: str) -> Optional[str]:
    """Case-insensitive header lookup."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def verify_github(parsed: _ParsedRequest, entry: RepoEntry, headers: dict) -> bool:
    secret = entry.config.secret
    if not secret:
        return False
    sig = _header(headers, "X-Hub-Signature-256")
    return _verify_hmac_sha256(parsed.body_bytes, secret, sig or "")


# Gitea and Forgejo use the same GitHub-compatible signature scheme.
verify_gitea = verify_github
verify_forgejo = verify_github


def verify_gitlab(parsed: _ParsedRequest, entry: RepoEntry, headers: dict) -> bool:
    secret = entry.config.secret
    if not secret:
        return False
    token = _header(headers, "X-Gitlab-Token")
    return bool(token) and _constant_time_eq(secret, token.strip())


PLATFORM_VERIFIERS: Dict[str, SignatureVerifier] = {
    "github": verify_github,
    "gitea": verify_gitea,
    "forgejo": verify_forgejo,
    "gitlab": verify_gitlab,
}


def detect_platform(headers: dict) -> Optional[str]:
    """Identify the sending platform from request headers."""
    # Normalise case
    h = {k.lower(): v for k, v in headers.items()}
    if "x-github-event" in h:
        return "github"
    if "x-gitea-event" in h:
        return "gitea"
    if "x-forgejo-event" in h:
        return "forgejo"
    if "x-gitlab-event" in h:
        return "gitlab"
    return None


def extract_repo_identifier(platform: str, payload: dict) -> Optional[str]:
    """
    Return '<platform>:<owner>/<name>' for the incoming push event.

    All four platforms expose the repository under payload['repository'] with
    either 'full_name' (GitHub/Gitea/Forgejo) or 'path_with_namespace' (GitLab).
    """
    repo = payload.get("repository") or {}
    full_name = (
        repo.get("full_name")
        or repo.get("path_with_namespace")
        or repo.get("fullname")
    )
    if not full_name:
        return None
    return f"{platform}:{full_name}"


def extract_ref(payload: dict) -> Optional[str]:
    value = payload.get("ref")
    if isinstance(value, str):
        return value
    return None


class _Handler(BaseHTTPRequestHandler):
    server: "_WebhookServer"

    def log_message(self, format: str, *args) -> None:
        logger.debug("webhook http | " + format, *args)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > self.server.max_body_bytes:
            return b""
        return self.rfile.read(length)

    def _reply(self, code: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802  (BaseHTTPRequestHandler convention)
        if self.path.rstrip("/") != self.server.webhook_path.rstrip("/"):
            self._reply(404, "not found")
            return

        headers = {k.lower(): v for k, v in self.headers.items()}
        platform = detect_platform(headers)
        if platform is None:
            self._reply(400, "unknown sender")
            return

        body_bytes = self._read_body()
        if not body_bytes:
            self._reply(400, "empty body")
            return

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._reply(400, "invalid json")
            return

        event = (
            headers.get("x-github-event")
            or headers.get("x-gitea-event")
            or headers.get("x-forgejo-event")
            or headers.get("x-gitlab-event")
            or ""
        )
        # Only handle push events — the only thing that warrants a pull.
        if "push" not in event.lower():
            self._reply(204, "")
            return

        repo_identifier = extract_repo_identifier(platform, payload)
        entry = self.server.find_entry(repo_identifier) if repo_identifier else None
        if entry is None:
            self._reply(404, "repo not configured")
            return

        parsed = _ParsedRequest(
            platform=platform,
            event=event,
            ref=extract_ref(payload),
            repo_identifier=repo_identifier,
            body_bytes=body_bytes,
            payload=payload,
        )

        verifier = PLATFORM_VERIFIERS.get(platform)
        if verifier is None or not verifier(parsed, entry, headers):
            self._reply(401, "signature invalid")
            return

        # Branch filter
        if entry.config.branch and parsed.ref:
            expected = entry.config.branch
            if not (
                parsed.ref == expected
                or parsed.ref == f"refs/heads/{expected}"
            ):
                self._reply(204, "")
                return

        try:
            self.server.invoke_callback(
                NotificationEvent(
                    repo_root=entry.config.repo_root,
                    platform=platform,
                    source="webhook",
                    ref=parsed.ref,
                    raw=payload,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("webhook callback raised")
        self._reply(200, "ok")


class _WebhookServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        webhook_path: str,
        entries: List[RepoEntry],
        callback: PushCallback,
        max_body_bytes: int,
    ) -> None:
        super().__init__(address, _Handler)
        self.webhook_path = webhook_path
        self._entries = entries
        self._callback = callback
        self.max_body_bytes = max_body_bytes

    def find_entry(self, identifier: Optional[str]) -> Optional[RepoEntry]:
        if identifier is None:
            return None
        for entry in self._entries:
            if entry.identifier == identifier:
                return entry
        return None

    def invoke_callback(self, event: NotificationEvent) -> None:
        self._callback(event)


class WebhookProvider(NotificationProvider):
    """
    A single HTTP server that receives push events from all configured repos.

    Supported platforms: github, gitea, forgejo (HMAC-SHA256) and gitlab
    (token equality via `X-Gitlab-Token`).

    Security:
    - Every repo must supply a `secret`. Requests without a valid signature are
      rejected with HTTP 401. There is no anonymous/default-allow mode.
    - Request bodies above `max_body_bytes` are rejected.
    - The server binds to `host` (default 127.0.0.1). Put a reverse proxy in
      front of it if you need to expose it to the internet.
    """

    name = "webhook"

    def __init__(
        self,
        callback: PushCallback,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/webhook",
        max_body_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        super().__init__(callback)
        self._host = host
        self._port = port
        self._path = path
        self._max_body_bytes = max_body_bytes

        self._entries: List[RepoEntry] = []
        self._server: Optional[_WebhookServer] = None
        self._thread: Optional[threading.Thread] = None

    def add_repository(self, config: NotificationConfig) -> None:
        identifier = config.extra.get("webhook_id")
        if not identifier:
            raise ValueError(
                "webhook provider requires config.extra['webhook_id'] in the form "
                "'<platform>:<owner>/<name>', matching the payload's repository.full_name"
            )
        self._entries.append(RepoEntry(config=config, identifier=identifier))

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _WebhookServer(
            address=(self._host, self._port),
            webhook_path=self._path,
            entries=self._entries,
            callback=self._callback,
            max_body_bytes=self._max_body_bytes,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="lucy-webhook-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "webhook provider listening on http://%s:%s%s (%d repos)",
            self._host,
            self._port,
            self._path,
            len(self._entries),
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
