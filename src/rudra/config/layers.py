"""The five configuration layers (TODO.md C2.1).

Each reader returns a plain nested dict in the same shape. None of them
knows about precedence — that lives entirely in `loader.deep_merge`, which
is the point: two of this project's three config defects (A5.1, A5.2) were
"which source won?" questions that per-field resolver chains could not
answer.
"""

from __future__ import annotations

import copy
import os
import tomllib
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rudra.config.schema import DEFAULTS, MODEL_KEYS
from rudra.state.paths import rudra_paths

LAYER_NAMES = ("builtin", "user", "project", "env", "cli")

# OLLAMA_* -> the RUDRA_* name that replaced it (C1.3, kept one more release
# by S6.4). VERBOSE joins them per A1.42.
_DEPRECATED_VARS = {
    "OLLAMA_BASE_URL": "RUDRA_BASE_URL",
    "OLLAMA_MODEL": "RUDRA_MODEL",
    "OLLAMA_MODEL_PLANNER": "RUDRA_PLANNER_MODEL",
    "OLLAMA_MODEL_CODER": "RUDRA_CODER_MODEL",
    "OLLAMA_TEMPERATURE": "RUDRA_TEMPERATURE",
    "OLLAMA_TIMEOUT": "RUDRA_TIMEOUT",
    "OLLAMA_NUM_PREDICT": "RUDRA_MAX_OUTPUT_TOKENS",
    "VERBOSE": "RUDRA_VERBOSE",
}

# Environment variables under the RUDRA_ prefix that are not configuration.
_NON_CONFIG_VARS = frozenset({"RUDRA_LIVE_TESTS"})

_NUMERIC_MODEL_KEYS = frozenset({"temperature", "context_tokens", "max_output_tokens", "timeout"})

_warned_vars: set[str] = set()
"""Deduped explicitly rather than via the warnings module's per-location
filter, so "warns once per variable" is deterministically testable."""


def _reset_deprecation_warnings() -> None:
    """Test support: let a later test observe the first warning again."""
    _warned_vars.clear()


class ConfigError(Exception):
    """A configuration file is unreadable, malformed, or invalid.

    Always names the offending file, and the line where the source gives one.
    """


def _legacy(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    if name not in _warned_vars:
        _warned_vars.add(name)
        warnings.warn(
            f"{name} is deprecated; use {_DEPRECATED_VARS[name]} instead. "
            f"It will be removed in the next release.",
            DeprecationWarning,
            stacklevel=4,
        )
    return value


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() not in {"false", "0", "no", "off", ""}


def _as_number(raw: str) -> Any:
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def builtin_layer() -> dict[str, Any]:
    """Layer 1. A deep copy — callers merge into their result in place."""
    return copy.deepcopy(DEFAULTS)


def user_toml_path() -> Path:
    """Layer 2's location: `$XDG_CONFIG_HOME/rudra/`, else `~/.config/rudra/`."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "rudra" / "config.toml"


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file. Absent is `{}`; unreadable or malformed raises.

    A file that exists but cannot be read is never silently skipped — that
    would hand the user a config which looks applied and is not.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML — {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read — {exc}") from exc


def user_toml_layer() -> dict[str, Any]:
    """Layer 2."""
    return read_toml(user_toml_path())


def project_toml_layer(project_root: Path) -> dict[str, Any]:
    """Layer 3."""
    return read_toml(rudra_paths(project_root).config_toml)


def _split_role_and_suffix(tail: str, known_roles: Iterable[str]) -> tuple[str, str]:
    """Map `PLANNER_MODEL` -> ("planner", "model"), `MODEL` -> ("default", "model").

    Roles are matched first, which is what the pre-Step-6 resolver did.
    Ambiguity is real: `RUDRA_PLANNER_MODEL` could be a suffix named
    `PLANNER_MODEL`, and only the known-role list settles it.
    """
    lowered = tail.lower()
    for role in known_roles:
        prefix = f"{role}_"
        if role != "default" and lowered.startswith(prefix):
            return role, lowered[len(prefix) :]
    return "default", lowered


def env_layer(known_roles: Iterable[str]) -> dict[str, Any]:
    """Layer 4: `RUDRA_*`, plus the deprecated shims.

    `known_roles` is the builtin set union whatever roles the merged TOML
    declared, so `[model.reviewer]` makes `RUDRA_REVIEWER_MODEL` work with no
    code change here.
    """
    roles = tuple(known_roles)
    layer: dict[str, Any] = {}

    def put(section: str, *path: str, value: Any) -> None:
        node = layer.setdefault(section, {})
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    for name, raw in os.environ.items():
        if not name.startswith("RUDRA_") or name in _NON_CONFIG_VARS:
            continue
        tail = name[len("RUDRA_") :]
        if tail == "VERBOSE":
            put("agent", "verbose", value=_as_bool(raw))
            continue
        if tail == "PERMISSIONS_MODE":
            put("permissions", "mode", value=raw.strip())
            continue
        # Before _split_role_and_suffix, which would otherwise read SHELL as
        # a role-and-suffix pair.
        if tail == "SHELL":
            put("tools", "shell", value=_as_bool(raw))
            continue
        if tail == "SHELL_IN_AUTO":
            put("tools", "shell_in_auto", value=_as_bool(raw))
            continue
        role, suffix = _split_role_and_suffix(tail, roles)
        if suffix in MODEL_KEYS:
            value: Any = _as_number(raw) if suffix in _NUMERIC_MODEL_KEYS else raw
            put("model", role, suffix, value=value)

    # Deprecated shims, applied only where the modern name did not already win.
    legacy_model = _legacy("OLLAMA_MODEL")
    legacy_pairs: list[tuple[str, str, str | None]] = [
        ("default", "base_url", _legacy("OLLAMA_BASE_URL")),
        ("default", "model", legacy_model),
        ("planner", "model", _legacy("OLLAMA_MODEL_PLANNER") or legacy_model),
        ("coder", "model", _legacy("OLLAMA_MODEL_CODER") or legacy_model),
        ("default", "temperature", _legacy("OLLAMA_TEMPERATURE")),
        ("default", "timeout", _legacy("OLLAMA_TIMEOUT")),
        ("default", "max_output_tokens", _legacy("OLLAMA_NUM_PREDICT")),
    ]
    for role, key, raw_value in legacy_pairs:
        if raw_value is None:
            continue
        if layer.get("model", {}).get(role, {}).get(key) is not None:
            continue
        put(
            "model",
            role,
            key,
            value=_as_number(raw_value) if key in _NUMERIC_MODEL_KEYS else raw_value,
        )

    if "RUDRA_VERBOSE" not in os.environ:
        legacy_verbose = _legacy("VERBOSE")
        if legacy_verbose is not None:
            put("agent", "verbose", value=_as_bool(legacy_verbose))

    return layer


def cli_layer(
    verbose: bool | None,
    permission_mode: str | None,
    allow_shell: bool | None = None,
) -> dict[str, Any]:
    """Layer 5. Only flags the user actually passed appear."""
    layer: dict[str, Any] = {}
    if verbose is not None:
        layer["agent"] = {"verbose": verbose}
    if permission_mode is not None:
        layer["permissions"] = {"mode": permission_mode}
    if allow_shell is not None:
        layer["tools"] = {"shell_in_auto": allow_shell}
    return layer


__all__ = [
    "LAYER_NAMES",
    "ConfigError",
    "builtin_layer",
    "cli_layer",
    "env_layer",
    "project_toml_layer",
    "read_toml",
    "user_toml_layer",
    "user_toml_path",
]
