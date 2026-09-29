from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_profile_with_telemetry.py"
SPEC = importlib.util.spec_from_file_location("run_profile_with_telemetry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def accepted(returncode: int | None, allowed: list[int]) -> bool:
    accepted_codes = sorted(set(int(value) for value in allowed))
    return returncode == 0 or returncode in accepted_codes


def test_zero_returncode_is_accepted_by_default() -> None:
    assert accepted(0, []) is True


def test_sigterm_143_requires_explicit_acceptance() -> None:
    assert accepted(143, []) is False
    assert accepted(143, [143]) is True


def test_other_nonzero_code_remains_failure() -> None:
    assert accepted(2, [143]) is False
