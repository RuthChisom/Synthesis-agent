"""
PR status evaluator — tracks whether a submitted PR is moving toward payment.

Runs during every poll cycle for each open submitted PR. Its verdict drives
three outcomes:
  wait    — PR looks healthy, nothing to do yet
  revise  — maintainer requested changes; flag for human review or re-solve
  abandon — PR rejected or stale; stop tracking to save API budget

INPUT:
  - pr_status    : "open" | "closed" | "merged"
  - comments     : raw text of all PR comments (maintainer feedback)
  - issue_status : "open" | "closed"

OUTPUT (strict JSON):
{
  "pr_accepted": true/false,
  "payment_expected": true/false,
  "next_action": "wait|revise|abandon"
}
"""

import json
import logging
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a system evaluator for an autonomous coding agent that earns ETH bounties
by solving GitHub issues and submitting pull requests.

Your job: look at the PR's current status, any maintainer comments, and the linked
issue status, then decide whether payment is likely and what to do next.

Decision rules:
- pr_accepted = true  → PR is merged, or maintainer has given clear approval ("LGTM",
  "looks good", "will merge")
- pr_accepted = false → PR is open with no approval, has change requests, or is closed
  without merge

- payment_expected = true  → pr_accepted AND issue is closed (or will be by the merge)
- payment_expected = false → otherwise

- next_action:
  "wait"    → PR is open, no blocking feedback, maintainer is neutral or positive
  "revise"  → Maintainer left specific change requests or questions that need addressing
  "abandon" → PR is closed without merge, maintainer rejected it, or issue is already
              resolved by another PR

Respond with ONLY valid JSON — no markdown, no commentary:
{
  "pr_accepted": true/false,
  "payment_expected": true/false,
  "next_action": "wait|revise|abandon"
}
"""

_USER_TEMPLATE = """\
## INPUT:
- PR status: {pr_status}
- Maintainer feedback: {comments}
- Issue status: {issue_status}

## TASK:
Evaluate whether the PR is accepted and whether payment is likely.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MonitorResult:
    pr_accepted: bool
    payment_expected: bool
    next_action: str  # "wait" | "revise" | "abandon"

    @classmethod
    def wait(cls) -> "MonitorResult":
        """Default optimistic result when the evaluator cannot run."""
        return cls(pr_accepted=False, payment_expected=False, next_action="wait")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class PRStatusEvaluator:
    """
    Evaluates the payment outlook for a submitted PR.
    Single claude-haiku call — cheap, runs once per open PR per poll cycle.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def evaluate(
        self,
        pr_status: str,
        comments: list[str],
        issue_status: str,
    ) -> MonitorResult:
        """
        Return a MonitorResult. Falls back to MonitorResult.wait() on any
        error — cautious optimism is better than silently abandoning a PR.
        """
        comments_text = _format_comments(comments)

        user_message = _USER_TEMPLATE.format(
            pr_status=pr_status,
            comments=comments_text,
            issue_status=issue_status,
        )

        try:
            response = self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("PRStatusEvaluator API call failed: %s", exc)
            return MonitorResult.wait()

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("PRStatusEvaluator returned non-JSON: %s  raw=%r", exc, raw[:200])
            return MonitorResult.wait()

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_comments(comments: list[str]) -> str:
    if not comments:
        return "(no comments yet)"
    # Include up to the last 10 comments to stay within token budget.
    recent = comments[-10:]
    return "\n---\n".join(c.strip() for c in recent if c.strip()) or "(no comments yet)"


def _parse_result(data: dict) -> MonitorResult:
    try:
        next_action = str(data.get("next_action", "wait"))
        if next_action not in ("wait", "revise", "abandon"):
            next_action = "wait"
        return MonitorResult(
            pr_accepted=bool(data.get("pr_accepted", False)),
            payment_expected=bool(data.get("payment_expected", False)),
            next_action=next_action,
        )
    except (TypeError, ValueError) as exc:
        log.warning("Failed to parse monitor result: %s  data=%r", exc, data)
        return MonitorResult.wait()
