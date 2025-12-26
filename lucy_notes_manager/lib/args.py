import argparse
import logging
import shlex
import sys
from typing import Any, Dict, List, Tuple

Template = Tuple[Tuple[str, type], ...]

logger = logging.getLogger(__name__)


def parse_args(
    template: Template,
    args: List[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Parse a list of CLI-style arguments using a dynamic template.

    template example:
        (
            ("-r", str),
            ("-banner", str),
        )

    args example:
        ["-r", "newname.md"]

    Returns:
        (known_args_dict, unknown_args_list)
    """
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)

    for flag, typ in template:
        dest = flag.lstrip("-").replace("-", "_")
        parser.add_argument(flag, dest=dest, type=typ, nargs="+")

    try:
        # args = split_equals_tokens(args)
        namespace, unknown_args = parser.parse_known_args(args)
    except SystemExit:
        # if parsing fails, treat everything as unknown
        return {}, args

    known_args = vars(namespace)  # Namespace -> dict
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


def get_args_from_first_file_line(
    path: str,
    template: Template,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Read args from the first non-empty, non-comment line.

    Returns:
        (known_args_dict, unknown_args_list)
    """
    try:
        with open(path, "r", encoding="utf-8") as file:
            first_line = file.readline().strip()
    except FileNotFoundError:
        logger.info(f"File: {path} not found.")
        return {}, []

    if not first_line or first_line.startswith("#"):
        return {}, []

    tokens = shlex.split(first_line)

    known_args, unknown_args = parse_args(
        template=template,
        args=tokens,
    )

    return known_args, unknown_args


def clean_args_from_line(first_line: str, flags):
    tokens = shlex.split(first_line)

    new_tokens = [
        token for token in tokens if not any(token.startswith(flag) for flag in flags)
    ]

    return " ".join(new_tokens)
