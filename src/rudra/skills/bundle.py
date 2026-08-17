"""One vendored upstream corpus, described by its own BUNDLE.toml.

A bundle is self-describing so the transform is not shaped around any
single upstream: shipping a second corpus is a directory plus one line in
registry.py (spec S11a.5).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_REQUIRED = (
    "name",
    "upstream",
    "version",
    "copied_on",
    "source",
    "license",
    "license_file",
    "copyright",
    "frozen",
    "skills_root",
)


@dataclass(frozen=True)
class Bundle:
    """Metadata for one vendored corpus, plus where its files are."""

    name: str
    root: Path
    upstream: str
    version: str
    copied_on: str
    source: str
    license: str
    license_file: str
    copyright: str
    frozen: bool
    skills_root: str
    cross_ref_prefix: str | None = None
    bootstrap_skill: str | None = None
    platform_ref_section: str | None = None

    @property
    def skills_path(self) -> Path:
        return self.root / self.skills_root

    @property
    def manifest_path(self) -> Path:
        return self.root / "MANIFEST.sha256"

    @property
    def license_path(self) -> Path:
        return self.root / self.license_file

    def skill_names(self) -> tuple[str, ...]:
        """Directory names holding a SKILL.md, sorted.

        Sorted because the transform's output must be deterministic: 11b's
        cache key would otherwise change with directory iteration order.
        """
        return tuple(sorted(p.parent.name for p in self.skills_path.glob("*/SKILL.md")))


def load_bundle(root: Path) -> Bundle:
    """Parse `root/BUNDLE.toml` into a Bundle.

    Missing required keys raise rather than defaulting: a bundle with no
    declared license would otherwise ship with silently empty attribution.
    """
    data = tomllib.loads((root / "BUNDLE.toml").read_text(encoding="utf-8"))

    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        msg = f"{root / 'BUNDLE.toml'} is missing required key(s): {', '.join(missing)}"
        raise ValueError(msg)

    return Bundle(
        name=data["name"],
        root=root,
        upstream=data["upstream"],
        version=data["version"],
        copied_on=data["copied_on"],
        source=data["source"],
        license=data["license"],
        license_file=data["license_file"],
        copyright=data["copyright"],
        frozen=data["frozen"],
        skills_root=data["skills_root"],
        cross_ref_prefix=data.get("cross_ref_prefix"),
        bootstrap_skill=data.get("bootstrap_skill"),
        platform_ref_section=data.get("platform_ref_section"),
    )
