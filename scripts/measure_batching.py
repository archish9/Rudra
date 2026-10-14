"""Price Vaayu's N>1 batched decode: N Rudra sessions concurrent vs serial.

`aggregate_report` is pure and unit-tested. `main` launches real `rudra`
processes and needs a running Vaayu server; it is not covered by tests.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _wall(usage: dict) -> float:
    if usage.get("_failed"):
        return 0.0
    return float(usage.get("run", {}).get("wall_seconds", 0.0))


def aggregate_report(
    serial_usages: list[dict], concurrent_usages: list[dict], vaayu_stats: dict
) -> dict:
    """Compare serial vs concurrent wall time; flag whether batching helped.

    A crashed or missing run has wall_seconds <= 0 and makes the report
    invalid: it is never scored as a win.
    """
    serial_walls = [_wall(u) for u in serial_usages]
    concurrent_walls = [_wall(u) for u in concurrent_usages]
    serial_wall_sum = sum(serial_walls)
    concurrent_wall_max = max(concurrent_walls, default=0.0)
    n_failed = sum(1 for w in serial_walls + concurrent_walls if w <= 0)
    valid = bool(serial_walls) and bool(concurrent_walls) and n_failed == 0
    batches_run = vaayu_stats.get("batches_run", 0)
    return {
        "n_serial": len(serial_usages),
        "n_concurrent": len(concurrent_usages),
        "serial_wall_sum": serial_wall_sum,
        "concurrent_wall_max": concurrent_wall_max,
        "speedup": (serial_wall_sum / concurrent_wall_max if concurrent_wall_max else None),
        "batches_run": batches_run,
        "gate1_refused": vaayu_stats.get("gate1_refused", 0),
        "gate2_refused": vaayu_stats.get("gate2_refused", 0),
        "valid": valid,
        "n_failed": n_failed,
        "batching_helped": valid and bool(batches_run) and concurrent_wall_max < serial_wall_sum,
    }


def _run_one(task: str, project: Path) -> dict:
    """Run one rudra session in `project`; return its usage.json ({} if absent)."""
    proc = subprocess.run(
        ["rudra", "--auto", "--allow-shell", task],
        cwd=project,
        check=False,
    )
    if proc.returncode != 0:
        print(f"WARNING: rudra exited {proc.returncode} in {project}", file=sys.stderr)
        failed = True
    else:
        failed = False
    usage = project / ".rudra" / "run" / "logs" / "usage.json"
    try:
        data = json.loads(usage.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"WARNING: no readable usage.json in {project}", file=sys.stderr)
        return {"_failed": True}
    if failed:
        data["_failed"] = True  # non-zero exit: counts as failed whatever usage says
    return data


def run_serial(task: str, n: int, root: Path) -> list[dict]:
    out = []
    for i in range(n):
        project = root / f"serial-{i}"
        project.mkdir()
        out.append(_run_one(task, project))
    return out


def run_concurrent(task: str, n: int, root: Path) -> list[dict]:
    projects = []
    for i in range(n):
        project = root / f"concurrent-{i}"
        project.mkdir()
        projects.append(project)
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda p: _run_one(task, p), projects))


def read_vaayu_stats(path: Path | None) -> dict:
    """Read a JSON file of Vaayu stats (batches_run, gate1_refused, gate2_refused)."""
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=3, help="sessions per arm")
    parser.add_argument("--task", required=True, help="prompt given to each rudra session")
    parser.add_argument("--vaayu-stats", type=Path, help="JSON file with Vaayu scheduler stats")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="rudra-measure-") as tmp:
        root = Path(tmp)
        serial = run_serial(args.task, args.n, root)
        concurrent = run_concurrent(args.task, args.n, root)
    report = aggregate_report(serial, concurrent, read_vaayu_stats(args.vaayu_stats))

    out_dir = Path(__file__).parent / "out"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"measure-{time.strftime('%Y%m%d-%H%M%S')}.json"
    text = json.dumps(report, indent=2)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
