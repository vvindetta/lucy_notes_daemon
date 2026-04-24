from __future__ import annotations

from lucy_notes_manager.lib.args import Template

DEFAULT_COMMIT_MESSAGE: str = "Auto-commit"
DEFAULT_TIMESTAMP_FORMAT: str = "%Y-%m-%d_%H-%M-%S"

GIT_TEMPLATE: Template = [
    (
        "--git-msg",
        str,
        DEFAULT_COMMIT_MESSAGE,
        "Base commit message. Example: --git-msg 'Notes update'.",
    ),
    (
        "--git-tsmsg",
        bool,
        False,
        "Append a timestamp to the commit message. Example: --git-tsmsg true.",
    ),
    (
        "--git-tsfmt",
        str,
        DEFAULT_TIMESTAMP_FORMAT,
        "Timestamp format for --git-tsmsg (Python strftime). Example: --git-tsfmt '%Y-%m-%d %H:%M:%S'.",
    ),
    (
        "--git-key",
        str,
        "",
        "Path to SSH private key for Git operations (no .pub). Used via GIT_SSH_COMMAND. Example: --git-key ~/.ssh/id_ed25519.",
    ),
    (
        "--git-auto-pull",
        bool,
        True,
        "Automatically run 'git pull --no-rebase' when a repo is opened. Never uses rebase or force.",
    ),
    (
        "--git-auto-pull-every-hours",
        float,
        0.0,
        "Run pull-only sync every N hours for active repos. Set 0 to disable (default).",
    ),
    (
        "--git-pull-cooldown-min-sec",
        float,
        10.0,
        "Minimum cooldown (seconds) between auto-pulls triggered by on_opened.",
    ),
    (
        "--git-pull-cooldown-max-sec",
        float,
        200.0,
        "Maximum cooldown cap (seconds). Cooldown progresses (doubles) if on_opened triggers too often.",
    ),
    (
        "--git-auto-merge-on-push",
        bool,
        True,
        "If 'git push' is rejected because the remote is ahead, automatically run 'git pull --no-rebase' (merge) and retry push. No rebase, no force.",
    ),
    (
        "--git-auto-set-upstream",
        bool,
        True,
        "If the current branch has no upstream, try to set it to <remote>/<branch> (prefer remote 'origin') when that remote branch exists.",
    ),
    (
        "--git-autoresolve",
        str,
        "union",
        "How to auto-resolve merge conflicts during auto-merge: "
        "'none' (do not resolve), 'ours' (keep local), 'theirs' (keep remote), 'union' (keep both sides, remove markers).",
    ),
    (
        "--git-debounce-seconds",
        float,
        0.8,
        "Debounce window in seconds: group file events and commit/push once after changes calm down.",
    ),
    (
        "--git-timeout-sec",
        float,
        8.0,
        "Timeout (seconds) for git add/status/commit operations.",
    ),
    (
        "--git-pull-timeout-sec",
        float,
        30.0,
        "Timeout (seconds) for git pull (merge). Increase for slow networks or large repos.",
    ),
    ("--git-push-timeout-sec", float, 20.0, "Timeout (seconds) for git push."),
    (
        "--git-push-backoff-start-sec",
        float,
        5.0,
        "Initial backoff (seconds) before retrying push after a failure.",
    ),
    (
        "--git-push-backoff-max-sec",
        float,
        120.0,
        "Maximum backoff (seconds) cap for repeated push failures.",
    ),
]
