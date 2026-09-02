"""The run archive: evidence that outlives the project it was produced in (OPEN-68).

Every instrument this project has -- `usage.json`, `debug-<id>.jsonl`, the
ledger, the transcript -- is written under `<project>/.rudra/run/`, and on
2026-09-01 the whole pool of run evidence (`run6` ... `run14`) was found to
be gone: the test projects were throwaway sibling directories and had been
deleted, taking `.rudra/` with them. Six items had been sized against that
pool and none of them can be recomputed.

That is why the archive is at the USER level and not a durable subtree of
`.rudra/`: the thing that was deleted was the project, so a copy inside it
survives nothing. `$XDG_STATE_HOME/rudra/runs/` is the same move
`config/layers.py` makes for `$XDG_CONFIG_HOME` and `skills/cache.py` makes
for `$XDG_CACHE_HOME`.

The rule these tests hold, in order of importance:

1. the archive survives `rmtree(project)` -- the failure that actually happened;
2. nothing here may raise, `write_usage_log`'s rule: a finished run must not
   be reported failed over its own bookkeeping;
3. retention deletes WHOLE runs, never part of one -- a half-copied debug log
   is an instrument that lies rather than one that is missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rudra.state.archive import (
    KEEP_RUNS,
    MAX_PROJECT_BYTES,
    META_NAME,
    archive_home,
    archive_run,
    project_slug,
    prune_runs,
    run_dir,
)
from rudra.state.paths import ensure_layout


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An XDG state root of our own. Nothing here may touch the real home."""
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    return root / "rudra" / "runs"


def _project_with_evidence(root: Path, session_id: str = "abc123def456") -> Path:
    """A project whose run left every file the archive collects."""
    paths = ensure_layout(root)
    (paths.logs / "usage.json").write_text(
        json.dumps({"roles": {"coder": {"calls": 7, "retries": 1, "exhaustions": 1}}}),
        encoding="utf-8",
    )
    paths.ledger_json.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    (paths.logs / f"debug-{session_id}.jsonl").write_text(
        '{"kind":"notice","name":"retry"}\n', encoding="utf-8"
    )
    (paths.transcripts / f"{session_id}.jsonl").write_text('{"kind":"tool"}\n', encoding="utf-8")
    return root


# --- where it goes ----------------------------------------------------------


def test_archive_home_honours_xdg_state_home(home: Path, tmp_path: Path) -> None:
    assert archive_home() == tmp_path / "state" / "rudra" / "runs"


def test_archive_home_falls_back_to_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The XDG default, spelled the way layers.py and cache.py spell theirs."""
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/nobody")))
    assert archive_home() == Path("/home/nobody/.local/state/rudra/runs")


def test_an_explicit_home_wins_over_the_environment(home: Path, tmp_path: Path) -> None:
    assert archive_home(tmp_path / "elsewhere") == tmp_path / "elsewhere"


def test_two_projects_of_the_same_name_do_not_collide(tmp_path: Path) -> None:
    """`app` and `app` are one name and two projects. The path settles it."""
    first = project_slug(tmp_path / "one" / "app")
    second = project_slug(tmp_path / "two" / "app")
    assert first != second
    assert first.startswith("app-") and second.startswith("app-")


def test_the_slug_is_stable_across_spellings_of_one_path(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    assert project_slug(tmp_path / "app") == project_slug(tmp_path / "." / "app")


def test_the_slug_survives_a_name_that_is_not_a_filename(tmp_path: Path) -> None:
    assert "/" not in project_slug(tmp_path / "a b/c:d")
    assert project_slug(Path("/")).startswith("project-")


def test_run_dir_is_project_then_run(home: Path, tmp_path: Path) -> None:
    where = run_dir(tmp_path / "app", "deadbeef", home=home)
    assert where.parent.parent == home
    assert where.name == "deadbeef"
    assert where.parent.name == project_slug(tmp_path / "app")


# --- what it collects -------------------------------------------------------


def test_archive_run_copies_every_instrument(home: Path, tmp_path: Path) -> None:
    project = _project_with_evidence(tmp_path / "app")
    where = archive_run(project_path=project, session_id="abc123def456", home=home, paths=None)

    assert where is not None
    names = {path.name for path in where.iterdir()}
    assert names == {
        META_NAME,
        "usage.json",
        "ledger.json",
        "debug-abc123def456.jsonl",
        "transcript.jsonl",
    }
    usage = json.loads((where / "usage.json").read_text(encoding="utf-8"))
    assert usage["roles"]["coder"]["exhaustions"] == 1


def test_the_archive_outlives_the_project(home: Path, tmp_path: Path) -> None:
    """The defect, reproduced: the project directory is deleted wholesale.

    This is the one test that reproduces what actually happened on
    2026-09-01. A durable subtree of `.rudra/` -- OPEN-68's shape A -- fails
    it, which is why the shape chosen is a user-level one.
    """
    project = _project_with_evidence(tmp_path / "test-rudra-run15")
    where = archive_run(project_path=project, session_id="abc123def456", home=home, paths=None)
    shutil.rmtree(project)

    assert not project.exists()
    assert where is not None and where.is_dir()
    assert json.loads((where / "usage.json").read_text(encoding="utf-8"))["roles"]


def test_meta_records_where_the_run_came_from(home: Path, tmp_path: Path) -> None:
    """The archive is read by a session that has only this directory."""
    import rudra

    project = _project_with_evidence(tmp_path / "app")
    where = archive_run(project_path=project, session_id="abc123def456", home=home, paths=None)
    assert where is not None
    meta = json.loads((where / META_NAME).read_text(encoding="utf-8"))
    assert meta["run_id"] == "abc123def456"
    assert meta["project_path"] == str(project.resolve())
    assert meta["rudra_version"] == rudra.__version__
    assert sorted(meta["files"]) == [
        "debug-abc123def456.jsonl",
        "ledger.json",
        "transcript.jsonl",
        "usage.json",
    ]
    assert meta["archived_at"] > 0


def test_a_missing_instrument_is_skipped_not_fatal(home: Path, tmp_path: Path) -> None:
    """`--no-debug` writes no debug log; a plan-only run writes no usage.json."""
    project = tmp_path / "app"
    paths = ensure_layout(project)
    paths.ledger_json.write_text("{}", encoding="utf-8")

    where = archive_run(project_path=project, session_id="run1", home=home, paths=None)
    assert where is not None
    assert {path.name for path in where.iterdir()} == {META_NAME, "ledger.json"}


def test_a_run_that_left_nothing_archives_nothing(home: Path, tmp_path: Path) -> None:
    """No empty directories: a --plan run and a REPL turn that wrote nothing
    must not each leave a folder holding one meta file."""
    project = tmp_path / "app"
    ensure_layout(project)
    assert archive_run(project_path=project, session_id="run1", home=home, paths=None) is None
    assert not home.exists()


def test_an_unwritable_home_is_reported_as_none(home: Path, tmp_path: Path) -> None:
    """Bookkeeping never ends a run -- write_usage_log's rule (C7.5)."""
    project = _project_with_evidence(tmp_path / "app")
    home.parent.mkdir(parents=True)
    home.write_text("not a directory", encoding="utf-8")

    assert archive_run(project_path=project, session_id="r", home=home, paths=None) is None


def test_archiving_a_project_that_does_not_exist_is_reported_as_none(
    home: Path, tmp_path: Path
) -> None:
    assert (
        archive_run(project_path=tmp_path / "gone", session_id="r", home=home, paths=None) is None
    )


# --- retention --------------------------------------------------------------


def test_pruning_keeps_the_newest_runs(home: Path, tmp_path: Path) -> None:
    project_dir = home / "app-0000"
    for index in range(5):
        run = project_dir / f"run{index}"
        run.mkdir(parents=True)
        (run / "usage.json").write_text("{}", encoding="utf-8")
        import os

        os.utime(run, (1_000 + index, 1_000 + index))

    kept = prune_runs(project_dir, keep=2)

    assert {path.name for path in kept} == {"run4", "run3"}
    assert {path.name for path in project_dir.iterdir()} == {"run4", "run3"}


def test_pruning_deletes_whole_runs_when_the_budget_is_exceeded(home: Path, tmp_path: Path) -> None:
    """Bounded by bytes as well as by count, because the debug log is uncapped.

    Whole runs, never a truncated file: an instrument missing is a gap, an
    instrument silently cut short is a wrong number.
    """
    project_dir = home / "app-0000"
    import os

    for index in range(4):
        run = project_dir / f"run{index}"
        run.mkdir(parents=True)
        (run / "debug.jsonl").write_bytes(b"x" * 1000)
        os.utime(run, (1_000 + index, 1_000 + index))

    kept = prune_runs(project_dir, keep=KEEP_RUNS, max_bytes=2500)

    assert {path.name for path in kept} == {"run3", "run2"}
    assert not (project_dir / "run0").exists()


def test_pruning_leaves_other_projects_alone(home: Path, tmp_path: Path) -> None:
    mine = home / "app-0000" / "run1"
    theirs = home / "other-1111" / "run1"
    for run in (mine, theirs):
        run.mkdir(parents=True)
        (run / "usage.json").write_text("{}", encoding="utf-8")

    prune_runs(home / "app-0000", keep=0)

    assert not mine.exists()
    assert theirs.exists()


def test_pruning_ignores_files_and_a_missing_directory(home: Path, tmp_path: Path) -> None:
    project_dir = home / "app-0000"
    project_dir.mkdir(parents=True)
    stray = project_dir / "README"
    stray.write_text("not a run", encoding="utf-8")

    assert prune_runs(project_dir, keep=0) == []
    assert stray.exists()
    assert prune_runs(home / "nope") == []


def test_archiving_prunes_the_project_it_wrote(home: Path, tmp_path: Path) -> None:
    project = _project_with_evidence(tmp_path / "app")
    for index in range(3):
        archive_run(project_path=project, session_id=f"run{index}", home=home, paths=None, keep=2)

    slug = project_slug(project)
    assert {path.name for path in (home / slug).iterdir()} == {"run1", "run2"}


def test_the_byte_budget_is_per_project_and_generous_enough_to_be_useful() -> None:
    """A guard on the constants, not on behaviour: these bound the user's
    home directory, and both were chosen rather than inherited."""
    assert KEEP_RUNS == 20
    assert MAX_PROJECT_BYTES == 2 * 1024**3


# --- configuration ----------------------------------------------------------


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with no user-level config layer above it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def test_run_archive_defaults_on(isolated: Path) -> None:
    from rudra.config.loader import build_config

    assert build_config(isolated).agent.run_archive is True


def test_run_archive_can_be_turned_off_in_toml(isolated: Path) -> None:
    from rudra.config.loader import build_config

    paths = ensure_layout(isolated)
    paths.config_toml.write_text("[agent]\nrun_archive = false\n", encoding="utf-8")

    assert build_config(isolated).agent.run_archive is False


def test_a_quoted_boolean_is_a_hard_error(isolated: Path) -> None:
    """`run_archive = "false"` must not mean on -- the CR-D3 class."""
    from rudra.config.loader import ConfigError, build_config

    paths = ensure_layout(isolated)
    paths.config_toml.write_text('[agent]\nrun_archive = "false"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="run_archive"):
        build_config(isolated)


def test_a_typo_suggests_the_real_key(isolated: Path) -> None:
    from rudra.config.loader import ConfigError, build_config

    paths = ensure_layout(isolated)
    paths.config_toml.write_text("[agent]\nrun_archiv = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="run_archive"):
        build_config(isolated)


# --- wiring -----------------------------------------------------------------


@pytest.fixture
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`create_main_agent` reaches the process-global get_config() (A1.52)."""
    from rudra.config.loader import reset_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_config()
    yield tmp_path
    reset_config()


def _project(root: Path, config: str = "") -> Path:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(
        '[permissions]\nmode = "auto"\n' + config, encoding="utf-8"
    )
    return root


async def test_closing_a_real_agent_archives_the_run(hermetic: Path) -> None:
    """The whole point, wired: close() is the last moment every instrument
    this run wrote is still on disk in a directory about to be deleted."""
    from rudra.agent.main_agent import create_main_agent

    root = _project(hermetic)
    agent = await create_main_agent(root, "task")
    await agent.close()

    where = run_dir(root, agent.session_id, home=archive_home())
    assert where.is_dir()
    meta = json.loads((where / META_NAME).read_text(encoding="utf-8"))
    assert meta["project_path"] == str(root.resolve())
    assert f"debug-{agent.session_id}.jsonl" in meta["files"]


async def test_run_archive_false_keeps_the_evidence_in_the_project(hermetic: Path) -> None:
    from rudra.agent.main_agent import create_main_agent

    root = _project(hermetic, "[agent]\nrun_archive = false\n")
    agent = await create_main_agent(root, "task")
    assert agent._archive_paths is None
    await agent.close()

    assert not archive_home().exists()


async def test_a_hand_built_agent_never_writes_to_the_home_directory(
    home: Path, tmp_path: Path
) -> None:
    """archive_paths defaults to None so a test that builds a RudraAgent
    directly cannot reach the developer's home."""
    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent

    project = _project_with_evidence(tmp_path / "app")
    agent = RudraAgent(
        context=AgentContext(project_path=project, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=object(),
        planner_callback=None,
        ledger=None,
    )
    await agent.close()

    assert not home.exists()


# --- what the archive says about the run ------------------------------------


def test_model_facts_records_the_endpoint_and_never_the_key() -> None:
    """The pooled rates all carry "one endpoint, one model throughout", and
    nothing in usage.json can support that claim. config.toml is not copied
    instead because it may hold a literal api_key (OPEN-6)."""
    from types import SimpleNamespace

    from rudra.state.archive import model_facts

    cfg = SimpleNamespace(
        models={
            "coder": SimpleNamespace(
                provider="openai_compatible",
                model="nemotron-3",
                base_url="http://localhost:8000/v1",
                api_key="sk-secret",
                api_key_env="RUDRA_CODER_KEY",
            )
        }
    )
    facts = model_facts(cfg)
    assert facts["coder"] == {
        "provider": "openai_compatible",
        "model": "nemotron-3",
        "base_url": "http://localhost:8000/v1",
    }


async def test_the_archive_never_contains_an_api_key(hermetic: Path) -> None:
    """A run configured with a literal key must not leave it in the archive."""
    from rudra.agent.main_agent import create_main_agent

    root = _project(
        hermetic,
        '[model.default]\napi_key = "sk-do-not-archive"\nmodel = "m"\n',
    )
    agent = await create_main_agent(root, "task")
    await agent.close()

    where = run_dir(root, agent.session_id, home=archive_home())
    body = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in where.rglob("*")
        if path.is_file()
    )
    assert "sk-do-not-archive" not in body
    assert (
        json.loads((where / META_NAME).read_text(encoding="utf-8"))["models"]["default"]["model"]
        == "m"
    )


# --- OPEN-69: the archive must not be short by construction -----------------


def test_the_archiver_logs_its_intent_before_copying(home: Path, tmp_path: Path) -> None:
    """OPEN-69: `archive_run` logs to the `rudra` tree, and the project's debug
    log IS that tree's handler -- so a line written after the copy lands in the
    file it just copied and can never be in the copy. RUN #9 measured exactly
    one such line, on every run, forever."""
    import logging

    from rudra.state.archive import LOGGER

    project = _project_with_evidence(tmp_path / "app")
    debug = project / ".rudra" / "run" / "logs" / "debug-abc123def456.jsonl"

    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Append to the project's debug log the way the real handler does,
            # so "was it copied" is a question about bytes and not about mocks.
            records.append(record.getMessage())
            with debug.open("a", encoding="utf-8") as handle:
                handle.write('{"kind":"log"}\n')

    handler = _Capture()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.DEBUG)
    try:
        where = archive_run(project_path=project, session_id="abc123def456", home=home, paths=None)
    finally:
        LOGGER.removeHandler(handler)

    assert where is not None
    assert records, "the archiver must say where the evidence went"
    archived = (where / "debug-abc123def456.jsonl").read_bytes()
    assert archived == debug.read_bytes()


def test_the_archived_debug_log_is_a_prefix_of_the_project_s(home: Path, tmp_path: Path) -> None:
    """The honest contract. The log is still OPEN when it is copied -- close()
    has mcp and the checkpointer left to shut down and either may log -- so
    byte-identity is not something this code can promise. A prefix is: later
    records only ever append."""
    project = _project_with_evidence(tmp_path / "app")
    debug = project / ".rudra" / "run" / "logs" / "debug-abc123def456.jsonl"

    where = archive_run(project_path=project, session_id="abc123def456", home=home, paths=None)
    assert where is not None
    with debug.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"log","payload":"written after close"}\n')

    archived = (where / "debug-abc123def456.jsonl").read_bytes()
    assert debug.read_bytes().startswith(archived)


async def test_close_archives_after_the_checkpointer_and_the_mcp_client(
    home: Path, tmp_path: Path
) -> None:
    """OPEN-69: archiving is the LAST thing close() does. The comment that put
    it first argued "what is copied must be complete" -- but every later step
    only appends, so archiving last copies strictly more."""
    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent
    from rudra.state.paths import rudra_paths

    project = _project_with_evidence(tmp_path / "app")
    debug = project / ".rudra" / "run" / "logs" / "debug-abc123def456.jsonl"
    order: list[str] = []

    def _log(what: str) -> None:
        """Write the way a real shutdown step's log record would. What the
        archive holds is the evidence of when it ran -- not a mock's word."""
        order.append(what)
        with debug.open("a", encoding="utf-8") as handle:
            handle.write('{"kind":"log","payload":"%s"}\n' % what)

    class _Transcript:
        def close(self) -> None:
            _log("transcript")

    class _Mcp:
        async def aclose(self) -> None:
            _log("mcp")

    class _Db:
        async def close(self) -> None:
            _log("db")

    agent = RudraAgent(
        context=AgentContext(project_path=project, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=_Db(),
        loop_context=object(),
        planner_callback=None,
        ledger=None,
        mcp=_Mcp(),
        transcript=_Transcript(),
        archive_paths=rudra_paths(project),
    )
    await agent.close()

    assert order == ["transcript", "mcp", "db"]
    archived = (run_dir(project, "abc123def456", home=home) / "debug-abc123def456.jsonl").read_text(
        encoding="utf-8"
    )
    for step in order:
        assert f'"{step}"' in archived, f"{step} ran after the copy, so the archive missed it"


async def test_close_detaches_the_run_log_after_archiving(tmp_path: Path, home: Path) -> None:
    """OPEN-75, and the ordering is OPEN-69's: archive_run logs where it
    copied the run to, so the handler must outlive the archive and not the
    turn."""
    import logging

    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent
    from rudra.trace.debug import LOGGER_NAME, configure_debug_logging

    project = _project_with_evidence(tmp_path / "app")
    log = project / ".rudra" / "run" / "logs" / "debug-abc123def456.jsonl"
    handler = configure_debug_logging(log, enabled=True)

    agent = RudraAgent(
        context=AgentContext(project_path=project, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=object(),
        planner_callback=None,
        ledger=None,
        debug_handler=handler,
    )
    await agent.close()

    assert handler not in logging.getLogger(LOGGER_NAME).handlers


# --- OPEN-78: what machine and what settings produced this run --------------


def test_env_facts_records_the_settings_that_change_behaviour() -> None:
    """`interactive` decides whether ask_user is registered at all and
    whether the plan gate prompts. A run that behaved unlike the reader's
    expectation usually differed here first."""
    from types import SimpleNamespace

    from rudra.state.archive import env_facts

    cfg = SimpleNamespace(
        agent=SimpleNamespace(verbose=True, debug_log=True, max_fix_attempts=3, max_questions=5),
        permissions=SimpleNamespace(mode="ask"),
        tools=SimpleNamespace(shell=True, shell_in_auto=False),
    )
    env = env_facts(cfg)

    assert env["mode"] == "ask"
    assert env["shell_in_auto"] is False
    assert env["max_questions"] == 5
    assert isinstance(env["interactive"], bool)


def test_env_facts_records_the_versions_a_bug_report_needs() -> None:
    from types import SimpleNamespace

    from rudra.state.archive import env_facts

    env = env_facts(SimpleNamespace())

    assert env["python"].count(".") == 2
    assert env["platform"]
    assert env["deepagents"]  # pinned exactly in pyproject; absent would be a finding


def test_env_facts_never_reaches_a_key() -> None:
    """model_facts' rule, one block over: config.toml is not copied because
    it may hold a literal api_key (OPEN-6), and neither is anything derived
    from it beyond settings, versions and capabilities."""
    from types import SimpleNamespace

    from rudra.state.archive import env_facts

    cfg = SimpleNamespace(
        agent=SimpleNamespace(verbose=False),
        permissions=SimpleNamespace(mode="auto"),
        tools=SimpleNamespace(shell=True),
        models={"planner": SimpleNamespace(api_key="sk-secret", model="m")},
    )
    assert "sk-secret" not in json.dumps(env_facts(cfg))


def test_env_facts_survives_a_config_missing_every_section() -> None:
    """Bookkeeping never ends a run, and a hand-built cfg is what several
    tests pass."""
    from types import SimpleNamespace

    from rudra.state.archive import env_facts

    assert env_facts(SimpleNamespace())["mode"] is None


# --- OPEN-79: every end writes the tally, and facts travel with it ----------


async def test_close_writes_the_usage_log_on_an_end_work_never_reached(
    tmp_path: Path, home: Path
) -> None:
    """work() writes usage.json as its last statement, so a declined plan,
    a dead planner stage and a crash wrote none -- the failure class a bug
    report is about is the one class with no cost data."""
    from types import SimpleNamespace

    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent
    from rudra.context.usage import RunUsage
    from rudra.state.paths import rudra_paths

    project = _project_with_evidence(tmp_path / "app")
    paths = rudra_paths(project)
    usage_json = Path(paths.logs) / "usage.json"
    usage_json.unlink(missing_ok=True)

    agent = RudraAgent(
        context=AgentContext(project_path=project, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=SimpleNamespace(usage=RunUsage(), paths=paths),
        planner_callback=None,
        ledger=None,
    )
    await agent.close()

    assert "run" in json.loads(usage_json.read_text(encoding="utf-8"))


async def test_a_hand_built_loop_context_does_not_break_close(tmp_path: Path, home: Path) -> None:
    """Several tests pass `object()`. Bookkeeping never ends a run."""
    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent

    agent = RudraAgent(
        context=AgentContext(
            project_path=_project_with_evidence(tmp_path / "app"),
            task="t",
            console=Console(quiet=True),
        ),
        session_id="abc123def456",
        db_conn=None,
        loop_context=object(),
        planner_callback=None,
        ledger=None,
    )
    await agent.close()


def test_facts_travel_with_the_archive(tmp_path: Path) -> None:
    """What the planner settled is in every agent's prompt and goes with
    the project when the project is deleted."""
    from rudra.state.archive import _sources
    from rudra.state.paths import rudra_paths

    names = [name for _source, name in _sources(rudra_paths(tmp_path), "abc123def456")]
    assert "facts.json" in names
