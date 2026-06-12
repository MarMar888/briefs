"""
Single source of truth for the pipeline version.

The semver lives in the `VERSION` file at the repo root (the Python equivalent of
npm's package version — bump it when the pipeline's behavior changes). Every domain
row records which pipeline version found, classified, and enriched it, and every
pipeline run records the version it ran under, so we can trace any lead back to the
exact code that produced it.

Resolution order:
  1. PIPELINE_VERSION env var (full override — e.g. a release tag).
  2. VERSION file + short git sha as build metadata, e.g. "0.1.0+a1b2c3d".
     The sha comes from GITHUB_SHA in CI, or `git rev-parse` locally.

Usage:
    from version import get_version
    get_version()  # -> "0.1.0+a1b2c3d"
"""

import os
import subprocess
from functools import lru_cache

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def _read_version_file() -> str:
    try:
        with open(_VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:7]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the current pipeline version string (cached per process)."""
    override = os.environ.get("PIPELINE_VERSION")
    if override:
        return override.strip()

    base = _read_version_file() or "0.0.0"
    sha = _git_sha()
    return f"{base}+{sha}" if sha else base


if __name__ == "__main__":
    print(get_version())
