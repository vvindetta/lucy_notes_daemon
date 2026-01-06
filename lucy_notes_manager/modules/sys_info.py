from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from lucy_notes_manager.lib import slow_write_lines_from
from lucy_notes_manager.lib.args import delete_args_from_string
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class SysInfo(AbstractModule):
    name: str = "sys"
    priority: int = 0

    template = [
        ("--mods", bool, False),
        ("--help", bool, False),
        ("--sys-event", bool, False),
        ("--config", bool, False),  # ✅ param name: --config
        ("--sys-slow-print", bool, False),
    ]

    def _defaults_map(self, system: System) -> dict[str, Any]:
        """
        Build {dest_key: default_value} from global template.
        dest_key matches argparse dest:
          --sys-event -> sys_event
        """
        out: dict[str, Any] = {}
        for flag, _typ, default in system.global_template:
            dest = flag.lstrip("-").replace("-", "_")
            out[dest] = default
        return out

    def _build_block(
        self, *, system: System, ctx: Context, opts: set[str], path: str
    ) -> List[str]:
        def title_from_opts(values: set[str]) -> str:
            order = ["mods", "help", "config", "event"]
            if len(values) == 1:
                return next(iter(values))
            parts = [x for x in order if x in values]
            return "+".join(parts) if parts else "sys"

        title = title_from_opts(opts)

        out: List[str] = []
        out.append(f"--- {title} ---\n")

        # time ONLY when event is included
        if "event" in opts:
            out.append(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.append("\n")

        if "mods" in opts:
            for m in system.modules:
                out.append(f"* {m.name} ({getattr(m, 'priority', None)})\n")
            out.append("\n")

        if "help" in opts:
            for flag, typ, default in system.global_template:
                tname = getattr(typ, "__name__", str(typ))
                out.append(f"* {flag} type={tname} default={default}\n")
            out.append("\n")

        if "config" in opts:
            defaults = self._defaults_map(system)

            any_printed = False

            for key in sorted(ctx.config.keys()):
                cur = ctx.config.get(key)
                dflt = defaults.get(key, None)

                # show only changed vs defaults (or keys not in template)
                if key in defaults and cur == dflt:
                    continue

                src = (
                    "file:" + ",".join(map(str, ctx.arg_lines.get(key, [])))
                    if key in ctx.arg_lines
                    else "config/default"
                )
                out.append(f"* {key} = {cur} (default={dflt}, src={src})\n")
                any_printed = True

            if not any_printed:
                out.append("* (no differences from defaults)\n")

            out.append("\n")

        if "event" in opts:
            e = system.event
            out.append(f"* type: {getattr(e, 'event_type', None)}\n")
            out.append(f"* is_directory: {getattr(e, 'is_directory', None)}\n")
            out.append(f"* src_path: {getattr(e, 'src_path', None)}\n")
            out.append(f"* dest_path: {getattr(e, 'dest_path', None)}\n")
            out.append(f"* ctx.path: {path}\n")
            out.append("\n")

        return out

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        key_to_opt = {
            "mods": "mods",
            "help": "help",
            "sys_event": "event",
            "config": "config",  # ✅ new option
        }
        key_to_flag = {
            "mods": "--mods",
            "help": "--help",
            "sys_event": "--sys-event",
            "config": "--config",  # ✅ param name
        }

        occurrences: dict[int, set[str]] = {}
        flags_on_line: dict[int, List[str]] = {}

        # NOTE: placement is still controlled by ctx.arg_lines (i.e. by flags written in the file)
        for key, opt in key_to_opt.items():
            if not ctx.config.get(key):
                continue
            line_nums = ctx.arg_lines.get(key)
            if not line_nums:
                continue
            for lineno_1based in line_nums:
                occurrences.setdefault(lineno_1based, set()).add(opt)
                flags_on_line.setdefault(lineno_1based, []).append(key_to_flag[key])

        if not occurrences:
            return None

        sep = str("---").strip()
        sep_line = sep + ("\n" if not sep.endswith("\n") else "")

        try:
            with open(ctx.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []

        if not lines:
            lines = ["\n"]

        min_from_line: int | None = None

        for lineno_1based in sorted(occurrences.keys(), reverse=True):
            idx = max(0, min(len(lines) - 1, lineno_1based - 1))
            opts = occurrences[lineno_1based]
            remove_flags = flags_on_line[lineno_1based]

            block = self._build_block(system=system, ctx=ctx, opts=opts, path=ctx.path)

            if idx == 0:
                cleaned0 = delete_args_from_string(lines[0], remove_flags)

                # if first line had ONLY sys flags -> replace line 1 with block (no separator, no shifting)
                if cleaned0.strip() == "":
                    lines[0:1] = block
                    min_from_line = (
                        1 if min_from_line is None else min(min_from_line, 1)
                    )
                    continue

                # first-line rule only if something else remains on line 1
                lines[0] = cleaned0

                if lines[0].strip():
                    lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    lines[0] = "\n"
                    insert_pos = 1

                lines[insert_pos:insert_pos] = [sep_line] + block

                start_line = insert_pos + 1  # list index -> 1-based line
                min_from_line = (
                    start_line
                    if min_from_line is None
                    else min(min_from_line, start_line)
                )

            else:
                cleaned = delete_args_from_string(lines[idx], remove_flags)

                lines[idx : idx + 1] = block

                if cleaned.strip():
                    lines[idx + len(block) : idx + len(block)] = [cleaned]

                start_line = idx + 1
                min_from_line = (
                    start_line
                    if min_from_line is None
                    else min(min_from_line, start_line)
                )

        from_line = min_from_line or 1

        if ctx.config.get("sys_slow_print"):
            return slow_write_lines_from(
                ctx.path, lines, from_line=from_line, delay=0.1
            )

        with open(ctx.path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
