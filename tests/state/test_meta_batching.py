from __future__ import annotations

import json
from pathlib import Path

from rudra.state import ensure_layout
from rudra.state.archive import write_run_meta

BASE = "http://localhost:8000/v1"


def test_meta_has_batching_no_secret(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path)
    dest = write_run_meta(
        paths,
        project_path=tmp_path,
        session_id="abc",
        batching={BASE: {"concurrency": 3}},
    )
    assert dest is not None
    meta = json.loads(dest.read_text(encoding="utf-8"))
    assert meta["batching"][BASE]["concurrency"] == 3
    assert "api_key" not in json.dumps(meta)


def test_meta_omits_batching_when_absent(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path)
    dest = write_run_meta(paths, project_path=tmp_path, session_id="abc")
    assert "batching" not in json.loads(dest.read_text(encoding="utf-8"))
