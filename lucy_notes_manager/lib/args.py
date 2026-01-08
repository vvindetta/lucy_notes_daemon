import argparse
import logging
import shlex
import sys
from collections.abc import Iterable
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


"""
Template example:
    [
        ("--rename", str, None, "Will rename file),
        ("--banner", str, ["date"], "Draws ASCII banner),
    ]
"""
Template = List[Tuple[str, type, Any, str]]

ArgLines = Dict[str, List[int]]


def parse_args(args: list[str], template: Template) -> tuple[dict[str, Any], list[str]]:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)

    for flag, typ, default, desc in template:
        dest = flag.lstrip("-").replace("-", "_")

        if typ is bool:
            parser.add_argument(
                flag,
                dest=dest,
                action="store_true",  # --flag -> True
                default=default,  # missing -> default (usually False)
            )
        else:
            parser.add_argument(
                flag,
                dest=dest,
                type=typ,
                nargs="+",
                default=default,
            )

    try:
        namespace, unknown_args = parser.parse_known_args(args)
    except SystemExit:
        return {}, args

    return vars(namespace), unknown_args


def get_config_args(path: str, template: Template) -> Tuple[Dict[str, Any], List[str]]:
    """
    Read arguments from a config file and parse them with the same template.
    """
    config_args_raw: List[str] = []

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            config_args_raw.extend(shlex.split(line))

    return parse_args(template=template, args=config_args_raw)


def merge_known_args(
    args: Dict[str, Any], overwrite_args: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge two dicts of parsed arguments.

    - 'args' usually comes from config file (defaults).
    - 'overwrite_args' usually comes from CLI.

    Rules:
        - None or "" in overwrite_args = "not provided", do NOT overwrite.
        - Everything else in overwrite_args overrides args.
    """
    merged_args = dict(args)
    for key, value in overwrite_args.items():
        if value not in (None, ""):
            merged_args[key] = value
    return merged_args


def setup_config_and_cli_args(
    template: Template,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    High-level helper:

    1. Parse startup (CLI) arguments from sys.argv[1:].
    2. Try to read config file.
    3. Merge config and CLI args (CLI wins).
    4. Return (known_args_dict, unknown_args_list).
    """
    # 1. Parse CLI args
    known_startup_args, unknown_startup_args = parse_args(
        template=template,
        args=sys.argv[1:],
    )

    # 2. Parse config-file args
    try:
        known_config_args, unknown_config_args = get_config_args(
            path=known_startup_args["sys_config_path"][0],
            template=template,
        )
    except FileNotFoundError:
        logging.basicConfig(level=logging.INFO)
        logging.warning(
            f"Config file {known_startup_args['sys_config_path']} not found, using only startup arguments",
        )
        return known_startup_args, unknown_startup_args

    # 3. Merge: CLI first, than config
    merged_known = merge_known_args(
        args=known_config_args,
        overwrite_args=known_startup_args,
    )
    merged_unknown = unknown_config_args + unknown_startup_args

    return merged_known, merged_unknown


def get_args_from_file(
    path: str,
    template: Template,
    only_first_line: bool = False,
) -> Tuple[Dict[str, Any], List[str], ArgLines]:
    """
    Reads args from file, accepting only lines that begin with a valid flag:
      --<name>   (where <name> starts with a letter)
    Rejects:
      -- text
      ---text
      ---
      text --flag
    """

    def is_valid_flag_token(token: str) -> bool:
        if not token.startswith("--"):
            return False
        head = token.split("=", 1)[0]  # allow --flag=value
        if len(head) < 3 or not head[2].isalpha():  # must start with letter
            return False
        for ch in head[3:]:
            if not (ch.isalnum() or ch in ("_", "-")):
                return False
        return True

    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        logger.info(f"File: {path} not found.")
        return {}, [], {}

    if not lines:
        return {}, [], {}

    merged_known: Dict[str, Any] = {}
    merged_unknown: List[str] = []
    arg_lines: ArgLines = {"__unknown__": []}

    for lineno, raw_line in enumerate(lines, start=1):
        if only_first_line and lineno > 1:
            break

        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # line must start with a valid flag token
        start = stripped.split()[0]
        if not is_valid_flag_token(start):
            continue

        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
        except ValueError as e:
            logger.debug(f"shlex.split failed for line {lineno} in {path}: {e}")
            continue

        # collect "--flag" + values until next flag
        cli_tokens: List[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if not is_valid_flag_token(tok):
                i += 1
                continue

            cli_tokens.append(tok)
            i += 1
            while i < len(tokens):
                nxt = tokens[i]
                if is_valid_flag_token(nxt):
                    break
                cli_tokens.append(nxt)
                i += 1

        if not cli_tokens:
            continue

        line_known, line_unknown = parse_args(template=template, args=cli_tokens)

        if line_unknown:
            merged_unknown.extend(line_unknown)
            arg_lines["__unknown__"].extend([lineno] * len(line_unknown))

        for key, value in line_known.items():
            if value in (None, ""):
                continue

            count = len(value) if isinstance(value, list) else 1
            arg_lines.setdefault(key, []).extend([lineno] * count)

            if key not in merged_known or merged_known[key] in (None, ""):
                merged_known[key] = value
                continue

            if isinstance(merged_known[key], list) and isinstance(value, list):
                merged_known[key].extend(value)
            else:
                merged_known[key] = value

    return merged_known, merged_unknown, arg_lines


def delete_args_from_string(line: str, flags: Iterable[str]) -> str:
    """
    Remove flags and their values from a single line.

    - flags: flags to remove (e.g. ["--banner"])
    - If removed flag is in form "--flag=value" -> removed fully.
    - If removed flag is in form "--flag" -> removes ALL following value tokens
      until the next flag-like token (greedy).
    - Preserves trailing newline automatically.

    Heuristic for "flag-like token":
      --something  -> flag
      -s           -> flag
      but NOT negative numbers like -1, -2.5
    """

    def looks_like_flag(token: str) -> bool:
        if token.startswith("--") and len(token) > 2:
            return True
        if token.startswith("-") and len(token) > 1:
            return not (token[1].isdigit() or token[1] == ".")
        return False

    newline = "\n" if line.endswith("\n") else ""
    raw = line[:-1] if newline else line

    tokens = shlex.split(raw)
    remove = set(flags)

    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # handle --flag=value by checking only the head part
        head = tok.split("=", 1)[0] if tok.startswith("-") else tok

        if head in remove:
            i += 1

            # "--flag=value" -> already contains value, nothing else to consume
            if "=" in tok:
                continue

            # consume value tokens until next flag-like token
            while i < len(tokens) and not looks_like_flag(tokens[i]):
                i += 1
            continue

        out.append(tok)
        i += 1

    return (shlex.join(out) if out else "") + newline
