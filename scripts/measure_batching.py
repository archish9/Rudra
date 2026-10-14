"""Price Vaayu's N>1 batched decode: N Rudra sessions concurrent vs serial.

`aggregate_report` is pure and unit-tested. `main` launches real `rudra`
processes and needs a running Vaayu server; it is not covered by tests.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _wall(usage: dict) -> float:
    return float(usage.get("run", {}).get("wall_seconds", 0.0))


def aggregate_report(
    serial_usages: list[dict], concurrent_usages: list[dict], vaayu_stats: dict
) -> dict:
    """Compare serial vs concurrent wall time; flag whether batching helped."""
    serial_wall_sum = sum(_wall(u) for u in serial_usages)
    concurrent_wall_max = max((_wall(u) for u in concurrent_usages), default=0.0)
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
        "batching_helped": bool(batches_run) and concurrent_wall_max < serial_wall_sum,
    }


def _run_one(task: str, project: Path) -> dict:
    """Run one rudra session in `project`; return its usage.json ({} if absent)."""
    subprocess.run(
        ["rudra", "--auto", "--allow-shell", task],
        cwd=project,
        check=False,
    )
    usage = project / ".rudra" / "run" / "logs" / "usage.json"
    try:
        return json.loads(usage.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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
