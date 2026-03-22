"""
Outcome validator — verifies that the generated code is safe to submit.

Two modes:
  1. Real test run  — writes files to a temp directory and runs pytest.
                      Used when pytest is available in the environment.
  2. Static analysis — lightweight proxy when pytest is absent:
                      • checks every Python file for SyntaxError
                      • ensures at least one test file contains an assertion

The pipeline calls run_tests_if_possible() after the test-writing step.
If the outcome fails, the PR is NOT submitted.
"""

import ast
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass

log = logging.getLogger(__name__)

_PYTEST_TIMEOUT_SECS = 60


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class TestOutcome:
    passed: bool
    stdout: str
    stderr: str


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_tests_if_possible(
    updated_files: dict[str, str],
    test_files: dict[str, str],
) -> TestOutcome:
    """
    Attempt a real pytest run in a temporary directory.
    Falls back to simulate_test_run() if pytest is unavailable or fails to start.
    """
    if _pytest_available():
        return _run_pytest(updated_files, test_files)
    log.info("pytest unavailable — running static analysis instead")
    return simulate_test_run(updated_files, test_files)


# ---------------------------------------------------------------------------
# Static analysis (fallback)
# ---------------------------------------------------------------------------

def simulate_test_run(
    updated_files: dict[str, str],
    test_files: dict[str, str],
) -> TestOutcome:
    """
    Lightweight static analysis proxy for a real test run.

    Checks:
    - All Python implementation files parse without SyntaxError.
    - At least one test file contains an assert statement or a common
      assertion method (assertEqual, assertTrue, assertIn, …).

    This is intentionally conservative: a syntax error is a hard failure,
    but missing assertions only warn rather than block (the test engineer
    may have written JS/TS tests or doctest-style tests).
    """
    errors: list[str] = []

    # 1. Syntax-check every Python implementation file.
    for path, content in updated_files.items():
        if not path.endswith(".py"):
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            errors.append(f"SyntaxError in {path}: {exc}")

    # 2. Also syntax-check Python test files.
    for path, content in test_files.items():
        if not path.endswith(".py"):
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            errors.append(f"SyntaxError in test file {path}: {exc}")

    if errors:
        return TestOutcome(passed=False, stdout="", stderr="\n".join(errors))

    # 3. Check that at least one test file has a real assertion.
    assertion_markers = (
        "assert ",
        "assertEqual",
        "assertTrue",
        "assertFalse",
        "assertIn",
        "assertRaises",
        "expect(",      # Jest / Vitest
        "toBe(",
        "toEqual(",
    )
    has_assertions = any(
        any(marker in content for marker in assertion_markers)
        for content in test_files.values()
    )
    if test_files and not has_assertions:
        log.warning("No assertions found in any test file — tests may be vacuous")
        # Warn but do not block; the reviewer already evaluated test quality.

    return TestOutcome(
        passed=True,
        stdout="Static analysis passed",
        stderr="",
    )


# ---------------------------------------------------------------------------
# Real pytest run
# ---------------------------------------------------------------------------

def _pytest_available() -> bool:
    try:
        subprocess.run(
            ["python", "-m", "pytest", "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def _run_pytest(
    updated_files: dict[str, str],
    test_files: dict[str, str],
) -> TestOutcome:
    """Write all files to a temp directory and run pytest against them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        all_files = {**updated_files, **test_files}
        for path, content in all_files.items():
            full_path = os.path.join(tmpdir, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as fh:
                fh.write(content)

        try:
            result = subprocess.run(
                ["python", "-m", "pytest", tmpdir, "--tb=short", "-q", "--no-header"],
                capture_output=True,
                text=True,
                timeout=_PYTEST_TIMEOUT_SECS,
            )
            return TestOutcome(
                passed=result.returncode == 0,
                stdout=result.stdout[:3000],
                stderr=result.stderr[:500],
            )
        except subprocess.TimeoutExpired:
            return TestOutcome(
                passed=False,
                stdout="",
                stderr=f"pytest timed out after {_PYTEST_TIMEOUT_SECS}s",
            )
        except Exception as exc:
            return TestOutcome(passed=False, stdout="", stderr=str(exc))
