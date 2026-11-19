# Compatibility

The toolkit is not tied to one Python release, one dependency version or one operating system.

## Python versions

**Supported: Python 3.9 and every newer release.** `requires-python = ">=3.9"` has no upper
bound, so a new Python installs as soon as the dependencies publish wheels for it.

| Tested in CI | How it adapts |
|---|---|
| 3.9 (oldest) through 3.14, on Linux | Explicit versions in the matrix |
| `3.x` on Linux, macOS and Windows | Always the newest stable Python; no edit needed when 3.15, 3.16, ... ship |
| `3.14t` (free-threaded, no GIL) | Exercises the build without the global interpreter lock |
| Newest Python with pre-release dependencies, weekly | Catches upstream breakage before a release reaches users |

Why 3.9 is the floor: it is the oldest Python that the core dependencies (pandas 2.2,
numpy 1.26, pydantic 2) still support, and it is the system `python3` on current macOS.
Python 3.8 and earlier lack the libraries' wheels.

How the code stays portable:

- Every module starts with `from __future__ import annotations` (enforced by ruff), so modern
  annotations such as `float | None` are never evaluated at import time.
- pydantic evaluates annotations anyway; on 3.9 the `eval_type_backport` package (installed
  automatically there only) handles the newer syntax.
- Typer reads CLI signatures at runtime with the standard library, so CLI parameters use
  `Optional[...]`.
- Version-specific APIs go through small shims (`utils/plugins.py` for entry points).

## Dependency versions

Dependencies declare minimums only (`pandas>=2.2`, ...), never caps, so upgrades flow in.
Two CI jobs keep both ends honest:

- **lowest-deps**: Python 3.9 with every dependency pinned to its declared floor
  (`python scripts/min_constraints.py > min.txt && pip install -c min.txt -e ".[dev]"`).
- **latest-deps**: the newest Python with `pip install --upgrade --pre`.

Raise a floor in `pyproject.toml` only when the code needs a newer feature; the
lowest-deps job then proves the new floor.

## Operating systems and runtimes

Linux, macOS and Windows are tested on every push. No step needs a shell script, make
or a container; see [Deployment](deployment.md) for running it as a service anywhere.
To pin the Python version in the container image:
`docker build --build-arg PYTHON_VERSION=3.12 -t financial-model .` (default: newest 3.x).
