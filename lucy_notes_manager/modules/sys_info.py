from __future__ import annotations

from datetime import datetime
from typing import List, Optional

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
        ("--sys-separator", str, ["---"]),
    ]

    def _build_block(self, *, system: System, opts: set[str], path: str) -> List[str]:
        def title_from_opts(values: set[str]) -> str:
            order = ["mods", "help", "event"]
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
        }
        key_to_flag = {
            "mods": "--mods",
            "help": "--help",
            "sys_event": "--sys-event",
        }

        occurrences: dict[int, set[str]] = {}
        flags_on_line: dict[int, List[str]] = {}

        for key, opt in key_to_opt.items():
            if not ctx.config[key]:
                continue
            lines = ctx.arg_lines.get(key)
            if not lines:
                continue
            for lineno_1based in lines:
                occurrences.setdefault(lineno_1based, set()).add(opt)
                flags_on_line.setdefault(lineno_1based, []).append(key_to_flag[key])

        if not occurrences:
            return None

        sep = str(ctx.config["sys_separator"][0]).strip()
        sep_line = sep + ("\n" if not sep.endswith("\n") else "")

        with open(ctx.path, "r+", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                lines = ["\n"]

            for lineno_1based in sorted(occurrences.keys(), reverse=True):
                idx = max(0, min(len(lines) - 1, lineno_1based - 1))
                opts = occurrences[lineno_1based]
                remove_flags = flags_on_line[lineno_1based]

                block = self._build_block(system=system, opts=opts, path=ctx.path)

                if idx == 0:
                    cleaned0 = delete_args_from_string(lines[0], remove_flags)

                    # if first line had ONLY sys flags -> replace line 1 with block (no separator, no shifting)
                    if cleaned0.strip() == "":
                        lines[0:1] = block
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

                else:
                    cleaned = delete_args_from_string(lines[idx], remove_flags)

                    lines[idx : idx + 1] = block

                    if cleaned.strip():
                        lines[idx + len(block) : idx + len(block)] = [cleaned]

            f.seek(0)
            f.truncate()
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
