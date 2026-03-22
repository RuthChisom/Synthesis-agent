"""
Structured emoji logging helpers for each pipeline stage.

Import and call these instead of bare log.info() to ensure every stage
produces consistent, human-readable output that is easy to scan at a glance.

All functions accept an issue_number as the first argument so log lines
can be correlated across the full pipeline for a single issue.
"""

import logging

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Stage loggers
# ---------------------------------------------------------------------------

def log_discovery(
    issue_number: int,
    accepted: bool,
    reason: str,
    probability: float,
    expected_value: float,
) -> None:
    """🕵️  Discovery Agent — accepted or rejected the issue."""
    status = "ACCEPTED" if accepted else "REJECTED"
    log.info(
        "🕵️  Discovery Agent   #%d %s  p=%.2f  ev=%.4f ETH  reason=%s",
        issue_number, status, probability, expected_value, reason,
    )


def log_plan(issue_number: int, steps: int, files: int) -> None:
    """🧩 Planner Agent — plan created."""
    log.info(
        "🧩 Planner Agent     #%d plan created  steps=%d  files=%d",
        issue_number, steps, files,
    )


def log_build(issue_number: int, files: int, summary: str) -> None:
    """🛠  Builder Agent — code generated."""
    log.info(
        "🛠  Builder Agent     #%d code generated  files=%d  summary=%s",
        issue_number, files, summary,
    )


def log_test(issue_number: int, files: int) -> None:
    """🧪 Tester Agent — tests created."""
    log.info(
        "🧪 Tester Agent      #%d tests created  files=%d",
        issue_number, files,
    )


def log_outcome(issue_number: int, passed: bool, detail: str = "") -> None:
    """✅ Outcome check — result of static analysis / test execution."""
    status = "PASSED" if passed else "FAILED"
    msg = f"✅ Outcome check     #{issue_number} {status}"
    if detail:
        msg += f"  [{detail}]"
    if passed:
        log.info(msg)
    else:
        log.warning(msg)


def log_review(
    issue_number: int,
    approved: bool,
    priority: str,
    issues: list[str],
) -> None:
    """🔍 Reviewer Agent — approved or rejected."""
    status = "APPROVED" if approved else "REJECTED"
    log.info(
        "🔍 Reviewer Agent    #%d %s  priority=%s  issues=%d",
        issue_number, status, priority, len(issues),
    )
    for issue in issues:
        log.info("   ↳ %s", issue)


def log_fix_loop(issue_number: int, cycle: int, max_cycles: int) -> None:
    """🔁 Fix loop — iteration count."""
    log.info(
        "🔁 Fix loop          #%d iteration %d/%d",
        issue_number, cycle, max_cycles,
    )


def log_pr(issue_number: int, pr_url: str) -> None:
    """🚀 PR created — URL."""
    log.info("🚀 PR created        #%d → %s", issue_number, pr_url)


def log_audit(
    issue_number: int,
    success: bool,
    confidence: float,
    problems: int,
    improvements: int = 0,
) -> None:
    """📋 Audit — final pipeline evaluation."""
    status = "SUCCESS" if success else "FAILED"
    log.info(
        "📋 Audit             #%d %s  confidence=%.2f  problems=%d  improvements=%d",
        issue_number, status, confidence, problems, improvements,
    )


def log_final(issue_number: int, result) -> None:
    """
    📊 Final result — one-line summary of the entire run.

    Accepts a FinalResult object.
    """
    log.info(
        "📊 Final result      #%d status=%s  pr=%s  confidence=%.2f  errors=%d",
        issue_number,
        result.status,
        result.pr_url or "none",
        result.confidence,
        len(result.errors),
    )
