"""Merge the layers, record provenance, validate once, build the dataclasses.

Precedence exists in exactly one place — `deep_merge` — and every value
carries the name of the layer that set it. That is the whole reason this
module replaced a chain of per-field `x or y or default` lookups: A5.1 and
A5.2 were both "which source won?" defects, invisible to the old shape.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rudra.config.layers import (
    ConfigError,
    _reset_deprecation_warnings,
    builtin_layer,
    cli_layer,
    env_layer,
    project_toml_layer,
    read_toml,
    user_toml_path,
)
from rudra.config.schema import (
    BUILTIN_ROLES,
    DISABLEABLE_FLOOR_RULES,
    FLOOR_RULE_NAMES,
    MODEL_KEYS,
    RESERVED_SECTIONS,
    VALID_MODES,
    VALID_PROVIDERS,
    AgentConfig,
    CompatConfig,
    ModelConfig,
    PermissionsConfig,
    ToolsConfig,
)
from rudra.state.paths import rudra_paths

_TOP_LEVEL = ("model", "agent", "permissions", "compat", "tools")
_AGENT_KEYS = frozenset({"verbose"})
_PERMISSION_KEYS = frozenset({"mode", "allow", "deny", "floor_disable"})
_COMPAT_KEYS = frozenset({"task_anchor", "sandbox_paths"})
_TOOLS_BOOL_KEYS = frozenset({"shell", "shell_in_auto", "auto_branch"})
_TOOLS_INT_KEYS = frozenset({"test_timeout"})
_TOOLS_KEYS = _TOOLS_BOOL_KEYS | _TOOLS_INT_KEYS
_POSITIVE_INT_KEYS = ("context_tokens", "max_output_tokens", "timeout")


def deep_merge(layers: list[tuple[str, dict]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge layer dicts in order, per leaf, recording who set what.

    Returns the merged mapping and a dotted-key -> layer-name provenance map.
    Merging per leaf (not per table) is what lets a project override one key
    of `[model.planner]` without erasing its siblings from the user file.
    """
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    def walk(target: dict[str, Any], source: dict[str, Any], layer: str, prefix: str) -> None:
        for key, value in source.items():
            dotted = f"{prefix}{key}"
            if isinstance(value, dict):
                node = target.get(key)
                if not isinstance(node, dict):
                    node = {}
                    target[key] = node
                walk(node, value, layer, f"{dotted}.")
            else:
                target[key] = value
                provenance[dotted] = layer

    for name, layer_dict in layers:
        walk(merged, layer_dict, name, "")
    return merged, provenance


def _where(provenance: dict[str, str], sources: dict[str, Path | None], dotted: str) -> str:
    layer = provenance.get(dotted)
    path = sources.get(layer) if layer else None
    if path:
        return str(path)
    return f"the {layer} layer" if layer else "configuration"


def _suggest(unknown: str, valid: frozenset[str] | tuple[str, ...]) -> str:
    close = difflib.get_close_matches(unknown, sorted(valid), n=1)
    if close:
        return f" Did you mean '{close[0]}'?"
    return f" Valid keys: {', '.join(sorted(valid))}."


def validate(
    merged: dict[str, Any], provenance: dict[str, str], sources: dict[str, Path | None]
) -> None:
    """Reject anything the rest of the program would otherwise misread.

    Unknown keys are fatal because the common case is a typo, and silently
    ignoring one means the user's setting never applies with no signal.
    """
    for section in merged:
        if section in RESERVED_SECTIONS:
            raise ConfigError(f"[{section}] is {RESERVED_SECTIONS[section]}.")
        if section not in _TOP_LEVEL:
            raise ConfigError(f"Unknown section [{section}].{_suggest(section, _TOP_LEVEL)}")

    for role, settings in merged.get("model", {}).items():
        if not isinstance(settings, dict):
            raise ConfigError(
                f"[model.{role}] must be a table of settings, not {type(settings).__name__}."
            )
        for key, value in settings.items():
            dotted = f"model.{role}.{key}"
            if key not in MODEL_KEYS:
                raise ConfigError(
                    f"Unknown key '{key}' in [model.{role}] "
                    f"({_where(provenance, sources, dotted)}).{_suggest(key, MODEL_KEYS)}"
                )
            if key == "provider" and value not in VALID_PROVIDERS:
                raise ConfigError(
                    f"Unknown provider '{value}' in [model.{role}] "
                    f"({_where(provenance, sources, dotted)}). "
                    f"Valid providers: {', '.join(sorted(VALID_PROVIDERS))}."
                )
            if key in _POSITIVE_INT_KEYS and value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ConfigError(
                        f"{key} in [model.{role}] must be a positive integer, got {value!r} "
                        f"({_where(provenance, sources, dotted)})."
                    )
            if key == "temperature" and value is not None:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ConfigError(
                        f"temperature in [model.{role}] must be a number, got {value!r}."
                    )

    for key in merged.get("agent", {}):
        if key not in _AGENT_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [agent].{_suggest(key, _AGENT_KEYS)}")

    permissions = merged.get("permissions", {})
    for key in permissions:
        if key not in _PERMISSION_KEYS:
            raise ConfigError(
                f"Unknown key '{key}' in [permissions].{_suggest(key, _PERMISSION_KEYS)}"
            )
    mode = permissions.get("mode")
    if mode not in VALID_MODES:
        raise ConfigError(
            f"Unknown permissions mode '{mode}' "
            f"({_where(provenance, sources, 'permissions.mode')}). "
            f"Valid modes: {', '.join(VALID_MODES)}."
        )
    for key in ("allow", "deny", "floor_disable"):
        value = permissions.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"[permissions] {key} must be a list of strings, got {value!r}.")
    # A typo must be fatal here specifically: silently ignoring one leaves a
    # floor rule armed that the user believes they turned off, and they find
    # out by being blocked mid-run.
    for name in permissions.get("floor_disable", []):
        if name == "outside-root":
            raise ConfigError(
                "[permissions] floor_disable cannot contain 'outside-root'. "
                "Writes, edits and deletes are confined to the project by the "
                "file-access layer itself, not by this rule, so disabling it "
                "would change nothing and silently redirect the write back "
                "into the project. Shell commands are not confined by it "
                f"either. Disableable rules: {', '.join(DISABLEABLE_FLOOR_RULES)}. "
                "See Documentation/09-permissions.md."
            )
        if name not in FLOOR_RULE_NAMES:
            raise ConfigError(
                f"Unknown floor rule '{name}' in [permissions] floor_disable "
                f"({_where(provenance, sources, 'permissions.floor_disable')})."
                f"{_suggest(name, FLOOR_RULE_NAMES)}"
            )

    for key, value in merged.get("compat", {}).items():
        if key not in _COMPAT_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [compat].{_suggest(key, _COMPAT_KEYS)}")
        if not isinstance(value, bool):
            raise ConfigError(f"[compat] {key} must be true or false, got {value!r}.")

    for key, value in merged.get("tools", {}).items():
        if key not in _TOOLS_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [tools].{_suggest(key, _TOOLS_KEYS)}")
        if key in _TOOLS_BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError(f"[tools] {key} must be true or false, got {value!r}.")
            continue
        # bool is a subclass of int, so `test_timeout = true` would otherwise
        # pass isinstance and quietly become a one-second timeout.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"[tools] {key} must be a whole number of seconds, got {value!r}.")
        if value <= 0:
            raise ConfigError(f"[tools] {key} must be greater than 0, got {value!r}.")


@dataclass(frozen=True)
class Config:
    """Fully resolved configuration for one project."""

    agent: AgentConfig
    permissions: PermissionsConfig
    compat: CompatConfig
    tools: ToolsConfig
    models: dict[str, ModelConfig]
    provenance: dict[str, str] = field(default_factory=dict)
    sources: dict[str, Path | None] = field(default_factory=dict)
    project_root: Path | None = None

    def model_for(self, role: str) -> ModelConfig:
        """Settings for a role, falling back to `default` for unknown roles.

        Step 9 calls build_model("reviewer") before any reviewer config
        exists. Falling back beats raising, and beats shipping config keys
        for roles with no consumer yet.
        """
        return self.models.get(role, self.models["default"])

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Where checkpoints live. Creates nothing — see TODO.md A1.43."""
        return rudra_paths(project_dir).checkpoints_db


def _build_models(merged: dict[str, Any]) -> dict[str, ModelConfig]:
    """Apply role inheritance AFTER the cross-layer merge.

    Doing it per layer would mean a project-level [model.planner] fails to
    inherit a user-level [model.default].
    """
    table = merged.get("model", {})
    base = dict(table.get("default", {}))
    roles = {*BUILTIN_ROLES, *table}
    models: dict[str, ModelConfig] = {}
    for role in roles:
        settings = {**base, **table.get(role, {})}
        models[role] = ModelConfig(**{key: settings.get(key) for key in MODEL_KEYS})
    return models


def build_config(
    project_root: Path | None = None,
    *,
    verbose: bool | None = None,
    permission_mode: str | None = None,
    allow_shell: bool | None = None,
) -> Config:
    """Read all five layers, merge, validate, construct."""
    root = Path(project_root) if project_root is not None else Path.cwd()

    # .env feeds layer 4 only, read from the project root with override=False
    # so a real environment variable still beats it (A5.1).
    load_dotenv(root / ".env")

    user_path = user_toml_path()
    user = read_toml(user_path)
    project = project_toml_layer(root)

    # Roles the env layer must recognise = builtin plus whatever TOML declared.
    declared = {*BUILTIN_ROLES, *user.get("model", {}), *project.get("model", {})}

    ordered = [
        ("builtin", builtin_layer()),
        ("user", user),
        ("project", project),
        ("env", env_layer(declared)),
        ("cli", cli_layer(verbose, permission_mode, allow_shell)),
    ]
    merged, provenance = deep_merge(ordered)

    project_toml = rudra_paths(root).config_toml
    sources: dict[str, Path | None] = {
        "builtin": None,
        "user": user_path if user_path.exists() else None,
        "project": project_toml if project_toml.exists() else None,
        "env": None,
        "cli": None,
    }

    validate(merged, provenance, sources)

    permissions = merged.get("permissions", {})
    return Config(
        agent=AgentConfig(verbose=bool(merged.get("agent", {}).get("verbose", True))),
        permissions=PermissionsConfig(
            mode=permissions.get("mode", "ask"),
            allow=tuple(permissions.get("allow", [])),
            deny=tuple(permissions.get("deny", [])),
            floor_disable=tuple(permissions.get("floor_disable", [])),
        ),
        compat=CompatConfig(**merged.get("compat", {})),
        tools=ToolsConfig(**merged.get("tools", {})),
        models=_build_models(merged),
        provenance=provenance,
        sources=sources,
        project_root=root,
    )


_config: Config | None = None


def get_config(
    project_root: Path | None = None,
    *,
    verbose: bool | None = None,
    permission_mode: str | None = None,
    allow_shell: bool | None = None,
) -> Config:
    """Return the process-wide Config, loading it on first use.

    `project_root` is honored only on the call that actually constructs the
    Config — which is why cli.py must resolve the project path before its
    first get_config() call (TODO.md A5.2).
    """
    global _config
    if _config is None:
        _config = build_config(
            project_root,
            verbose=verbose,
            permission_mode=permission_mode,
            allow_shell=allow_shell,
        )
    return _config


def reset_config() -> None:
    """Drop the cached Config so the next get_config() re-reads everything.

    Also clears the deprecation-warning dedupe set, so a test asserting
    "warns once" is not silenced by an earlier test that already tripped it.
    This was the pre-Step-6 contract and is deliberately preserved.
    """
    global _config
    _config = None
    _reset_deprecation_warnings()


__all__ = [
    "Config",
    "ConfigError",
    "build_config",
    "deep_merge",
    "get_config",
    "reset_config",
    "validate",
]
