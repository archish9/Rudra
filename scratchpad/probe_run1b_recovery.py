"""Verify the collection-error fix on run1b's REAL recorded block output.

run1b (Vaayu) left t1 BLOCKED with this exact block_gate_output (a pytest
collection ImportError). Pre-fix, missing_packages returned () -> acted=False.
Post-fix, missing_modules_in_text names the package -> recovery_route acts.
Real laya (MLX), real gate text.
"""

from pathlib import Path

from rudra.config import build_config
from rudra.decide.engine import DecisionEngine
from rudra.decide.gates import recovery_route
from rudra.loop.dependencies import missing_modules_in_text

# Verbatim from run1b's ledger.json t1.block_gate_output.
REAL_BLOCK = "ModuleNotFoundError: No module named 'rudra_fx_client'"

pkgs = missing_modules_in_text(REAL_BLOCK, lambda name: False)
print("missing_modules_in_text ->", pkgs)

eng = DecisionEngine.build(build_config(Path.cwd()))
action = recovery_route(eng, REAL_BLOCK, packages=pkgs)
print("recovery_route ->", action)
