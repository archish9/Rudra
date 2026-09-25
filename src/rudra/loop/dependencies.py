"""Did a gate fail ONLY because a package nobody declared is not installed?

OPEN-161. The coder holds no shell (OPEN-36), so it learns a package is
missing only when the gate's pytest fails to import it -- and pytest stops at
the first failing import of a module, so a project missing packages behind
one another spends a gate per layer. Run `4989aefefacb`'s t1 went stub ->
`No module named 'sqlalchemy'` -> `greenlet` and ended BLOCKED on the third,
having declared sqlalchemy correctly on the second. Every coder in the archive
that was shown a missing package declared it on its next attempt, 4 of 4: what
it could not do is see the next one.

So the loop gives such a gate's attempt back (`loop/engine.py::run_task`), and
this module answers the one question that needs: is EVERY failure in the gate
a missing third-party package? A gate that also failed on the project's own
code is not -- the attempt it followed had real work left, and is charged.

Pure, like `bounds.py` beside it: reads a VerifyReport and a predicate, so a
test can drive every branch with a tail cut from a real run.

Read from pytest's `E` column only, because that is where a runner prints the
exception, and each contiguous run of `E` lines is one exception. A run is a
missing package when it is:

- `ModuleNotFoundError: No module named 'x'` for an `x` that is neither the
  project's own module (OPEN-140's `main` is a path defect, not a
  dependency) nor the standard library; or
- anything carrying a `pip install <x>` line -- FastAPI's `python-multipart`
  is a RuntimeError, and a detector keyed on ImportError misses it; or
- an `ImportError` whose message says something is not installed, which is
  the chained half of greenlet's.

Any other run -- an assertion, an AttributeError, `cannot import name` -- and
the answer is none. So is a truncated tail: a failure it dropped cannot be
shown to be a package.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from rudra.verify.result import FAILED, VerifyReport
from rudra.verify.stubs import source_files

# pytest's exception column: `E` then spaces, or a bare `E` for a blank line.
_E_LINE = re.compile(r"^E(?:\s|$)")
_E_PREFIX = re.compile(r"^E\s*")
_HEADLINE = re.compile(r"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*(?:Error|Exception)):\s*(.*)$")
_NO_MODULE = re.compile(r"No module named '([^']+)'")
# `pip install x`, `pip install 'pydantic[email]'`, `` `pip install x` ``.
_PIP_INSTALL = re.compile(r"pip install\s+['\"`]?([A-Za-z0-9_.\-\[\],]+)")
_NOT_INSTALLED = re.compile(r"\binstall", re.IGNORECASE)
# What `verify/pipeline.py::_tail` and `testing/runner.py::_tail` prepend.
_TRUNCATED = "earlier characters omitted"

_STDLIB = frozenset(sys.stdlib_module_names)


def _exception_runs(tail: str) -> list[list[str]]:
    """Each contiguous run of `E` lines, prefix stripped, blank lines dropped."""
    runs: list[list[str]] = []
    current: list[str] | None = None
    for raw in tail.splitlines():
        line = raw.strip()
        if _E_LINE.match(line):
            if current is None:
                current = []
                runs.append(current)
            text = _E_PREFIX.sub("", line).strip()
            if text:
                current.append(text)
        else:
            current = None
    return [run for run in runs if run]


def _packages_in(run: list[str], is_local: Callable[[str], bool]) -> tuple[str, ...] | None:
    """The packages one exception names, or None when it is not a missing package."""
    headline = _HEADLINE.match(run[0])
    if headline is None:
        return None
    kind, message = headline.groups()
    body = "\n".join(run)
    installs = tuple(_PIP_INSTALL.findall(body))
    if kind == "ModuleNotFoundError":
        missing = _NO_MODULE.search(message)
        if missing is None:
            return None
        top = missing.group(1).split(".")[0]
        if is_local(top) or top in _STDLIB:
            return None
        return (top, *installs)
    if installs:
        return installs
    if kind == "ImportError" and _NOT_INSTALLED.search(message) and "cannot import" not in message:
        return ()
    return None


def missing_packages(report: VerifyReport, is_local: Callable[[str], bool]) -> tuple[str, ...]:
    """The packages this gate says are missing -- empty unless that is ALL it says.

    `is_local(name)` answers whether a top-level module name is the project's
    own; `local_module_names` is the reading the loop uses. Sorted and
    de-duplicated, so the result can be recorded and compared.
    """
    blocker = report.blocker
    if blocker is None or blocker.name != "test" or blocker.outcome != FAILED:
        return ()
    tail = blocker.output_tail
    if _TRUNCATED in tail:
        return ()
    runs = _exception_runs(tail)
    if not runs:
        return ()
    found: set[str] = set()
    for run in runs:
        packages = _packages_in(run, is_local)
        if packages is None:
            return ()
        found.update(packages)
    return tuple(sorted(found))


def local_module_names(project_path: Path) -> frozenset[str]:
    """Every name an `import` could resolve to inside this project.

    Each directory and each `.py` stem on the path to a project source file,
    build output pruned by the one rule the gate and ledger share
    (`verify/stubs.py::is_build_output`, OPEN-64) -- so `.venv` is not the
    project. Deliberately generous: a name the project holds anywhere is read
    as local, because the cost of the wrong answer is asymmetric. Calling a
    package local charges an attempt, as before OPEN-161; calling a path
    defect a package would hand OPEN-140's `No module named 'main'` free
    attempts.
    """
    names: set[str] = set()
    for relative in source_files(Path(project_path)):
        path = PurePosixPath(relative)
        if path.suffix != ".py":
            continue
        names.update(path.parts[:-1])
        names.add(path.stem)
    return frozenset(names)


__all__ = ["local_module_names", "missing_packages"]
