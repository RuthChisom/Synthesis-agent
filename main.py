"""
Autonomous GitHub issue-solving agent — main entry point.

Loop:
  1. For each target repo, fetch open issues.
  2. For each issue with an ETH bounty, evaluate with BountyEvaluator.
  3. Solve accepted issues with IssueSolver and submit PRs.
  4. Poll open PRs; on merge, log expected ETH payment.

Environment variables (see .env.example):
  ANTHROPIC_API_KEY   — required
  GITHUB_TOKEN        — required
  GITHUB_USERNAME     — agent's GitHub username (must own the token)
  TARGET_REPOS        — comma-separated "owner/repo" list
  ETH_PRIVATE_KEY     — for receiving payments (optional in solve-only mode)
  ETH_RPC_URL         — for monitoring wallet (optional in solve-only mode)
  POLL_INTERVAL       — seconds between full scan cycles (default: 300)
  MIN_BOUNTY_ETH      — minimum bounty to consider (default: 0.001)
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv

from bounty import extract_from_issue
from evaluator import BountyEvaluator, EvaluationResult, format_tech_stack
from github_client import GithubClient, IssueInfo, PRInfo
from identity import load_public_identity, verify_identity
from implementer import ImplementationResult, SeniorEngineer
from planner import IssuePlanner
from solver import IssueSolver, FileChange, Solution
from state import State

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _require(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        log.error("Missing required environment variable: %s", key)
        sys.exit(1)
    return value


def _config() -> dict:
    return {
        "anthropic_key": _require("ANTHROPIC_API_KEY"),
        "github_token": _require("GITHUB_TOKEN"),
        "github_username": _require("GITHUB_USERNAME"),
        "target_repos": [
            r.strip()
            for r in _require("TARGET_REPOS").split(",")
            if r.strip()
        ],
        "poll_interval": int(os.environ.get("POLL_INTERVAL", "300")),
        "min_bounty_eth": float(os.environ.get("MIN_BOUNTY_ETH", "0.001")),
    }


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _solution_from_implementation(
    result: ImplementationResult,
    issue: IssueInfo,
) -> Solution:
    """Convert an ImplementationResult into a Solution ready for PR submission."""
    changes = [
        FileChange(path=f.file_path, content=f.content, action="modify")
        for f in result.updated_files
    ]
    title = f"Fix: {issue.title}"[:72]
    body = (
        f"{result.summary_of_changes}\n\n"
        f"Closes #{issue.number}"
    )
    return Solution(changes=changes, pr_title=title, pr_body=body, confidence="high")


def process_issue(
    issue: IssueInfo,
    bounty_eth: float,
    gh: GithubClient,
    evaluator: BountyEvaluator,
    planner: IssuePlanner,
    engineer: SeniorEngineer,
    solver: IssueSolver,
    state: State,
) -> None:
    """Evaluate, plan, solve, and submit a PR for one bounty issue."""
    log.info(
        "Evaluating issue #%d in %s  [bounty: %.4f ETH]",
        issue.number,
        issue.repo_full_name,
        bounty_eth,
    )

    # Gather repo context (shared by evaluator and planner).
    languages = gh.get_languages(issue.repo_full_name)
    tech_stack = format_tech_stack(languages)
    repo_description = gh.get_repo_description(issue.repo_full_name)
    recent_commits = gh.get_recent_commit_count(issue.repo_full_name, days=30)

    # Step 1 — Evaluate: structured senior-engineer verdict.
    evaluation: EvaluationResult = evaluator.evaluate(
        repo_name=issue.repo_full_name,
        repo_description=repo_description,
        tech_stack=tech_stack,
        issue_title=issue.title,
        issue_body=issue.body,
        bounty_amount=bounty_eth,
        recent_commits=recent_commits,
    )

    log.info(
        "Issue #%d evaluation: should_attempt=%s  type=%s  "
        "hours=%.1f  p(success)=%.2f  ev=%.4f ETH  reason=%s",
        issue.number,
        evaluation.should_attempt,
        evaluation.issue_type,
        evaluation.estimated_hours,
        evaluation.success_probability,
        evaluation.expected_value,
        evaluation.reason,
    )

    if not evaluation.should_attempt:
        log.info("Issue #%d skipped: %s", issue.number, evaluation.reason)
        state.mark_skipped(issue.url, issue.repo_full_name, issue.number)
        return

    # Step 2 — Plan: architect-level breakdown of exact changes needed.
    file_tree = gh.get_file_tree(issue.repo_full_name)
    plan = planner.plan(
        issue_title=issue.title,
        issue_body=issue.body,
        file_tree=file_tree,
        tech_stack=tech_stack,
    )

    if plan.steps:
        log.info(
            "Issue #%d plan: %d steps, %d files, %d edge cases, %d tests",
            issue.number,
            len(plan.steps),
            len(plan.all_files),
            len(plan.edge_cases),
            len(plan.tests_needed),
        )
    else:
        log.info("Issue #%d: planner returned empty plan — proceeding without it.", issue.number)

    # Step 3 — Implement: read the exact files the plan named, then write the fix.
    solution: Solution | None = None
    if plan.steps:
        files_content = gh.read_files(issue.repo_full_name, plan.all_files)
        log.info(
            "Issue #%d: read %d/%d planned files for implementer.",
            issue.number,
            len(files_content),
            len(plan.all_files),
        )
        impl: ImplementationResult = engineer.implement(plan, files_content)
        if impl.succeeded:
            log.info(
                "Issue #%d: implementer produced %d file(s) — %s",
                issue.number,
                len(impl.updated_files),
                impl.summary_of_changes,
            )
            solution = _solution_from_implementation(impl, issue)

    # Step 4 — Fallback: if implementer produced nothing, use the multi-turn solver.
    if solution is None:
        log.info(
            "Issue #%d: implementer yielded no output — falling back to solver.",
            issue.number,
        )
        solution = solver.solve(issue, languages, plan=plan)

    if solution is None:
        log.info("Issue #%d: no solution generated.", issue.number)
        state.mark_failed(issue.url, issue.repo_full_name, issue.number)
        return

    log.info(
        "Issue #%d: solution ready  confidence=%s  files=%d",
        issue.number,
        solution.confidence,
        len(solution.changes),
    )

    # Fork + push + open PR.
    try:
        fork_name = gh.ensure_fork(issue.repo_full_name)
        pr = gh.push_changes_and_open_pr(
            base_repo_full_name=issue.repo_full_name,
            fork_full_name=fork_name,
            branch_name=solution.branch_name,
            file_changes=solution.file_map,
            pr_title=solution.pr_title,
            pr_body=solution.pr_body,
            issue_number=issue.number,
        )
    except Exception as exc:
        log.error("Issue #%d: failed to submit PR: %s", issue.number, exc)
        state.mark_failed(issue.url, issue.repo_full_name, issue.number)
        return

    state.mark_submitted(
        issue_url=issue.url,
        repo=issue.repo_full_name,
        issue_number=issue.number,
        pr_url=pr.url,
        pr_number=pr.number,
        bounty_eth=bounty_eth,
        branch=solution.branch_name,
    )
    log.info("Issue #%d: PR opened at %s", issue.number, pr.url)


def check_open_prs(gh: GithubClient, state: State) -> None:
    """Poll submitted PRs and log when they are merged."""
    for job in state.all_submitted():
        if job.pr_number is None:
            continue
        try:
            pr_info: PRInfo = gh.get_pr_info(job.repo, job.pr_number)
        except Exception as exc:
            log.warning(
                "Could not fetch PR #%d in %s: %s", job.pr_number, job.repo, exc
            )
            continue

        if pr_info.merged:
            log.info(
                "PR #%d in %s MERGED — expected payment: %.4f ETH",
                job.pr_number,
                job.repo,
                job.bounty_eth or 0,
            )
            state.mark_merged(job.issue_url)
        elif pr_info.state == "closed":
            log.info("PR #%d in %s closed without merge.", job.pr_number, job.repo)
            state.mark_failed(job.issue_url, job.repo, job.issue_number)


def scan_repos(
    gh: GithubClient,
    evaluator: BountyEvaluator,
    planner: IssuePlanner,
    engineer: SeniorEngineer,
    solver: IssueSolver,
    state: State,
    config: dict,
) -> None:
    """One full scan: evaluate, plan, implement, and solve bounty issues."""
    min_bounty = config["min_bounty_eth"]

    for repo_name in config["target_repos"]:
        log.info("Scanning %s ...", repo_name)
        try:
            issues = gh.get_open_issues(repo_name)
        except Exception as exc:
            log.error("Could not fetch issues from %s: %s", repo_name, exc)
            continue

        for issue in issues:
            if state.is_known(issue.url):
                continue

            bounty_eth = extract_from_issue(issue.body, issue.comment_bodies)
            if bounty_eth is None or bounty_eth < min_bounty:
                continue

            process_issue(issue, bounty_eth, gh, evaluator, planner, engineer, solver, state)

    check_open_prs(gh, state)
    log.info("State: %s", state.summary())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    identity = load_public_identity()
    if not verify_identity(identity):
        log.error("Identity signature invalid — run register.py --verify")
        sys.exit(1)

    log.info(
        "Agent: %s  |  %s  |  track: %s",
        identity["name"],
        identity["address"],
        identity["track"],
    )

    cfg = _config()
    gh = GithubClient(cfg["github_token"], cfg["github_username"])
    evaluator = BountyEvaluator(cfg["anthropic_key"])
    planner = IssuePlanner(cfg["anthropic_key"])
    engineer = SeniorEngineer(cfg["anthropic_key"])
    solver = IssueSolver(cfg["anthropic_key"], gh)
    state = State()

    log.info(
        "Watching repos: %s  |  poll every %ds  |  min bounty: %.4f ETH",
        ", ".join(cfg["target_repos"]),
        cfg["poll_interval"],
        cfg["min_bounty_eth"],
    )

    while True:
        try:
            scan_repos(gh, evaluator, planner, engineer, solver, state, cfg)
        except KeyboardInterrupt:
            log.info("Interrupted — shutting down.")
            break
        except Exception as exc:
            log.error("Unhandled error in scan loop: %s", exc, exc_info=True)

        log.info("Sleeping %ds until next scan ...", cfg["poll_interval"])
        time.sleep(cfg["poll_interval"])


if __name__ == "__main__":
    main()
