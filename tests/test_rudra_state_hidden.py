"""OPEN-117: no agent can see `.rudra/`, and so none can read its `api_key`.

`.rudra/config.toml` may hold a literal `api_key` (OPEN-6), and `.rudra/` sits
inside the project the backend is rooted at, so before this fix `read_file`,
`ls`, `glob` and `grep` all reached it -- `grep("api_key", "/")` returned the
key line without the model ever naming `.rudra`. Reads are never gated
(`permissions/floor.py`), so the only place that can refuse them for every
agent and every tool at once is the backend.

The key below is fake and is built rather than written out, so no test output
or grep of this repository shows a credential-shaped literal.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from rudra.agent.main_agent import build_backend
from rudra.config.loader import build_config, reset_config
from rudra.middleware.memory_prompt import PLANNER_MEMORY_SOURCES
from rudra.permissions.rules import PermissionEngine
from rudra.state.paths import ensure_layout

FAKE_KEY = "sk-" + "FAKE" * 4 + "1234"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


@pytest.fixture
def project(tmp_path):
    ensure_layout(tmp_path)
    (tmp_path / ".rudra" / "config.toml").write_text(
        f'[model.default]\napi_key = "{FAKE_KEY}"\n', encoding="utf-8"
    )
    (tmp_path / ".rudra" / "AGENTS.md").write_text(
        "## Architecture Notes\n\nthe parser lives in src/p.py\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("api_key = os.environ['X']\n", encoding="utf-8")
    # A name that merely resembles the state directory is project content.
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / ".rudra-notes.md").write_text("api_key notes\n", encoding="utf-8")
    return tmp_path


def backend_for(root):
    return build_backend(build_config(root), root)


def symlink_or_skip(link, target):
    """A capability check, not a platform one (CLAUDE.md §1.8)."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - no symlink support
        pytest.skip(f"cannot create a symlink here: {exc}")


def paths_of(result, field):
    return [item["path"] for item in (getattr(result, field) or [])]


# -- read ------------------------------------------------------------------


def state_spellings(root):
    return [
        "/.rudra/config.toml",
        "/./.rudra/config.toml",
        ".rudra/config.toml",
        "/.RUDRA/config.toml",
        "/src/../.rudra/config.toml",
        "\\.rudra\\config.toml",
        str(root / ".rudra" / "config.toml"),
    ]


def test_every_spelling_of_the_config_reads_as_not_found(project):
    backend = backend_for(project)
    for spelling in state_spellings(project):
        result = backend.read(spelling)
        assert result.error, spelling
        assert "not found" in result.error, spelling
        assert FAKE_KEY not in str(result), spelling


def test_the_not_found_answer_is_the_one_a_missing_file_gets(project):
    backend = backend_for(project)
    hidden = backend.read("/.rudra/config.toml")
    missing = backend.read("/nope.toml")
    assert hidden.error == missing.error.replace("/nope.toml", "/.rudra/config.toml")


def test_a_symlink_into_the_state_directory_does_not_reach_it(project):
    symlink_or_skip(project / "link", ".rudra")
    backend = backend_for(project)
    assert FAKE_KEY not in str(backend.read("/link/config.toml"))
    assert FAKE_KEY not in str(backend.grep("api_key", "/link"))
    assert "/.rudra/config.toml" not in paths_of(backend.ls("/link"), "entries")
    assert "/.rudra/config.toml" not in paths_of(backend.glob("*", "/link"), "matches")


def test_project_files_still_read(project):
    backend = backend_for(project)
    assert "os.environ" in str(backend.read("/src/app.py"))
    assert "api_key notes" in str(backend.read("/notes/.rudra-notes.md"))


# -- ls, glob, grep ---------------------------------------------------------


def test_ls_of_the_root_does_not_list_the_state_directory(project):
    entries = paths_of(backend_for(project).ls("/"), "entries")
    assert "/src/" in entries
    assert not [p for p in entries if ".rudra/" in p.casefold() and "notes" not in p]


def test_ls_of_the_state_directory_is_path_not_found(project):
    backend = backend_for(project)
    for spelling in ("/.rudra", "/.rudra/", ".rudra", "/.RUDRA", "/./.rudra/run"):
        result = backend.ls(spelling)
        assert result.error and "path_not_found" in result.error, spelling
        assert not result.entries


def test_glob_never_matches_state(project):
    backend = backend_for(project)
    for pattern, path in (
        ("**/*", "/"),
        ("**/*.toml", "/"),
        ("*", "/.rudra"),
        ("**/.rudra/*", "/"),
    ):
        matched = paths_of(backend.glob(pattern, path), "matches")
        assert not [p for p in matched if p.startswith("/.rudra/")], (pattern, path)
    assert "/src/app.py" in paths_of(backend.glob("**/*", "/"), "matches")


def test_grep_over_the_root_does_not_return_the_key(project):
    backend = backend_for(project)
    for args in (("api_key", "/"), ("api_key", None), (FAKE_KEY, "/"), ("api_key", "/.rudra")):
        result = backend.grep(*args)
        assert FAKE_KEY not in str(result), args
        assert not [p for p in paths_of(result, "matches") if p.startswith("/.rudra/")], args
    assert FAKE_KEY not in str(backend.grep("api_key", "/", glob="*.toml"))
    visible = paths_of(backend.grep("api_key", "/"), "matches")
    assert "/src/app.py" in visible
    assert "/notes/.rudra-notes.md" in visible


def test_grep_honours_max_count_after_hiding_state(project):
    """A cap must be spent on what the agent can see. `.rudra/run/logs/`
    holds every line a run printed, so state matches crowding out the
    project's own would make grep answer "nothing" to a real question."""
    backend = backend_for(project)
    logs = project / ".rudra" / "run" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "a.jsonl").write_text("api_key\n" * 50, encoding="utf-8")
    matched = paths_of(backend.grep("api_key", "/", max_count=1), "matches")
    assert matched and not [p for p in matched if p.startswith("/.rudra/")]


def test_the_async_twins_hide_state_too(project):
    backend = backend_for(project)

    async def probe():
        return (
            await backend.aread("/.rudra/config.toml"),
            await backend.als("/"),
            await backend.aglob("**/*", "/"),
            await backend.agrep("api_key", "/"),
        )

    read, ls, glob, grep = asyncio.run(probe())
    assert FAKE_KEY not in str(read) and read.error
    assert not [p for p in paths_of(ls, "entries") if p.startswith("/.rudra")]
    assert not [p for p in paths_of(glob, "matches") if p.startswith("/.rudra/")]
    assert FAKE_KEY not in str(grep)


# -- what must keep working ------------------------------------------------


def test_memory_still_loads_agents_md(project):
    """MemoryMiddleware reads `.rudra/AGENTS.md` through `download_files`
    (deepagents/middleware/memory.py:295). Hiding that route would take the
    planner's memory away silently."""
    backend = backend_for(project)
    (response,) = backend.download_files(list(PLANNER_MEMORY_SOURCES))
    assert response.error is None
    assert b"the parser lives in src/p.py" in response.content


def test_the_default_still_executes(project):
    backend = backend_for(project)
    assert isinstance(backend.default, SandboxBackendProtocol)
    assert "shell-is-live" in backend.execute("echo shell-is-live").output


def test_shell_false_still_yields_no_execution(tmp_path):
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "config.toml").write_text("[tools]\nshell = false\n", encoding="utf-8")
    ensure_layout(tmp_path)
    backend = backend_for(tmp_path)
    assert not isinstance(backend.default, SandboxBackendProtocol)
    assert FAKE_KEY not in str(backend.read("/.rudra/config.toml"))
    assert backend.read("/.rudra/config.toml").error


def test_the_artifacts_route_still_reads_and_writes(project):
    """Eviction writes `/artifacts/...`, which lands under `.rudra/run/` on
    disk (A1.45). It is a route, not the default backend, and must stay
    readable -- the agent reads its evicted tool results back from it."""
    backend = backend_for(project)
    assert backend.write("/artifacts/large_tool_results/x", "evicted").error is None
    assert "evicted" in str(backend.read("/artifacts/large_tool_results/x"))


# -- through the tools, on a real graph ------------------------------------


class ScriptedReads(BaseChatModel):
    """Every read-shaped tool at once, then stops. No network, no provider."""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-reads"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls > 1:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])
        calls = [
            ("read_file", {"file_path": "/.rudra/config.toml"}),
            ("ls", {"path": "/"}),
            ("ls", {"path": "/.rudra"}),
            ("glob", {"pattern": "**/*.toml", "path": "/"}),
            ("grep", {"pattern": "api_key", "path": "/", "output_mode": "content"}),
            ("grep", {"pattern": "api_key"}),
        ]
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": args, "id": f"call-{i}"}
                for i, (name, args) in enumerate(calls)
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_no_tool_result_carries_the_key_or_the_config_path(project):
    agent = create_deep_agent(model=ScriptedReads(), backend=backend_for(project))
    result = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 6
    for message in tool_messages:
        content = str(message.content)
        assert FAKE_KEY not in content, message.name
        if message.name != "read_file":
            assert ".rudra/config.toml" not in content, (message.name, content)
    grep_content = next(
        str(m.content) for m in tool_messages if m.name == "grep" and "os.environ" in str(m.content)
    )
    assert "src/app.py" in grep_content


# -- writes: the floor -----------------------------------------------------


def engine(root, **kwargs):
    defaults = dict(mode="auto", allow=(), deny=(), floor_disable=(), project_root=root)
    defaults.update(kwargs)
    return PermissionEngine(**defaults)


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "delete"])
def test_writing_rudra_state_is_denied_by_the_floor_in_auto_mode(project, tool):
    for spelling in ("/.rudra/config.toml", ".rudra/facts.json", "/.RUDRA/config.toml"):
        decision = engine(project).decide(tool, {"file_path": spelling, "content": "x"})
        assert (decision.effect, decision.rule, decision.source) == (
            "deny",
            "<floor:rudra-state>",
            "floor",
        ), (tool, spelling)


def test_disabling_the_rule_still_audits_it(project):
    decision = engine(project, floor_disable=("rudra-state",)).decide(
        "write_file", {"file_path": "/.rudra/config.toml", "content": "x"}
    )
    assert decision.effect != "deny"
    assert (decision.rule, decision.source) == ("<floor:rudra-state>", "floor-disabled")


def test_the_artifacts_route_is_not_rudra_state_to_the_gate(project):
    """`build_gate` is handed the route prefixes, so `/artifacts/x` resolves
    as a mount point rather than as `.rudra/run/artifacts/x` on disk."""
    decision = engine(project, routes=("/artifacts/",)).decide(
        "write_file", {"file_path": "/artifacts/large_tool_results/x", "content": "x"}
    )
    assert decision.rule != "<floor:rudra-state>"


def test_a_lookalike_name_is_not_rudra_state(project):
    decision = engine(project).decide(
        "write_file", {"file_path": "/notes/.rudra-notes.md", "content": "x"}
    )
    assert (decision.effect, decision.source) == ("allow", "mode-default")


def test_the_config_loader_accepts_the_new_rule_name(tmp_path):
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "config.toml").write_text(
        '[permissions]\nfloor_disable = ["rudra-state"]\n', encoding="utf-8"
    )
    assert build_config(tmp_path).permissions.floor_disable == ("rudra-state",)


def test_the_default_is_still_what_deepagents_checks_for(project):
    """deepagents reads `isinstance(backend.default, LocalShellBackend)` into
    the execute tool's prompt (`_route_host_path_prompt`). A wrapper failed
    that check and silently told the model its routes had no host path."""
    from deepagents.middleware.filesystem import _route_host_path_prompt

    backend = backend_for(project)
    assert isinstance(backend.default, LocalShellBackend)
    assert str(backend.routes["/artifacts/"].cwd) in _route_host_path_prompt(backend)
