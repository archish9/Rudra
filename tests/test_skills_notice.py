from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from rudra.skills.notice import render_notice
from rudra.skills.registry import BUNDLES

REPO_ROOT = Path(__file__).parent.parent


def test_notice_names_rudras_own_license() -> None:
    text = render_notice(BUNDLES)
    assert "Apache License" in text or "Apache-2.0" in text


def test_notice_attributes_every_bundle() -> None:
    text = render_notice(BUNDLES)
    for bundle in BUNDLES:
        assert bundle.name in text
        assert bundle.upstream in text
        assert bundle.version in text
        assert bundle.license in text
        assert bundle.copyright in text


def test_notice_states_what_rudra_modifies() -> None:
    """MIT requires the notice, not a changelog -- but the honest statement
    is that the distributed copy is unmodified and the runtime one is not."""
    text = render_notice(BUNDLES)
    assert "unmodified" in text
    assert "Cross-references between documents are rewritten" in text


def test_committed_notice_matches_the_registry() -> None:
    """NOTICE is generated, so it cannot drift when a bundle is added."""
    assert (REPO_ROOT / "NOTICE").read_text(encoding="utf-8") == render_notice(BUNDLES)


def test_built_wheel_carries_the_vendored_corpus(tmp_path: Path) -> None:
    """472K of non-Python data must survive packaging.

    Losing it would only surface for a pipx or PyPI user, long after merge.
    """
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())

    assert "rudra/skills/bundles/superpowers/src/skills/brainstorming/SKILL.md" in names
    assert "rudra/skills/bundles/superpowers/LICENSE" in names
    assert "rudra/skills/bundles/superpowers/MANIFEST.sha256" in names
    assert "rudra/skills/bundles/superpowers/BUNDLE.toml" in names
