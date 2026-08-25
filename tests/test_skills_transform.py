from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from rudra.skills.bundle import Bundle
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import DuplicateSkillError, render


def _fake_bundle(root: Path, name: str, skills: dict[str, str]) -> Bundle:
    """A minimal on-disk bundle, so layout tests do not depend on 472K of corpus."""
    skills_root = root / "src" / "skills"
    for skill_name, body in skills.items():
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: test skill\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return Bundle(
        name=name,
        root=root,
        upstream="https://example.invalid/x",
        version="1.0.0",
        copied_on="2026-08-17",
        source="fixture",
        license="MIT",
        license_file="LICENSE",
        copyright="Copyright (c) 2026 Nobody",
        frozen=True,
        skills_root="src/skills",
    )


def test_library_is_namespaced_by_bundle(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "demo" / "beta" / "SKILL.md").is_file()


def test_active_is_flat_and_holds_only_enabled_skills(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "alpha" / "SKILL.md").is_file()
    assert not (tmp_path / "out" / "active" / "beta").exists()
    assert report.active_skills == ("alpha",)
    assert report.library_skills == ("demo/alpha", "demo/beta")


def test_supporting_files_travel_with_their_skill(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    refs = bundle.skills_path / "alpha" / "references"
    refs.mkdir()
    (refs / "deep.md").write_text("reference body", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "references" / "deep.md").is_file()
    assert (tmp_path / "out" / "active" / "alpha" / "references" / "deep.md").is_file()


def test_duplicate_active_skill_name_raises_naming_both_bundles(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2"})

    with pytest.raises(DuplicateSkillError) as excinfo:
        render([one, two], frozenset({"alpha"}), tmp_path / "out")

    message = str(excinfo.value)
    assert "alpha" in message
    assert "first" in message
    assert "second" in message


def test_duplicate_names_are_fine_when_only_one_is_enabled(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2", "beta": "B"})

    render([one, two], frozenset({"beta"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "beta").is_dir()


def test_render_into_an_existing_dest_replaces_it(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    dest = tmp_path / "out"
    (dest / "library" / "demo" / "stale").mkdir(parents=True)

    render([bundle], frozenset({"alpha"}), dest)

    assert not (dest / "library" / "demo" / "stale").exists()


def test_render_is_deterministic(tmp_path: Path) -> None:
    """Byte-identical output twice over, or 11b's cache key thrashes."""
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "one")
    render([bundle], frozenset({"alpha"}), tmp_path / "two")

    first = sorted(p.relative_to(tmp_path / "one") for p in (tmp_path / "one").rglob("*"))
    second = sorted(p.relative_to(tmp_path / "two") for p in (tmp_path / "two").rglob("*"))
    assert first == second
    for rel in first:
        left, right = tmp_path / "one" / rel, tmp_path / "two" / rel
        if left.is_file():
            assert left.read_bytes() == right.read_bytes()


def test_renders_the_real_corpus(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert len(report.library_skills) == 14
    assert len(report.active_skills) == 9
    assert (tmp_path / "out" / "active" / "brainstorming" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "superpowers" / "writing-skills" / "SKILL.md").is_file()


def test_cross_refs_become_library_paths(tmp_path: Path) -> None:
    bundle = _fake_bundle(
        tmp_path / "b",
        "demo",
        {"alpha": "See demo:beta for details.", "beta": "B"},
    )
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body
    assert "demo:beta" not in body
    assert report.cross_refs_rewritten == 1


def test_active_copies_inherit_the_rewrite(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"})
    bundle = replace(bundle, cross_ref_prefix="demo:")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "active" / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_unknown_names_after_the_prefix_are_left_alone(tmp_path: Path) -> None:
    """Only real skill names are rewritten.

    Prose like "demo:whatever" is not a cross-reference, and turning it into
    a path to a file that does not exist would be worse than leaving it.
    """
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:nosuchskill."})
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "demo:nosuchskill" in body
    assert report.cross_refs_rewritten == 0


def test_a_bundle_without_a_prefix_is_not_rewritten(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert report.cross_refs_rewritten == 0


def test_supporting_markdown_is_rewritten_too(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})
    bundle = replace(bundle, cross_ref_prefix="demo:")
    (bundle.skills_path / "alpha" / "notes.md").write_text("see demo:beta", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_real_corpus_has_no_surviving_namespace_refs(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert report.cross_refs_rewritten == 26

    survivors = [
        path
        for path in (tmp_path / "out").rglob("*.md")
        if "superpowers:" in path.read_text(encoding="utf-8")
    ]
    assert survivors == []


def test_every_rewritten_target_exists_on_disk(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    targets = set()
    pattern = re.compile(r"/skills/library/([a-z0-9-]+)/([a-z0-9-]+)/SKILL\.md")
    for path in dest.rglob("*.md"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            targets.add((match.group(1), match.group(2)))

    assert targets
    for bundle_name, skill_name in sorted(targets):
        assert (dest / "library" / bundle_name / skill_name / "SKILL.md").is_file()


def test_platform_reference_file_is_written_into_the_bootstrap_skill(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = render(BUNDLES, DEFAULT_ENABLED, dest)

    ref = dest / "library" / "superpowers" / "using-superpowers" / "references" / "rudra-tools.md"
    assert ref.is_file()
    assert report.platform_refs_injected == ("superpowers/using-superpowers",)


def test_platform_adaptation_list_names_rudra(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    body = (dest / "library" / "superpowers" / "using-superpowers" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    section = body.split("## Platform Adaptation", 1)[1].split("\n## ", 1)[0]

    # ABSOLUTE, not relative (OPEN-18). The bullet reaches the model inside
    # an injected system prompt, where there is no working directory for a
    # relative path to resolve against -- and it went unread for a whole run
    # because of it. A relative spelling here is the regression.
    assert (
        "Rudra: `/skills/library/superpowers/using-superpowers/references/rudra-tools.md`"
        in section
    )
    assert "Rudra: `references/" not in section
    # Upstream's own entries survive -- this is an addition, not a replacement.
    assert "Codex:" in section


def test_the_active_bootstrap_carries_the_reference_too(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    assert (dest / "active" / "using-superpowers" / "references" / "rudra-tools.md").is_file()
    body = (dest / "active" / "using-superpowers" / "SKILL.md").read_text(encoding="utf-8")
    assert (
        "Rudra: `/skills/library/superpowers/using-superpowers/references/rudra-tools.md`" in body
    )


def test_reference_states_that_no_tool_can_mark_work_done(tmp_path: Path) -> None:
    """The reconciliation that justifies the file existing.

    verification-before-completion and executing-plans both instruct the
    agent to mark work complete. Only loop/engine.py writes DONE, and only
    on VerifyReport.passed (S9c.1). Unreconciled, the corpus argues with
    the architecture inside the model's context.
    """
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    ref = (
        dest / "library" / "superpowers" / "using-superpowers" / "references" / "rudra-tools.md"
    ).read_text(encoding="utf-8")

    assert "add_tasks" in ref
    assert "cannot mark" in ref
    assert "rudra verify" in ref
    assert "read_file" in ref


def test_the_two_imperative_descriptions_are_overridden(tmp_path: Path) -> None:
    """OPEN-17: a description is an instruction, not documentation.

    deepagents renders the index entry verbatim from frontmatter --
    `- **{name}**: {description}` (middleware/skills.py:870) -- so an
    upstream "You MUST use this before any creative work" lands in the
    system prompt of every agent that indexes the corpus. Measured twice on
    nvidia/nemotron-3-ultra-550b-a55b (runs a5aa4552c9ab, 298dd9ac5f2c):
    all three planner stages opened by reading brainstorming, and the
    imperative beat the stage prompt telling them not to.
    """
    dest = tmp_path / "out"
    report = render(BUNDLES, DEFAULT_ENABLED, dest)

    assert report.descriptions_rewritten == ("brainstorming", "using-superpowers")

    for name in ("brainstorming", "using-superpowers"):
        body = (dest / "active" / name / "SKILL.md").read_text(encoding="utf-8")
        front = body.split("---", 2)[1]
        assert "You MUST use this" not in front
        assert "before ANY response" not in front


def test_overriding_a_description_leaves_the_skill_body_untouched(tmp_path: Path) -> None:
    """The methodology is upstream's. Only the index line is ours.

    Rewriting the body would be forking the corpus, which S11a.3 defines as
    a hand-update of the vendored source -- a different, deliberate act.
    """
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    source = (BUNDLES[0].skills_path / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    rendered = (dest / "active" / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")

    # Everything after the frontmatter survives byte for byte.
    assert source.split("---", 2)[2] == rendered.split("---", 2)[2]
    # And the name -- which deepagents requires to equal the directory.
    assert "name: brainstorming" in rendered.split("---", 2)[1]


def test_a_skill_with_no_override_keeps_its_description(tmp_path: Path) -> None:
    """Seven of the nine enabled skills say "Use when ...", which is
    conditional and correct. Only the two unconditional ones are touched."""
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    body = (dest / "active" / "test-driven-development" / "SKILL.md").read_text(encoding="utf-8")
    assert "Use when implementing any feature or bugfix" in body


def test_reference_reconciles_planning_against_the_three_stages(tmp_path: Path) -> None:
    """OPEN-17: the second reconciliation the file has to carry.

    brainstorming ships enabled and its frontmatter says "You MUST use this
    before any creative work". It describes planning as one continuous
    process ending in a written spec; Rudra runs three agents with disjoint
    tools ending at add_tasks. Left unreconciled, run a5aa4552c9ab emitted
    the skill's own workflow as the task list.
    """
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    ref = (
        dest / "library" / "superpowers" / "using-superpowers" / "references" / "rudra-tools.md"
    ).read_text(encoding="utf-8")

    # All three stage names, so a skill's single-process framing is placed
    # rather than merely contradicted.
    assert "clarify" in ref
    assert "architect" in ref
    assert "breakdown" in ref
    # The terminal state, which is what the failing run got wrong.
    assert "Planning ends at `add_tasks`, not at a document." in ref
    # The approval gate belongs to Python, not to the planner.
    assert "not yours to collect" in ref
    # The failure itself, quoted so the model sees the shape it must avoid.
    assert "Propose 2-3 architectural approaches with trade-offs" in ref


def test_every_tool_named_in_the_reference_actually_exists() -> None:
    """A1.77: the file whose job is naming tools must not invent them.

    It shipped claiming `git_status` and `git_log`, which have never
    existed -- git_tools.py grants exactly one tool. An agent believing
    that calls a missing tool and burns a turn recovering, and the wrong
    names landed on the reviewer, the only subagent granted a git tool.
    """
    from rudra.skills.rudra_tools import RUDRA_TOOLS_MD
    from rudra.subagents.spec import FS_TOOL_NAMES

    # Every tool an agent can be granted anywhere in Rudra.
    real = set(FS_TOOL_NAMES) | {
        "task",
        "add_tasks",
        "drop_task",
        "record_fact",
        "ask_user",
        "run_tests",
        "git_diff",
        "read_ledger",
    }

    # Scope the check to the action table -- the part that *promises* a
    # tool exists. Prose elsewhere legitimately backticks parameter names
    # (`limit`, `subagent_type`) and subagent types, which are not tools.
    table = [
        line for line in RUDRA_TOOLS_MD.splitlines() if line.startswith("| ") and "|" in line[2:]
    ]
    named: set[str] = set()
    for line in table:
        promised = line.rsplit("|", 2)[-2]
        named |= set(re.findall(r"`([a-z_][a-z0-9_]*)(?:\([^`]*\))?`", promised))

    assert named, "the action table names no tools at all -- the parser stopped matching"
    assert named <= real, f"names tools that do not exist: {sorted(named - real)}"


def test_injection_is_skipped_for_a_bundle_that_declares_none(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert report.platform_refs_injected == ()


def test_a_missing_platform_section_raises(tmp_path: Path) -> None:
    """A silent skip here would ship a corpus that never mentions Rudra."""
    bundle = _fake_bundle(tmp_path / "b", "demo", {"boot": "no section here"})
    bundle = replace(bundle, bootstrap_skill="boot", platform_ref_section="## Platform Adaptation")

    with pytest.raises(ValueError, match="Platform Adaptation"):
        render([bundle], frozenset({"boot"}), tmp_path / "out")
