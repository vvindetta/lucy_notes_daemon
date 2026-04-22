from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class _RepoBatch:
    repo_root: str
    base_message: str
    add_timestamp_to_message: bool
    timestamp_format: str
    environment: Dict[str, str]

    debounce_seconds: float
    git_timeout_seconds: float
    pull_timeout_seconds: float
    push_timeout_seconds: float
    backoff_start_seconds: float
    backoff_max_seconds: float

    pull_cooldown_min_seconds: float
    pull_cooldown_max_seconds: float

    wants_pull: bool = False
    auto_merge_on_push: bool = True
    auto_set_upstream: bool = True
    autoresolve_mode: str = "union"  # none|ours|theirs|union

    last_event_at: float = field(default_factory=time.time)
    event_types: set[str] = field(default_factory=set)
    hinted_paths: set[str] = field(default_factory=set)
