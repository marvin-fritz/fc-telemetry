"""Versionskennung des laufenden Dienstes: Env, sonst Git-SHA, sonst Default."""

from __future__ import annotations

import os
import subprocess


def detect_version(default: str = "unknown", cwd: str | None = None) -> str:
    env = os.environ.get("FC_SERVICE_VERSION")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=2, check=False
        )
        sha = out.stdout.strip()
        if out.returncode == 0 and sha:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return default
