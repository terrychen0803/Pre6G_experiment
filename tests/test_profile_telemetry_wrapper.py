from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_profile_with_telemetry.py"
SPEC = importlib.util.spec_from_file_location("run_profile_with_telemetry", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_zero_returncode_is_accepted_by_default() -> None:
    assert MODULE._is_command_returncode_accepted(0, []) is True


def test_sigterm_143_requires_explicit_acceptance() -> None:
    assert MODULE._is_command_returncode_accepted(143, []) is False
    assert MODULE._is_command_returncode_accepted(143, [143]) is True


def test_other_nonzero_code_remains_failure() -> None:
    assert MODULE._is_command_returncode_accepted(2, [143]) is False
