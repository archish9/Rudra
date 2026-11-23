"""Local probe: does laya's failure_class clear the gate on a missing-module tail?"""

from rudra.decide.catalog import FAILURE_CLASS
from rudra.decide.engine import DecisionEngine
from rudra.decide.gates import recovery_route


from pathlib import Path

from rudra.config import build_config

cfg = build_config(Path.cwd())
eng = DecisionEngine.build(cfg)
print("available:", eng.available(), "backend:", getattr(eng._backend, "name", None))

samples = {
    "missing_dep": "E   ModuleNotFoundError: No module named 'rudra_ghost_pkg_xyz'",
    "assert": "E   assert add(2, 2) == 5",
    "attr": "E   AttributeError: 'Foo' object has no attribute 'bar'",
    "flaky": "E   ConnectionError: Connection reset by peer during setup",
}
for label, go in samples.items():
    d = eng.decide({"gate_output": go}, FAILURE_CLASS)["failure_class"]
    pkgs = ("rudra_ghost_pkg_xyz",) if label == "missing_dep" else ()
    act = recovery_route(eng, go, packages=pkgs)
    print(
        f"{label:11s} value={str(d.value):22s} conf={d.confidence:.3f} "
        f"abstain={d.abstain} route={d.route} action={act}"
    )
