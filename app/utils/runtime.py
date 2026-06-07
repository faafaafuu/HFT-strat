from __future__ import annotations

import asyncio
import os
from pathlib import Path


def memory_usage_mb() -> float:
    statm = "/proc/self/statm"
    try:
        pages = int(Path(statm).read_text(encoding="utf-8").split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024
    except (OSError, IndexError, ValueError):
        return 0.0


def active_task_count() -> int:
    try:
        return sum(1 for task in asyncio.all_tasks() if not task.done())
    except RuntimeError:
        return 0
