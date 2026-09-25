"""Which gates failed ONLY on a package nobody declared (OPEN-161).

Every tail below is cut from a real gate in the run archive, the E-lines
verbatim: run `4989aefefacb` t1, t11 and t12, and run `ea0cafc09997` t4.
"""

from __future__ import annotations

from rudra.loop.dependencies import local_module_names, missing_packages
from rudra.verify.pipeline import _parse_findings
from rudra.verify.result import FAILED, PASSED, StageResult, VerifyReport

# 4989aefefacb t1, gate 2: the coder's app imports sqlalchemy, nobody declared it.
SQLALCHEMY = """\
============================= test session starts ==============================
collected 0 items / 1 error

==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_main.py ______________________
ImportError while importing test module '/p/tests/test_main.py'.
Traceback:
tests/test_main.py:3: in <module>
    from app.main import app
app/database.py:1: in <module>
    from sqlalchemy import create_engine
E   ModuleNotFoundError: No module named 'sqlalchemy'
=========================== short test summary info ============================
ERROR tests/test_main.py
=============================== 1 error in 0.13s ===============================
"""

# 4989aefefacb t1, gate 3: declared sqlalchemy, and greenlet was behind it.
GREENLET = """\
collected 0 items / 1 error

==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_main.py ______________________
ImportError while importing test module '/p/tests/test_main.py'.
Traceback:
.venv/lib/python3.12/site-packages/sqlalchemy/util/concurrency.py:70: in _initialize
    from greenlet import getcurrent
E   ModuleNotFoundError: No module named 'greenlet'

The above exception was the direct cause of the following exception:
app/database.py:4: in <module>
    engine = create_async_engine(URL)
E   ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed.  In order to ensure this dependency is available, use the 'sqlalchemy[asyncio]' install target:  'pip install sqlalchemy[asyncio]'
=========================== short test summary info ============================
ERROR tests/test_main.py
=============================== 1 error in 1.20s ===============================
"""

# 4989aefefacb t11, gate 3: FastAPI raises a RuntimeError, not an ImportError.
MULTIPART = """\
collected 25 items / 1 error

==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_main.py ______________________
.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py:129: in ensure_multipart_is_installed
    raise RuntimeError(multipart_not_installed_error) from None
E   RuntimeError: Form data requires "python-multipart" to be installed.
E   You can install "python-multipart" with:
E
E   pip install python-multipart
=========================== short test summary info ============================
ERROR tests/test_main.py - RuntimeError: Form data requires "python-multipart...
========================= 4 warnings, 1 error in 0.32s =========================
"""

# ea0cafc09997 t4, gate 2: a failing TEST, not a collection error.
PYDANTIC = """\
collected 8 items

test_crud_import.py F.......                                             [100%]

=================================== FAILURES ===================================
_________________________________ test_import __________________________________
    def test_import():
>       import crud
crud.py:2: in <module>
    from pydantic import BaseModel
E   ModuleNotFoundError: No module named 'pydantic'
=========================== short test summary info ============================
FAILED test_crud_import.py::test_import - ModuleNotFoundError: No module name...
========================= 1 failed, 7 passed in 0.11s ==========================
"""

# 4989aefefacb t11, gate 1: a missing package AND a bug in the project's code.
MIXED = """\
collected 4 items / 2 errors

==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_main.py ______________________
tests/test_main.py:5: in <module>
    from app.models import User
E   AttributeError: module 'app.models' has no attribute 'User'
_________________ ERROR collecting tests/unit/test_schemas.py __________________
.venv/lib/python3.12/site-packages/pydantic/networks.py:966: in import_email_validator
    import email_validator
E   ModuleNotFoundError: No module named 'email_validator'

The above exception was the direct cause of the following exception:
.venv/lib/python3.12/site-packages/pydantic/networks.py:968: in import_email_validator
    raise ImportError("email-validator is not installed, run `pip install 'pydantic[email]'`") from e
E   ImportError: email-validator is not installed, run `pip install 'pydantic[email]'`
=========================== short test summary info ============================
ERROR tests/test_main.py - AttributeError: module 'app.models' has no attribu...
ERROR tests/unit/test_schemas.py
========================= 1 warning, 2 errors in 0.30s =========================
"""

ASSERTION = """\
collected 1 item

tests/test_a.py F                                                        [100%]

=================================== FAILURES ===================================
____________________________________ test_a ____________________________________
    def test_a():
>       assert 1 == 2
E       assert 1 == 2
=========================== short test summary info ============================
FAILED tests/test_a.py::test_a - assert 1 == 2
============================== 1 failed in 0.01s ===============================
"""


def _gate(tail: str, name: str = "test") -> VerifyReport:
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name=name,
                outcome=FAILED,
                blocking=True,
                findings=_parse_findings(tail),
                output_tail=tail,
                detail="1 run, 1 failed, 0 skipped",
            ),
        ]
    )


def _nothing_local(name: str) -> bool:
    return False


def test_a_missing_import_the_project_wrote_is_named():
    assert missing_packages(_gate(SQLALCHEMY), _nothing_local) == ("sqlalchemy",)


def test_a_package_behind_another_is_named_through_its_chained_import_error():
    """The ImportError after `The above exception…` names no module, only an
    install target; it is part of the same missing package, not a second
    failure."""
    assert missing_packages(_gate(GREENLET), _nothing_local) == ("greenlet", "sqlalchemy[asyncio]")


def test_fastapi_s_runtime_error_is_read_by_its_pip_install_line():
    """The Step 0 finding that widened option A: a detector keyed on
    ImportError alone misses this one of the five."""
    assert missing_packages(_gate(MULTIPART), _nothing_local) == ("python-multipart",)


def test_a_failing_test_that_only_failed_on_an_import_counts_too():
    assert missing_packages(_gate(PYDANTIC), _nothing_local) == ("pydantic",)


def test_a_gate_that_also_failed_on_the_project_s_code_is_not_a_dependency_gate():
    """t11's first gate: the attempt it follows had real work left, so it is charged."""
    assert missing_packages(_gate(MIXED), _nothing_local) == ()


def test_a_module_the_project_holds_is_not_a_package():
    """OPEN-140's `No module named 'main'`: a path defect, not a dependency."""
    tail = SQLALCHEMY.replace("sqlalchemy", "main")
    assert missing_packages(_gate(tail), lambda name: name == "main") == ()


def test_a_submodule_of_a_project_package_is_not_a_package():
    tail = SQLALCHEMY.replace("'sqlalchemy'", "'app.models'")
    assert missing_packages(_gate(tail), lambda name: name == "app") == ()


def test_a_missing_standard_library_module_is_not_a_package():
    tail = SQLALCHEMY.replace("'sqlalchemy'", "'_ctypes'")
    assert missing_packages(_gate(tail), _nothing_local) == ()


def test_an_import_of_a_name_the_project_did_not_define_is_not_a_package():
    tail = SQLALCHEMY.replace(
        "ModuleNotFoundError: No module named 'sqlalchemy'",
        "ImportError: cannot import name 'get_password_hash' from 'app.security' (/p/app/security.py)",
    )
    assert missing_packages(_gate(tail), _nothing_local) == ()


def test_an_assertion_is_not_a_package():
    assert missing_packages(_gate(ASSERTION), _nothing_local) == ()


def test_a_truncated_tail_cannot_say_every_failure_was_a_package():
    tail = "[... 982 earlier characters omitted ...]\n" + SQLALCHEMY
    assert missing_packages(_gate(tail), _nothing_local) == ()


def test_only_the_test_stage_is_read():
    assert missing_packages(_gate(SQLALCHEMY, name="typecheck"), _nothing_local) == ()


def test_a_passing_gate_names_nothing():
    report = VerifyReport.from_stages([StageResult(name="test", outcome=PASSED, blocking=True)])
    assert missing_packages(report, _nothing_local) == ()


def test_local_module_names_are_the_project_s_packages_and_modules(tmp_path):
    (tmp_path / "app" / "routers").mkdir(parents=True)
    (tmp_path / "app" / "routers" / "todos.py").write_text("x = 1\n")
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("x\n")
    (tmp_path / ".venv" / "lib" / "sqlalchemy").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "sqlalchemy" / "__init__.py").write_text("")

    names = local_module_names(tmp_path)

    assert {"app", "routers", "todos", "main"} <= names
    assert "sqlalchemy" not in names, "the venv is build output, not the project"
    assert "README" not in names
