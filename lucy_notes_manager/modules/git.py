from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Dict, Optional, Union

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

PathLike = Union[str, bytes]


@dataclass
class _RepoBatch:
    repo_root: str
    base_msg: str
    tsmsg: bool
    tsfmt: str
    env: Dict[str, str]
    last_event_at: float = field(default_factory=time.time)
    event_types: set[str] = field(default_factory=set)
    hinted_paths: set[str] = field(default_factory=set)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    default_commit_message: str = "Auto-commit"
    default_timestamp_format: str = "%Y-%m-%d_%H-%M-%S"

    # All git params start with --git-
    template: Template = [
        # commit/push behavior
        ("--git-msg", str, None),
        ("--git-tsmsg", bool, False),
        ("--git-tsfmt", str, None),
        ("--git-key", str, None),  # NOTE: private key path (no .pub)
        # ---- performance knobs ----
        ("--git-debounce-seconds", float, 0.8),
        ("--git-timeout-sec", float, 8.0),  # add/status/commit timeout
        ("--git-push-timeout-sec", float, 20.0),  # push timeout
        ("--git-push-backoff-start-sec", float, 5.0),  # backoff start
        ("--git-push-backoff-max-sec", float, 120.0),  # backoff cap
    ]

    def __init__(self) -> None:
        super().__init__()
        self._q: Queue[tuple[str, str, list[str], dict]] = Queue()
        self._pending: dict[str, _RepoBatch] = {}
        self._pending_lock = threading.Lock()

        # push backoff per repo
        self._push_next_allowed_at: dict[str, float] = {}
        self._push_backoff: dict[str, float] = {}

        self._worker: threading.Thread = threading.Thread(
            target=self._worker_loop, daemon=True
        )
        self._worker.start()

    # ---------------- small utils ----------------

    @staticmethod
    def _to_str(path: PathLike) -> str:
        if isinstance(path, bytes):
            return path.decode(errors="surrogateescape")
        return path

    @staticmethod
    def _abs(p: str) -> str:
        return os.path.abspath(os.path.expanduser(p))

    @staticmethod
    def _cfg_first(cfg: dict, key: str) -> Any:
        v = cfg.get(key)
        if isinstance(v, list):
            return v[0] if v else None
        return v

    @staticmethod
    def _cfg_float(cfg: dict, key: str, default: float) -> float:
        v = Git._cfg_first(cfg, key)
        try:
            if v is None or v == "":
                return float(default)
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _path_is_inside_git_dir(path: str) -> bool:
        parts = os.path.abspath(path).split(os.sep)
        return ".git" in parts

    @staticmethod
    def _find_git_root(path: str) -> str | None:
        cur = os.path.abspath(path)
        if not os.path.isdir(cur):
            cur = os.path.dirname(cur)

        while True:
            if os.path.isdir(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                return None
            cur = parent

    # ---------------- git env / run ----------------

    def _git_env(self, cfg: dict) -> Dict[str, str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        key_raw = self._cfg_first(cfg, "git_key")
        if not isinstance(key_raw, str) or not key_raw:
            return env

        key_path = self._abs(key_raw)
        if not os.path.isfile(key_path):
            safe_notify(
                name=f"gkey-missing:{key_path}",
                message=f"SSH key not found:\n{key_path}",
            )
            return env

        env["GIT_SSH_COMMAND"] = (
            f'ssh -i "{key_path}" '
            f"-o IdentitiesOnly=yes "
            f"-o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new"
        )
        return env

    def _run_git(
        self,
        root: str,
        cmd: list[str],
        env: Dict[str, str],
        timeout_sec: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + cmd,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_sec,
        )

    # ---------------- commit message ----------------

    def _get_base_msg(self, cfg: dict) -> str:
        base = self.default_commit_message
        msg = self._cfg_first(cfg, "git_msg")
        if isinstance(msg, str) and msg:
            base = msg
        return base

    def _get_tsfmt(self, cfg: dict) -> str:
        tsfmt = self._cfg_first(cfg, "git_tsfmt")
        if isinstance(tsfmt, str) and tsfmt:
            return tsfmt
        return self.default_timestamp_format

    @staticmethod
    def _parse_porcelain_paths(porcelain: str) -> list[str]:
        out: list[str] = []
        for line in (porcelain or "").splitlines():
            line = line.rstrip("\n")
            if len(line) < 4:
                continue
            path_part = line[3:]  # after "XY "
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            out.append(path_part)
        return out

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
        et = "+".join(sorted(batch.event_types)) if batch.event_types else "change"

        names = [os.path.basename(p) for p in changed_paths if p]
        if not names and batch.hinted_paths:
            names = [os.path.basename(p) for p in sorted(batch.hinted_paths)]

        shown = ", ".join(names[:8])
        if len(names) > 8:
            shown += f", +{len(names) - 8} more"

        msg = f"{batch.base_msg}: {et}"
        if shown:
            msg += f" {shown}"

        if batch.tsmsg:
            msg += f" [{datetime.now().strftime(batch.tsfmt)}]"

        return msg

    # ---------------- batching worker ----------------

    def _enqueue(
        self, repo_root: str, event_type: str, paths: list[str], cfg: dict
    ) -> None:
        self._q.put((repo_root, event_type, paths, dict(cfg)))

    def _worker_loop(self) -> None:
        while True:
            # 1) ingest new events
            try:
                repo_root, event_type, paths, cfg = self._q.get(timeout=0.2)
                now = time.time()

                base_msg = self._get_base_msg(cfg)
                tsmsg = bool(self._cfg_first(cfg, "git_tsmsg"))
                tsfmt = self._get_tsfmt(cfg)
                env = self._git_env(cfg)

                with self._pending_lock:
                    batch = self._pending.get(repo_root)
                    if not batch:
                        batch = _RepoBatch(
                            repo_root=repo_root,
                            base_msg=base_msg,
                            tsmsg=tsmsg,
                            tsfmt=tsfmt,
                            env=env,
                        )
                        self._pending[repo_root] = batch

                    # latest options win
                    batch.base_msg = base_msg
                    batch.tsmsg = tsmsg
                    batch.tsfmt = tsfmt
                    batch.env = env

                    batch.last_event_at = now
                    batch.event_types.add(event_type)
                    for p in paths:
                        if p:
                            batch.hinted_paths.add(p)

            except Empty:
                pass

            # 2) flush batches that are quiet long enough (per-batch debounce read from config snapshot)
            now = time.time()
            due: list[_RepoBatch] = []

            with self._pending_lock:
                for root, batch in list(self._pending.items()):
                    # debounce is per-batch (taken from last event config)
                    debounce = self._cfg_float(
                        {
                            "git_debounce_seconds": self._cfg_first(
                                {"git_debounce_seconds": None}, "git_debounce_seconds"
                            )
                        },
                        "git_debounce_seconds",
                        0.8,
                    )
                    # ^ we don't have cfg here; debounce is stored in batch by design choice below.
                    # We'll store debounce in batch.env-free way: reuse batch.tsfmt slot? no.
                    # Instead: we compute debounce from batch.env? no.
                    # So: store debounce in env dict under a private key at enqueue time.
                    debounce = float(batch.env.get("__git_debounce_seconds", 0.8))

                    if now - batch.last_event_at >= debounce:
                        due.append(batch)
                        del self._pending[root]

            for batch in due:
                self._process_batch(batch)

    def _process_batch(self, batch: _RepoBatch) -> None:
        root = batch.repo_root
        env = batch.env

        git_timeout = float(env.get("__git_timeout_sec", 8.0))
        push_timeout = float(env.get("__git_push_timeout_sec", 20.0))
        backoff_start = float(env.get("__git_push_backoff_start_sec", 5.0))
        backoff_max = float(env.get("__git_push_backoff_max_sec", 120.0))

        # stage
        try:
            p_add = self._run_git(root, ["add", "-A"], env, timeout_sec=git_timeout)
        except subprocess.TimeoutExpired:
            safe_notify(
                name=f"timeout:add:{root}", message=f"git add timed out:\n{root}"
            )
            return

        if p_add.returncode != 0:
            err = (p_add.stderr or p_add.stdout or "git add failed").strip()
            safe_notify(
                name=f"addfail:{root}",
                message=f"Repository:\n{root}\n\nError:\n{err[:1200]}",
            )
            return

        # status
        try:
            p_status = self._run_git(
                root, ["status", "--porcelain"], env, timeout_sec=git_timeout
            )
        except subprocess.TimeoutExpired:
            safe_notify(
                name=f"timeout:status:{root}", message=f"git status timed out:\n{root}"
            )
            return

        if p_status.returncode != 0:
            err = (p_status.stderr or p_status.stdout or "git status failed").strip()
            safe_notify(
                name=f"statusfail:{root}",
                message=f"Repository:\n{root}\n\nError:\n{err[:1200]}",
            )
            return

        porcelain = (p_status.stdout or "").strip()
        changed_paths = self._parse_porcelain_paths(porcelain)

        # commit only if there are staged changes
        if porcelain:
            msg = self._build_commit_message(batch, changed_paths)

            try:
                p_commit = self._run_git(
                    root, ["commit", "-m", msg], env, timeout_sec=git_timeout
                )
            except subprocess.TimeoutExpired:
                safe_notify(
                    name=f"timeout:commit:{root}",
                    message=f"git commit timed out:\n{root}",
                )
                return

            if p_commit.returncode != 0:
                out = (
                    (((p_commit.stderr or "") + "\n" + (p_commit.stdout or "")))
                    .strip()
                    .lower()
                )
                if "nothing to commit" not in out:
                    err = (
                        p_commit.stderr or p_commit.stdout or "git commit failed"
                    ).strip()
                    safe_notify(
                        name=f"commitfail:{root}",
                        message=f"Repository:\n{root}\n\nError:\n{err[:1200]}",
                    )
                    return

        # ALWAYS push, but with backoff if it fails
        now = time.time()
        next_allowed = self._push_next_allowed_at.get(root, 0.0)
        if now < next_allowed:
            return

        try:
            p_push = self._run_git(root, ["push"], env, timeout_sec=push_timeout)
        except subprocess.TimeoutExpired:
            self._push_register_fail(root, backoff_start, backoff_max)
            safe_notify(
                name=f"timeout:push:{root}", message=f"git push timed out:\n{root}"
            )
            return

        if p_push.returncode != 0:
            self._push_register_fail(root, backoff_start, backoff_max)
            err = (p_push.stderr or p_push.stdout or "git push failed").strip()
            safe_notify(
                name=f"pushfail:{root}",
                message=f"Repository:\n{root}\n\nCommand:\ngit push\n\nError:\n{err[:1200]}",
            )
        else:
            self._push_backoff[root] = backoff_start
            self._push_next_allowed_at[root] = 0.0

    def _push_register_fail(
        self, root: str, backoff_start: float, backoff_max: float
    ) -> None:
        backoff = self._push_backoff.get(root, backoff_start)
        backoff = min(max(backoff, backoff_start) * 2.0, backoff_max)
        self._push_backoff[root] = backoff
        self._push_next_allowed_at[root] = time.time() + backoff

    # ---------------- event handlers (new contract) ----------------

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "modified")

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "deleted")

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "moved")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> Optional[IgnoreMap]:
        ev = system.event

        src = self._to_str(getattr(ev, "src_path", "") or "")
        dest_raw = getattr(ev, "dest_path", None)
        dest = self._to_str(dest_raw) if dest_raw is not None else ""

        src = self._abs(src) if src else ""
        dest = self._abs(dest) if dest else ""

        if (src and self._path_is_inside_git_dir(src)) or (
            dest and self._path_is_inside_git_dir(dest)
        ):
            return None

        root = self._find_git_root(ctx.path) or self._find_git_root(dest or src)
        if not root:
            return None

        # read knobs from ctx.config (already merged + defaults)
        debounce = self._cfg_float(ctx.config, "git_debounce_seconds", 0.8)
        git_timeout = self._cfg_float(ctx.config, "git_timeout_sec", 8.0)
        push_timeout = self._cfg_float(ctx.config, "git_push_timeout_sec", 20.0)
        backoff_start = self._cfg_float(ctx.config, "git_push_backoff_start_sec", 5.0)
        backoff_max = self._cfg_float(ctx.config, "git_push_backoff_max_sec", 120.0)

        # batch env (store knobs in env so worker can use them later per-repo-batch)
        env = self._git_env(ctx.config)
        env["__git_debounce_seconds"] = str(debounce)
        env["__git_timeout_sec"] = str(git_timeout)
        env["__git_push_timeout_sec"] = str(push_timeout)
        env["__git_push_backoff_start_sec"] = str(backoff_start)
        env["__git_push_backoff_max_sec"] = str(backoff_max)

        base_msg = self._get_base_msg(ctx.config)
        tsmsg = bool(self._cfg_first(ctx.config, "git_tsmsg"))
        tsfmt = self._get_tsfmt(ctx.config)

        with self._pending_lock:
            batch = self._pending.get(root)
            if not batch:
                batch = _RepoBatch(
                    repo_root=root,
                    base_msg=base_msg,
                    tsmsg=tsmsg,
                    tsfmt=tsfmt,
                    env=env,
                )
                self._pending[root] = batch

            # latest options win
            batch.base_msg = base_msg
            batch.tsmsg = tsmsg
            batch.tsfmt = tsfmt
            batch.env = env
            batch.last_event_at = time.time()
            batch.event_types.add(event_type)

            if event_type != "moved":
                batch.hinted_paths.add(ctx.path)
            else:
                if src:
                    batch.hinted_paths.add(src)
                if dest:
                    batch.hinted_paths.add(dest)

        return None
