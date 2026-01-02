import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Dict, List, Union, cast

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule

PathLike = Union[str, bytes]
KnownArgs = Dict[str, List[Any]]


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

    # NOTE: --gkey must be PRIVATE key path (no .pub)
    template = (
        ("--gmsg", str),
        ("--tsmsg", bool),
        ("--tsfmt", str),
        ("--gkey", str),
    )

    # ---- performance knobs ----
    debounce_seconds: float = 0.8  # merge events inside this window
    git_timeout_sec: float = 8  # git add/status/commit timeout
    push_timeout_sec: float = 20  # git push timeout
    push_backoff_start_sec: float = 5.0  # backoff if push fails
    push_backoff_max_sec: float = 120.0

    def __init__(self) -> None:
        super().__init__()
        self._q: Queue[tuple[str, str, list[str], KnownArgs]] = Queue()
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
    def _path_is_inside_git_dir(path: str) -> bool:
        # extra protection (even if ChangeHandler already ignores .git)
        parts = os.path.abspath(path).split(os.sep)
        return ".git" in parts

    @staticmethod
    def _find_git_root(path: str) -> str | None:
        cur = os.path.abspath(path)
        if os.path.isfile(cur):
            cur = os.path.dirname(cur)

        while True:
            if os.path.isdir(os.path.join(cur, ".git")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                return None
            cur = parent

    # ---------------- git env / run ----------------

    def _git_env(self, known_args: KnownArgs) -> Dict[str, str]:
        """
        Build environment for git commands.
        If --gkey is provided, force ssh to use that key (BatchMode prevents prompts).
        """
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        key_list = known_args.get("gkey")
        if not key_list:
            return env

        key_raw = key_list[0]
        if not isinstance(key_raw, str) or not key_raw:
            return env

        key_path = os.path.abspath(os.path.expanduser(key_raw))
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

    def _get_base_msg(self, known: KnownArgs) -> str:
        base = self.default_commit_message
        gmsg = known.get("gmsg")
        if gmsg and isinstance(gmsg[0], str) and gmsg[0]:
            base = gmsg[0]
        return base

    def _get_tsfmt(self, known: KnownArgs) -> str:
        tsfmt = known.get("tsfmt")
        if tsfmt and isinstance(tsfmt[0], str) and tsfmt[0]:
            return tsfmt[0]
        return self.default_timestamp_format

    @staticmethod
    def _parse_porcelain_paths(porcelain: str) -> list[str]:
        """
        Extract file paths from `git status --porcelain` output.
        Handles rename lines like: R  old -> new
        """
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
        self, repo_root: str, event_type: str, paths: list[str], known: KnownArgs
    ) -> None:
        self._q.put((repo_root, event_type, paths, known))

    def _worker_loop(self) -> None:
        while True:
            # 1) ingest new events
            try:
                repo_root, event_type, paths, known = self._q.get(timeout=0.2)
                now = time.time()

                base_msg = self._get_base_msg(known)
                tsmsg = bool(known.get("tsmsg"))  # keep old semantics
                tsfmt = self._get_tsfmt(known)
                env = self._git_env(known)

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

                    # update batch (latest options win)
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

            # 2) flush batches that are quiet long enough
            now = time.time()
            due: list[_RepoBatch] = []
            with self._pending_lock:
                for root, batch in list(self._pending.items()):
                    if now - batch.last_event_at >= self.debounce_seconds:
                        due.append(batch)
                        del self._pending[root]

            for batch in due:
                self._process_batch(batch)

    def _process_batch(self, batch: _RepoBatch) -> None:
        root = batch.repo_root
        env = batch.env

        # stage
        try:
            p_add = self._run_git(
                root, ["add", "-A"], env, timeout_sec=self.git_timeout_sec
            )
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
                root, ["status", "--porcelain"], env, timeout_sec=self.git_timeout_sec
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
                    root, ["commit", "-m", msg], env, timeout_sec=self.git_timeout_sec
                )
            except subprocess.TimeoutExpired:
                safe_notify(
                    name=f"timeout:commit:{root}",
                    message=f"git commit timed out:\n{root}",
                )
                return

            if p_commit.returncode != 0:
                out = (
                    ((p_commit.stderr or "") + "\n" + (p_commit.stdout or ""))
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
            p_push = self._run_git(
                root, ["push"], env, timeout_sec=self.push_timeout_sec
            )
        except subprocess.TimeoutExpired:
            self._push_register_fail(root)
            safe_notify(
                name=f"timeout:push:{root}", message=f"git push timed out:\n{root}"
            )
            return

        if p_push.returncode != 0:
            self._push_register_fail(root)
            err = (p_push.stderr or p_push.stdout or "git push failed").strip()
            safe_notify(
                name=f"pushfail:{root}",
                message=f"Repository:\n{root}\n\nCommand:\ngit push\n\nError:\n{err[:1200]}",
            )
        else:
            self._push_backoff[root] = self.push_backoff_start_sec
            self._push_next_allowed_at[root] = 0.0

    def _push_register_fail(self, root: str) -> None:
        backoff = self._push_backoff.get(root, self.push_backoff_start_sec)
        backoff = min(
            max(backoff, self.push_backoff_start_sec) * 2.0, self.push_backoff_max_sec
        )
        self._push_backoff[root] = backoff
        self._push_next_allowed_at[root] = time.time() + backoff

    # ---------------- event handlers ----------------
    # New AbstractModule contract: return List[str] | None
    # This module does not write files directly -> always returns None.

    def created(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        known_raw, _ = parse_args(self.template, args)
        known = cast(KnownArgs, known_raw)

        path = self._to_str(event.src_path)
        if self._path_is_inside_git_dir(path):
            return None

        root = self._find_git_root(path)
        if root:
            self._enqueue(root, "created", [path], known)
        return None

    def modified(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        known_raw, _ = parse_args(self.template, args)
        known = cast(KnownArgs, known_raw)

        path = self._to_str(event.src_path)
        if self._path_is_inside_git_dir(path):
            return None

        root = self._find_git_root(path)
        if root:
            self._enqueue(root, "modified", [path], known)
        return None

    def deleted(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        known_raw, _ = parse_args(self.template, args)
        known = cast(KnownArgs, known_raw)

        path = self._to_str(event.src_path)
        if self._path_is_inside_git_dir(path):
            return None

        root = self._find_git_root(path)
        if root:
            self._enqueue(root, "deleted", [path], known)
        return None

    def moved(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        known_raw, _ = parse_args(self.template, args)
        known = cast(KnownArgs, known_raw)

        src = self._to_str(event.src_path)
        dest_raw = getattr(event, "dest_path", None)
        dest = self._to_str(dest_raw) if dest_raw is not None else ""

        if (src and self._path_is_inside_git_dir(src)) or (
            dest and self._path_is_inside_git_dir(dest)
        ):
            return None

        root = self._find_git_root(dest or src)
        if root:
            self._enqueue(root, "moved", [src, dest], known)
        return None
