"""
Print a pip constraints file that pins every runtime dependency to its declared minimum.

CI installs with it on the oldest supported Python to prove the lower bounds in
pyproject.toml really work (no tool beyond pip is needed):

    python scripts/min_constraints.py > min.txt
    python -m pip install -c min.txt -e ".[dev]"
    python scripts/min_constraints.py --names   # just the runtime package names

Dev tools (pytest, ruff, mypy, ...) are left unpinned, except PINNED_DEV; only what users install is tested
at its floor. Needs Python 3.11+ (tomllib) or the `tomli` package to run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

RUNTIME_EXTRAS = ("api", "charts", "yfinance")
# Dev packages that must move in step with a runtime floor (old starlette TestClient needs old httpx).
PINNED_DEV = ("httpx",)
_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;]*)(;.*)?$")


def minimum_pins(pyproject: Path) -> list[str]:
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    reqs = list(project.get("dependencies", []))
    for extra in RUNTIME_EXTRAS:
        reqs += project.get("optional-dependencies", {}).get(extra, [])
    dev = project.get("optional-dependencies", {}).get("dev", [])
    reqs += [r for r in dev if (m := _REQ.match(r)) and m.group(1).lower() in PINNED_DEV]
    pins = []
    for req in reqs:
        m = _REQ.match(req)
        if not m:
            continue
        name, spec, marker = m.group(1), m.group(2), m.group(3) or ""
        floor = re.search(r">=\s*([^,\s]+)", spec)
        if floor:
            pins.append(f"{name}=={floor.group(1)}{marker}")
    return pins


def runtime_names(pyproject: Path) -> list[str]:
    """Names of the runtime dependencies (no dev tools, no version-specific backports)."""
    return [p.split("==")[0] for p in minimum_pins(pyproject) if ";" not in p and p.split("==")[0] not in PINNED_DEV]


if __name__ == "__main__":
    args = sys.argv[1:]
    names_only = "--names" in args
    args = [a for a in args if a != "--names"]
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    print(" ".join(runtime_names(pyproject)) if names_only else "\n".join(minimum_pins(pyproject)))
