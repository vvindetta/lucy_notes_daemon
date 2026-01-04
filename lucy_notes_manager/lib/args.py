import argparse
import logging
import shlex
import sys
from collections.abc import Iterable
from typing import Any, Dict, List, Tuple, Union

logger = logging.getLogger(__name__)


# Args = List[List[Dict[str, Any]], int]

Template = List[
    Union[
        Tuple[str, type],
        Tuple[str, type, Any],
    ]
]


def parse_args(
    args: List[str],
    template: Template,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Parse a list of CLI-style arguments using a dynamic template.

    template example:
        [
            ("--rename", str),
            ("--banner", str, ["date"]),
        ]

    Returns:
        (known_args_dict, unknown_args_list)
    """
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)

    for item in template:
        if len(item) == 2:
            flag, typ = item
            default = None  # if not provided, argparse will use None
        else:
            flag, typ, default = item

        dest = flag.lstrip("-").replace("-", "_")

        kwargs: Dict[str, Any] = {
            "dest": dest,
            "type": typ,
            "nargs": "+",
        }

        # Only set default if user provided it in the template item
        if len(item) == 3:
            kwargs["default"] = default

        parser.add_argument(flag, **kwargs)

    try:
        namespace, unknown_args = parser.parse_known_args(args)
    except SystemExit:
        return {}, args

    known_args = vars(namespace)
    return known_args, unknown_args


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


def merge_args(args: Dict[str, Any], overwrite_args: Dict[str, Any]) -> Dict[str, Any]:
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


def setup_args(
    template: Template,
    default_config_path: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    High-level helper:

    1. Parse startup (CLI) arguments from sys.argv[1:].
    2. Determine config path (CLI 'config_path' or default_config_path).
    3. Try to read and parse config file.
    4. Merge config args with CLI args (CLI wins).
    5. Return (known_args_dict, unknown_args_list).
    """
    # 1. Parse CLI args
    known_startup_args, unknown_startup_args = parse_args(
        template=template,
        args=sys.argv[1:],
    )

    # 2. Decide config path
    config_path = known_startup_args.get("config_path") or default_config_path

    try:
        # 3. Parse config-file args
        known_config_args, unknown_config_args = get_config_args(
            path=config_path,
            template=template,
        )

        # 4. Merge known; concat unknown (config first, then CLI)
        merged_known = merge_args(
            args=known_config_args,
            overwrite_args=known_startup_args,
        )
        merged_unknown = unknown_config_args + unknown_startup_args

        return merged_known, merged_unknown

    except FileNotFoundError:
        logging.basicConfig(level=logging.INFO)
        logging.warning(
            f"Config file {config_path} not found, using only startup arguments",
        )
        return known_startup_args, unknown_startup_args


def get_args_from_file(
    path: str,
    template: Template,
    only_first_line: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Flags are ONLY like: --something

    - only_first_line=True  -> parse ONLY the first line
    - only_first_line=False -> parse ALL lines (whole file)

    Notes:
    - To avoid parsing normal text, this parses only lines where the first
      non-space characters start with "--".
    - Merges repeated flags by extending lists (nargs="+").
    """
    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        logger.info(f"File: {path} not found.")
        return {}, []

    if not lines:
        return {}, []

    scan_lines = [lines[0]] if only_first_line else lines

    merged_known: Dict[str, Any] = {}
    merged_unknown: List[str] = []

    for raw_line in scan_lines:
        line = raw_line.strip()

        # skip empty / comment lines
        if not line or line.startswith("#"):
            continue

        # args-lines start ONLY with "--"
        if not line.lstrip().startswith("--"):
            continue

        try:
            tokens = shlex.split(line, comments=False, posix=True)
        except ValueError as e:
            logger.debug(f"shlex.split failed for line in {path}: {e}")
            continue

        # inline: extract only "--flag + values until next --flag"
        cli_tokens: List[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]

            is_flag = tok.startswith("--") and len(tok) > 2
            if not is_flag:
                i += 1
                continue

            cli_tokens.append(tok)
            i += 1

            while i < len(tokens):
                nxt = tokens[i]
                nxt_is_flag = nxt.startswith("--") and len(nxt) > 2
                if nxt_is_flag:
                    break
                cli_tokens.append(nxt)
                i += 1

        if not cli_tokens:
            continue

        line_known, line_unknown = parse_args(template=template, args=cli_tokens)

        # merge known (nargs="+": list values)
        for key, value in line_known.items():
            if value in (None, ""):
                continue

            if key not in merged_known or merged_known[key] in (None, ""):
                merged_known[key] = value
                continue

            if isinstance(merged_known[key], list) and isinstance(value, list):
                merged_known[key].extend(value)
            else:
                merged_known[key] = value

        merged_unknown.extend(line_unknown)

    return merged_known, merged_unknown


def clean_args_from_line(line: str, flags: Iterable[str]) -> str:
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
