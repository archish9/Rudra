"""Where Rudra's own Python lives, so nothing else mistakes it for the user's.

ONE ANSWER, TWO CONSUMERS
-------------------------
Rudra is a Python program and so, often, is the project it is working on.
D18 forbids conflating the two, and until OPEN-80 that rule was enforced in
exactly one place, by NOT calling ``sys.executable``::

    stacks/detect.py::_system_interpreter
        "Deliberately NOT `sys.executable`. D18 forbids conflating Rudra's
         own interpreter with the target project's."

It then asked ``shutil.which("python3")``.  Rudra runs from its own
virtualenv, so that directory is FIRST on ``PATH`` and the answer was Rudra's
interpreter anyway.  **Avoiding `sys.executable` does not avoid the
interpreter `sys.executable` names.**

Run ``689f0ea263be`` ran a user's FastAPI suite under
``<rudra>/.venv/bin/python3`` -- against a stale ``sqlalchemy`` that no lock
file names and that was installed without its ``[asyncio]`` extra, so all 50
failures were ``No module named 'greenlet'``.  The coder read that as a bug in
its own code, and spent three attempts and 3,792 seconds -- 54% of the run's
machine time -- writing fixes for a failure no code it could write would ever
reach.

The same ``PATH`` made ``pip install`` mean "install into Rudra".  Nine
distributions from an August Flask run were still in Rudra's ``.venv`` on
2026-09-02 -- flask, flask_sqlalchemy, sqlalchemy, werkzeug, jinja2,
markupsafe, itsdangerous, blinker and pip -- every one recording ``pip`` as
its installer, where everything ``uv sync`` writes records ``uv``.

So two subsystems need the same fact: ``stacks/detect.py`` when it picks an
interpreter to REPORT, and ``permissions/env.py`` when it builds the
environment an agent's shell RUNS in.  It is written here once, for the reason
``verify/stubs.py::is_build_output`` is written once -- that rule had three
copies, one drifted, and the two directions of the drift cost a different half
of the userbase (OPEN-63, OPEN-64).

WHEN THIS IS A NO-OP, AND WHY THAT MATTERS
------------------------------------------
Everything here is inert unless Rudra is running from a virtualenv
(``sys.prefix != sys.base_prefix`` -- the standard test, and true of
``uv sync``, ``uv tool install`` and ``pipx`` alike).

That guard is not defensive padding.  Installed as a distribution package,
Rudra's interpreter is ``/usr/bin/python3`` and its "own bin directory" is
``/usr/bin`` -- so an unguarded version of this module would strip ``/usr/bin``
from the environment of every command an agent runs, which breaks the machine
rather than protecting it.  There is also nothing to protect: with no separate
environment, the system interpreter *is* Rudra's, no ``pip install`` can
contaminate a venv that does not exist, and D18 cannot be enforced by path at
all.  Saying so plainly beats enforcing a rule that has stopped meaning
anything.

WHY BOTH SPELLINGS, AND WHY NEVER THE TARGET
--------------------------------------------
``_bin_dir_spellings`` reports the directory as ``sys.executable`` gives it
AND as it resolves, because on macOS a path behind a symlink and its resolved
form differ, and a miss reads as "not Rudra".  Same idiom and same A1.58 as
``virtual_paths._posix_root_spellings``.

``is_own_interpreter`` compares a candidate's own PARENT directory and never
follows the candidate itself.  A virtualenv's ``python3`` is usually a symlink
to the base interpreter it was built from, so resolving it lands in
``/usr/bin`` and reports Rudra's own binary as somebody else's.

NOT A SANDBOX
-------------
This removes exactly one environment: Rudra's.  Every other venv, nvm, rustup
and pyenv path survives, because ``permissions/env.py`` exists to keep them --
``LocalShellBackend`` defaults to an EMPTY environment, and A1.44 is what that
default cost.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def running_in_own_virtualenv() -> bool:
    """Does Rudra have an environment of its own to keep the user out of?"""
    return sys.prefix != sys.base_prefix


def _spellings(path: Path) -> frozenset[str]:
    """`path` as given and as it resolves, both POSIX-style (A1.58)."""
    found = {path.as_posix()}
    try:
        found.add(path.resolve().as_posix())
    except (OSError, RuntimeError):  # pragma: no cover - unresolvable path
        pass
    return frozenset(found)


def _bin_dir_spellings() -> frozenset[str]:
    """Every spelling of the directory holding Rudra's own interpreter."""
    if not running_in_own_virtualenv() or not sys.executable:
        return frozenset()
    return _spellings(Path(sys.executable).parent)


def _prefix_spellings() -> frozenset[str]:
    """Every spelling of Rudra's own virtualenv root."""
    if not running_in_own_virtualenv():
        return frozenset()
    return _spellings(Path(sys.prefix))


def is_own_bin_dir(directory: str | Path) -> bool:
    """Is this the directory Rudra's own interpreter lives in?"""
    own = _bin_dir_spellings()
    return bool(own) and bool(_spellings(Path(directory)) & own)


def is_own_virtualenv(prefix: str | Path) -> bool:
    """Is this Rudra's own virtualenv root, as ``VIRTUAL_ENV`` would name it?"""
    own = _prefix_spellings()
    return bool(own) and bool(_spellings(Path(prefix)) & own)


def is_own_interpreter(executable: str | Path) -> bool:
    """Would running this executable reach Rudra's own Python?

    The candidate's PARENT is the question -- see the module docstring on why
    the candidate itself is never resolved.
    """
    return is_own_bin_dir(Path(executable).parent)


def path_without_own(path_value: str | None) -> str:
    """A ``PATH`` string with Rudra's own bin directory taken out.

    Every other entry survives in its original order and its original
    spelling: this is A1.44's environment minus one directory, not a
    curated one.
    """
    entries = (path_value or "").split(os.pathsep)
    return os.pathsep.join(e for e in entries if e and not is_own_bin_dir(e))


def search_path_without_own() -> str | None:
    """The live ``PATH``, minus Rudra's own bin, for ``shutil.which(path=...)``.

    ``None`` means "nothing left to search" -- which ``shutil.which`` would
    read as "use ``os.defpath``", so callers must not pass it through.
    """
    return path_without_own(os.environ.get("PATH")) or None


__all__ = [
    "is_own_bin_dir",
    "is_own_interpreter",
    "is_own_virtualenv",
    "path_without_own",
    "running_in_own_virtualenv",
    "search_path_without_own",
]
