from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from lucy_notes_manager.lib.args import delete_args_from_string
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Sys(AbstractModule):
    name: str = "sys"
    priority: int = 0

    template = [
        ("--mods", bool, False, "Print loaded modules and their priorities."),
        (
            "--config",
            bool,
            False,
            "Print config values that differ from defaults (and where they were set).",
        ),
        (
            "--man",
            str,
            None,
            "Argument manual. Use: --man list OR --man full OR --man <name> (example: --man todo).",
        ),
        (
            "--help",
            bool,
            False,
            "Print SysInfo commands help: --mods, --man, --config.",
        ),
        ("--sys-event", bool, False, "Print current filesystem event details."),
    ]

    @staticmethod
    def _flag_to_dest(flag: str) -> str:
        return flag.lstrip("-").replace("-", "_")

    @staticmethod
    def _type_name(type_value: Any) -> str:
        return getattr(type_value, "__name__", str(type_value))

    def _defaults_map(self, system: System) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for flag, _typ, default, _desc in system.global_template:
            defaults[self._flag_to_dest(flag)] = default
        return defaults

    @staticmethod
    def _command_help_lines() -> List[str]:
        return [
            "* --mods: print loaded modules and their priorities\n",
            "* --config: print config values that differ from defaults\n",
            "* --man list: print all arguments (no descriptions)\n",
            "* --man full: print all arguments with descriptions\n",
            "* --man <name>: print one argument with description (example: --man todo)\n",
        ]

    @staticmethod
    def _normalize_arg_name(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        return text.lstrip("-").strip().lower()

    def _man_list_lines(self, system: System) -> List[str]:
        lines: List[str] = []
        for flag, typ, default, _desc in system.global_template:
            type_name = self._type_name(typ)
            lines.append(f"* {flag} type={type_name} default={default}\n")
        return lines or ["* (no args)\n"]

    def _man_full_lines(self, system: System) -> List[str]:
        lines: List[str] = []
        for flag, typ, default, desc in system.global_template:
            type_name = self._type_name(typ)
            description = (desc or "").strip()
            lines.append(
                f"* {flag}: {description} (type={type_name}, default={default})\n"
            )
        return lines or ["* (no args)\n"]

    def _man_one_lines(self, system: System, requested_names: List[str]) -> List[str]:
        requested = [self._normalize_arg_name(item) for item in (requested_names or [])]
        requested = [item for item in requested if item]
        if not requested:
            return ["* (missing name)\n"]

        requested_set = set(requested)
        matched: List[str] = []

        for flag, typ, default, desc in system.global_template:
            flag_name = flag.lstrip("-").lower()
            dest_name = self._flag_to_dest(flag).lower()
            if flag_name in requested_set or dest_name in requested_set:
                type_name = self._type_name(typ)
                description = (desc or "").strip()
                matched.append(
                    f"* {flag}: {description} (type={type_name}, default={default})\n"
                )

        if matched:
            return matched

        return [f"* (unknown arg: {', '.join(requested)})\n"]

    def _man_lines(self, system: System, requests: List[str]) -> List[str]:
        normalized_requests = [
            self._normalize_arg_name(item) for item in (requests or [])
        ]
        normalized_requests = [item for item in normalized_requests if item]

        if not normalized_requests:
            return ["* (missing man mode: list/full/name)\n"]

        if normalized_requests[0] == "list":
            return self._man_list_lines(system)

        if normalized_requests[0] == "full":
            return self._man_full_lines(system)

        return self._man_one_lines(system, normalized_requests)

    def _build_block(
        self,
        *,
        system: System,
        ctx: Context,
        selected_opts: set[str],
        path: str,
        man_requests: List[str],
    ) -> List[str]:
        ordered = ["mods", "help", "man", "config", "event"]
        title_parts = [name for name in ordered if name in selected_opts]
        title = "+".join(title_parts) if title_parts else "sys"

        lines: List[str] = []
        lines.append(f"--- {title} ---\n")

        if "event" in selected_opts:
            lines.append(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append("\n")

        if "help" in selected_opts:
            lines.extend(self._command_help_lines())
            lines.append("\n")

        if "mods" in selected_opts:
            for module in system.modules:
                lines.append(f"* {module.name} ({getattr(module, 'priority', None)})\n")
            lines.append("\n")

        if "man" in selected_opts:
            lines.extend(self._man_lines(system, man_requests))
            lines.append("\n")

        if "config" in selected_opts:
            defaults = self._defaults_map(system)
            printed_any = False

            for key in sorted(ctx.config.keys()):
                current_value = ctx.config.get(key)
                default_value = defaults.get(key, None)

                if key in defaults and current_value == default_value:
                    continue

                source = (
                    "file:" + ",".join(map(str, ctx.arg_lines.get(key, [])))
                    if key in ctx.arg_lines
                    else "config/default"
                )
                lines.append(
                    f"* {key} = {current_value} (default={default_value}, src={source})\n"
                )
                printed_any = True

            if not printed_any:
                lines.append("* (no differences from defaults)\n")

            lines.append("\n")

        if "event" in selected_opts:
            event = system.event
            lines.append(f"* type: {getattr(event, 'event_type', None)}\n")
            lines.append(f"* is_directory: {getattr(event, 'is_directory', None)}\n")
            lines.append(f"* src_path: {getattr(event, 'src_path', None)}\n")
            lines.append(f"* dest_path: {getattr(event, 'dest_path', None)}\n")
            lines.append(f"* ctx.path: {path}\n")
            lines.append("\n")

        return lines

    def _apply(self, *, ctx: Context, system: System) -> Optional[IgnoreMap]:
        line_to_opts: dict[int, set[str]] = {}
        line_to_remove_flags: dict[int, List[str]] = {}
        line_to_man_requests: dict[int, List[str]] = {}

        def add_option(lineno_1based: int, option_name: str, remove_flag: str) -> None:
            line_to_opts.setdefault(lineno_1based, set()).add(option_name)
            line_to_remove_flags.setdefault(lineno_1based, []).append(remove_flag)

        if ctx.config.get("mods"):
            for lineno_1based in ctx.arg_lines.get("mods") or []:
                add_option(int(lineno_1based), "mods", "--mods")

        if ctx.config.get("config"):
            for lineno_1based in ctx.arg_lines.get("config") or []:
                add_option(int(lineno_1based), "config", "--config")

        if ctx.config.get("help"):
            for lineno_1based in ctx.arg_lines.get("help") or []:
                add_option(int(lineno_1based), "help", "--help")

        if ctx.config.get("sys_event"):
            for lineno_1based in ctx.arg_lines.get("sys_event") or []:
                add_option(int(lineno_1based), "event", "--sys-event")

        man_values = ctx.config.get("man") or []
        man_lines = ctx.arg_lines.get("man") or []
        if isinstance(man_values, list) and isinstance(man_lines, list):
            for man_value, lineno_1based in zip(man_values, man_lines):
                lineno_int = int(lineno_1based)
                add_option(lineno_int, "man", "--man")
                if man_value is not None and str(man_value).strip():
                    line_to_man_requests.setdefault(lineno_int, []).append(
                        str(man_value).strip()
                    )

        if not line_to_opts:
            return None

        try:
            with open(ctx.path, "r", encoding="utf-8") as file_handle:
                file_lines = file_handle.readlines()
        except FileNotFoundError:
            file_lines = []

        if not file_lines:
            file_lines = ["\n"]

        for lineno_1based in sorted(line_to_opts.keys(), reverse=True):
            index = max(0, min(len(file_lines) - 1, lineno_1based - 1))
            selected_opts = line_to_opts[lineno_1based]
            remove_flags = line_to_remove_flags[lineno_1based]
            man_requests = line_to_man_requests.get(lineno_1based, [])

            block = self._build_block(
                system=system,
                ctx=ctx,
                selected_opts=selected_opts,
                path=ctx.path,
                man_requests=man_requests,
            )

            if index == 0:
                cleaned_first_line = delete_args_from_string(
                    file_lines[0], remove_flags
                )
                if cleaned_first_line.strip() == "":
                    file_lines[0:1] = block
                    continue

                file_lines[0] = cleaned_first_line

                if file_lines[0].strip():
                    file_lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    file_lines[0] = "\n"
                    insert_pos = 1

                file_lines[insert_pos:insert_pos] = ["---\n"] + block
                continue

            cleaned_line = delete_args_from_string(file_lines[index], remove_flags)
            file_lines[index : index + 1] = block
            if cleaned_line.strip():
                insert_at = index + len(block)
                file_lines[insert_at:insert_at] = [cleaned_line]

        with open(ctx.path, "w", encoding="utf-8") as file_handle:
            file_handle.writelines(file_lines)

        return {ctx.path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(ctx=ctx, system=system)
