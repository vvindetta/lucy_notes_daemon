from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from lucy_notes_manager.lib.args import delete_args_from_string
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


@dataclass(frozen=True)
class CmdRun:
    lineno_1based: int
    cmd_tokens: List[str]


class Cmd(AbstractModule):
    """
    File usage:

        --c ls
        --c echo hello

    Output format:

        --- ls ---
        <output>

    Notes:
    - Uses already-parsed tokens from ctx.config + ctx.arg_lines (no manual parsing).
    - Executes with shell=False.
    - Replaces the original line containing --c with an output block.
    - Removes the --c ... part from the original line; if anything else remains on that line,
      it is kept after the block.
    """

    name: str = "cmd"
    priority: int = 50

    template = [
        ("--c", str, None),  # command tokens (nargs="+" in your argparse wrapper)
        ("--cmd-timeout", int, [5]),  # seconds
        ("--cmd-max-bytes", int, [20000]),  # clip stdout+stderr written into file
        ("--cmd-show-stderr", bool, True),
        ("--cmd-show-stdout", bool, True),
    ]

    # ----------------------------
    # Collect command runs by line using ctx.arg_lines["c"]
    # ----------------------------
    def _collect_runs(self, ctx: Context) -> List[CmdRun]:
        values = ctx.config.get("c")
        line_nums = ctx.arg_lines.get("c")

        if not values or not line_nums:
            return []

        if not isinstance(values, list) or not isinstance(line_nums, list):
            return []

        # Group tokens by their source line number
        by_line: Dict[int, List[str]] = {}
        for tok, ln in zip(values, line_nums):
            by_line.setdefault(int(ln), []).append(str(tok))

        runs: List[CmdRun] = []
        for ln in sorted(by_line.keys()):
            tokens = [t for t in by_line[ln] if t != ""]
            if tokens:
                runs.append(CmdRun(lineno_1based=ln, cmd_tokens=tokens))

        return runs

    # ----------------------------
    # Helpers
    # ----------------------------
    def _to_str(self, x) -> str:
        if x is None:
            return ""
        if isinstance(x, bytes):
            return x.decode("utf-8", errors="replace")
        return str(x)

    def _clip(self, s: str, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""
        b = s.encode("utf-8", errors="replace")
        if len(b) <= max_bytes:
            return s
        return b[:max_bytes].decode("utf-8", errors="replace") + "\n…(clipped)…\n"

    # ----------------------------
    # Execute command
    # ----------------------------
    def _run_cmd(
        self, *, cmd_tokens: List[str], cwd: str, timeout: int
    ) -> Tuple[int, str, str]:
        try:
            p = subprocess.run(
                cmd_tokens,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            return p.returncode, (p.stdout or ""), (p.stderr or "")

        except FileNotFoundError as e:
            return 127, "", f"Command not found: {e}\n"

        except subprocess.TimeoutExpired as e:
            out = self._to_str(getattr(e, "stdout", None))
            err = self._to_str(getattr(e, "stderr", None))
            err = err + f"\nTIMEOUT after {timeout}s\n"
            return 124, out, err

        except Exception as e:
            return 1, "", f"ERROR: {type(e).__name__}: {e}\n"

    # ----------------------------
    # Build output block
    # ----------------------------
    def _build_block(
        self,
        *,
        cmd_tokens: List[str],
        stdout: str,
        stderr: str,
        show_stdout: bool,
        show_stderr: bool,
        max_bytes: int,
    ) -> List[str]:
        title = cmd_tokens[0] if cmd_tokens else "cmd"

        out: List[str] = []
        out.append(f"--- {title} ---\n")

        wrote_any = False

        if show_stdout and stdout:
            out.append(self._clip(stdout, max_bytes))
            if not out[-1].endswith("\n"):
                out.append("\n")
            wrote_any = True

        if show_stderr and stderr:
            if wrote_any and out[-1].strip() != "":
                out.append("\n")
            out.append(self._clip(stderr, max_bytes))
            if not out[-1].endswith("\n"):
                out.append("\n")
            wrote_any = True

        if not wrote_any:
            out.append("(empty)\n")

        out.append("\n")
        return out

    # ----------------------------
    # Apply (replace lines)
    # ----------------------------
    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        runs = self._collect_runs(ctx)
        if not runs:
            return None

        timeout_raw = ctx.config.get("cmd_timeout") or [5]
        timeout = (
            int(timeout_raw[0]) if isinstance(timeout_raw, list) else int(timeout_raw)
        )

        maxb_raw = ctx.config.get("cmd_max_bytes") or [20000]
        max_bytes = int(maxb_raw[0]) if isinstance(maxb_raw, list) else int(maxb_raw)

        show_stdout = bool(ctx.config.get("cmd_show_stdout"))
        show_stderr = bool(ctx.config.get("cmd_show_stderr"))

        try:
            with open(ctx.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return None

        if not lines:
            lines = ["\n"]

        cwd = os.path.dirname(os.path.abspath(ctx.path)) or os.getcwd()

        # Replace from bottom to top to avoid index shifts
        for run in sorted(runs, key=lambda r: r.lineno_1based, reverse=True):
            idx = max(0, min(len(lines) - 1, run.lineno_1based - 1))

            # Remove only the --c ... segment from the original line
            cleaned = delete_args_from_string(lines[idx], ["--c"])

            _code, out_s, err_s = self._run_cmd(
                cmd_tokens=run.cmd_tokens,
                cwd=cwd,
                timeout=timeout,
            )

            block = self._build_block(
                cmd_tokens=run.cmd_tokens,
                stdout=out_s,
                stderr=err_s,
                show_stdout=show_stdout,
                show_stderr=show_stderr,
                max_bytes=max_bytes,
            )

            # Replace the line with the block
            lines[idx : idx + 1] = block

            # If there is other content on that line (besides --c ...), keep it after the block
            if cleaned.strip():
                lines[idx + len(block) : idx + len(block)] = [cleaned]

        with open(ctx.path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return {ctx.path: 1}

    # ----------------------------
    # Events
    # ----------------------------
    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        # The file is already gone.
        return None
