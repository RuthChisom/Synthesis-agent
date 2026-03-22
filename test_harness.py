"""
Test harness — runs the full agent pipeline against mock issues.

Defines a small set of representative test cases (with known expected
outcomes) and runs each through the evaluator to validate the pipeline's
decision logic without touching real GitHub repositories.

Usage:
    python test_harness.py

Requires: ANTHROPIC_API_KEY in the environment (or .env file).

Exit code:
    0 — all test cases produced the expected result
    1 — one or more cases failed or errored
"""

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_harness")


# ---------------------------------------------------------------------------
# Mock issue definitions
# ---------------------------------------------------------------------------

MOCK_ISSUES = [
    {
        "id": "mock-001",
        "title": "Fix off-by-one error in pagination",
        "body": (
            "## Bug Report\n\n"
            "The pagination returns N+1 results instead of N when `limit` equals "
            "the total count.\n\n"
            "**Bounty**: 0.005 ETH\n\n"
            "Steps to reproduce:\n"
            "1. Set limit=10 with exactly 10 items\n"
            "2. Observe 11 items returned"
        ),
        "bounty_eth": 0.005,
        "repo": "mock/repo",
        "expected_attempt": True,
    },
    {
        "id": "mock-002",
        "title": "Update README typo",
        "body": (
            "## Docs Fix\n\n"
            "There is a typo on line 3 of README.md.\n\n"
            "**Bounty**: 0.0001 ETH"
        ),
        "bounty_eth": 0.0001,
        "repo": "mock/repo",
        # Below MIN_BOUNTY_ETH default (0.001) so the scanner never calls process_issue.
        # At the evaluator level this would also be rejected on low expected_value.
        "expected_attempt": False,
    },
    {
        "id": "mock-003",
        "title": "Add input validation to user registration",
        "body": (
            "## Feature Request\n\n"
            "The registration endpoint does not validate email format or "
            "password length. Please add:\n"
            "- Email format validation (RFC 5322)\n"
            "- Password minimum 8 chars, 1 uppercase, 1 number\n\n"
            "**Bounty**: 0.01 ETH"
        ),
        "bounty_eth": 0.01,
        "repo": "mock/repo",
        "expected_attempt": True,
    },
    {
        "id": "mock-004",
        "title": "Rewrite entire authentication system from scratch",
        "body": (
            "## Major Refactor\n\n"
            "Please rewrite the entire auth system (JWT, OAuth2, MFA, "
            "session management, role-based access control) from the ground up "
            "in under 2 hours.\n\n"
            "**Bounty**: 0.002 ETH"
        ),
        "bounty_eth": 0.002,
        "repo": "mock/repo",
        # High hours estimate + low EV → evaluator should reject.
        "expected_attempt": False,
    },
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_mock_agent(case: dict, anthropic_key: str) -> dict:
    """
    Run the evaluator for a single test case and compare to expected_attempt.

    Only the evaluator step is exercised here to keep the harness fast and
    free of real GitHub dependencies. The full pipeline can be tested by
    pointing TARGET_REPOS at a sandbox repository.
    """
    from evaluator import BountyEvaluator, format_tech_stack

    evaluator = BountyEvaluator(anthropic_key)
    result = evaluator.evaluate(
        repo_name=case["repo"],
        repo_description="Mock repository for test harness",
        tech_stack=format_tech_stack({"Python": 10_000}),
        issue_title=case["title"],
        issue_body=case["body"],
        bounty_amount=case["bounty_eth"],
        recent_commits=5,
    )

    passed = result.should_attempt == case["expected_attempt"]
    return {
        "id": case["id"],
        "title": case["title"],
        "status": "SUCCESS" if passed else "FAILED",
        "should_attempt": result.should_attempt,
        "expected_attempt": case["expected_attempt"],
        "confidence": result.success_probability,
        "pr_url": None,
        "reason": result.reason,
        "errors": (
            []
            if passed
            else [
                f"Expected should_attempt={case['expected_attempt']}, "
                f"got {result.should_attempt} (reason: {result.reason})"
            ]
        ),
    }


def main() -> None:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        log.error("ANTHROPIC_API_KEY not set — cannot run test harness")
        sys.exit(1)

    log.info("=" * 62)
    log.info("  Test Harness — %d mock issue(s)", len(MOCK_ISSUES))
    log.info("=" * 62)

    results: list[dict] = []
    for case in MOCK_ISSUES:
        log.info("▶  Running %s: %s", case["id"], case["title"])
        try:
            outcome = run_mock_agent(case, anthropic_key)
        except Exception as exc:
            outcome = {
                "id": case["id"],
                "title": case["title"],
                "status": "ERROR",
                "pr_url": None,
                "confidence": 0.0,
                "errors": [str(exc)],
            }
        results.append(outcome)

        icon = "✅" if outcome["status"] == "SUCCESS" else "❌"
        log.info(
            "%s [%s] %s  confidence=%.2f",
            icon,
            outcome["status"],
            outcome["id"],
            outcome.get("confidence", 0.0),
        )
        for err in outcome.get("errors", []):
            log.info("   ↳ %s", err)

    passed = sum(1 for r in results if r["status"] == "SUCCESS")
    total = len(results)

    log.info("=" * 62)
    log.info("Results: %d/%d passed", passed, total)
    log.info("=" * 62)

    # Print the final result objects for each case.
    for r in results:
        log.info(
            "  %s  status=%-8s  pr=%s  errors=%d",
            r["id"],
            r["status"],
            r.get("pr_url") or "none",
            len(r.get("errors", [])),
        )

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
