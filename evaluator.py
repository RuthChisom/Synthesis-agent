"""
Bounty evaluator — decides whether a GitHub issue is worth attempting.

The agent acts as a senior engineer: it reads the issue, the repo context,
and the bounty amount, then returns a structured JSON verdict.

OUTPUT SCHEMA (strict):
{
  "should_attempt": true/false,
  "reason": "...",
  "issue_type": "bug|ui|docs|refactor|test|other",
  "estimated_hours": number,
  "success_probability": 0.0-1.0,
  "expected_value": number        // bounty_amount * success_probability - cost
}
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

log = logging.getLogger(__name__)

# Baseline engineering cost used in expected_value: 0.001 ETH per hour.
# Keeps expected_value in the same unit as bounty_amount (ETH).
_COST_PER_HOUR_ETH = 0.001

_SYSTEM = """\
You are a senior software engineer and bounty evaluator for an autonomous coding agent.

Your role: decide whether a GitHub issue is worth attempting autonomously,
given the effort required and the probability of PR acceptance.

Evaluation criteria:
1. Clarity — are the requirements specific and unambiguous?
2. Scope — is the change small and well-defined (few files, no design shifts)?
3. Likelihood of PR acceptance — is the repo active? Does it accept outside PRs?
4. Effort vs reward — does the expected value justify the work?

REJECT if any of these are true:
- Requirements are vague, ambiguous, or depend on design decisions
- Requires major architecture changes (rewrites, new subsystems)
- Repo activity is low (fewer than 5 commits in the last 30 days)
- Issue requires UI/visual work without precise specs
- Issue requires secrets, credentials, or external service access
- Issue is a feature request with no clear acceptance criteria

Compute:
  expected_value = bounty_amount * success_probability - (estimated_hours * 0.001)

where 0.001 is the baseline ETH cost per engineering hour.

Respond with ONLY valid JSON — no markdown, no commentary, no extra fields:
{
  "should_attempt": true/false,
  "reason": "<one concise sentence>",
  "issue_type": "bug|ui|docs|refactor|test|other",
  "estimated_hours": <number>,
  "success_probability": <0.0–1.0>,
  "expected_value": <number>
}
"""

_USER_TEMPLATE = """\
## INPUT:
- Repo name: {repo_name}
- Repo description: {repo_description}
- Tech stack: {tech_stack}
- Issue title: {issue_title}
- Issue body: {issue_body}
- Bounty amount: {bounty_amount} ETH
- Activity level: {recent_commits} commits in the last 30 days

## TASK:
Evaluate this issue and return your verdict as strict JSON.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    should_attempt: bool
    reason: str
    issue_type: str            # bug | ui | docs | refactor | test | other
    estimated_hours: float
    success_probability: float
    expected_value: float

    def to_dict(self) -> dict:
        return {
            "should_attempt": self.should_attempt,
            "reason": self.reason,
            "issue_type": self.issue_type,
            "estimated_hours": self.estimated_hours,
            "success_probability": self.success_probability,
            "expected_value": self.expected_value,
        }

    @classmethod
    def rejected(cls, reason: str) -> "EvaluationResult":
        return cls(
            should_attempt=False,
            reason=reason,
            issue_type="other",
            estimated_hours=0.0,
            success_probability=0.0,
            expected_value=0.0,
        )


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class BountyEvaluator:
    """
    Evaluates a bounty issue using a single Claude Haiku call.
    Cheap and fast — runs once per issue before any expensive solver work.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def evaluate(
        self,
        repo_name: str,
        repo_description: str,
        tech_stack: str,
        issue_title: str,
        issue_body: str,
        bounty_amount: float,
        recent_commits: int,
    ) -> EvaluationResult:
        """
        Return an EvaluationResult for the given issue.
        Falls back to rejected() on any parse error.
        """
        user_message = _USER_TEMPLATE.format(
            repo_name=repo_name,
            repo_description=repo_description or "(no description)",
            tech_stack=tech_stack or "unknown",
            issue_title=issue_title,
            issue_body=(issue_body or "(empty)").strip(),
            bounty_amount=bounty_amount,
            recent_commits=recent_commits,
        )

        try:
            response = self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("Evaluator API call failed: %s", exc)
            return EvaluationResult.rejected(f"evaluation error: {exc}")

        raw = response.content[0].text.strip()

        # Strip accidental markdown fences if the model added them.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("Evaluator returned non-JSON: %s  raw=%r", exc, raw)
            return EvaluationResult.rejected("evaluator returned non-JSON response")

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_result(data: dict) -> EvaluationResult:
    """Parse the raw JSON dict into an EvaluationResult, with safe defaults."""
    try:
        issue_type = str(data.get("issue_type", "other"))
        if issue_type not in ("bug", "ui", "docs", "refactor", "test", "other"):
            issue_type = "other"

        success_prob = float(data.get("success_probability", 0.0))
        success_prob = max(0.0, min(1.0, success_prob))

        estimated_hours = float(data.get("estimated_hours", 0.0))
        estimated_hours = max(0.0, estimated_hours)

        # Recompute expected_value locally for consistency even if Claude got it wrong.
        bounty_eth = float(data.get("bounty_amount", 0.0))
        expected_value = float(
            data.get(
                "expected_value",
                bounty_eth * success_prob - estimated_hours * _COST_PER_HOUR_ETH,
            )
        )

        llm_should_attempt = bool(data.get("should_attempt", False))
        reason = str(data.get("reason", ""))

        # Hard constraints — override LLM verdict if any threshold is violated.
        should_attempt = llm_should_attempt
        if success_prob <= 0.8:
            should_attempt = False
            reason = f"success_probability {success_prob:.2f} ≤ 0.8"
        elif expected_value <= 0:
            should_attempt = False
            reason = f"expected_value {expected_value:.4f} ETH is not positive"
        elif estimated_hours >= 3:
            should_attempt = False
            reason = f"estimated_hours {estimated_hours:.1f} ≥ 3"

        return EvaluationResult(
            should_attempt=should_attempt,
            reason=reason,
            issue_type=issue_type,
            estimated_hours=estimated_hours,
            success_probability=success_prob,
            expected_value=expected_value,
        )
    except (TypeError, ValueError) as exc:
        log.warning("Failed to parse evaluation result: %s  data=%r", exc, data)
        return EvaluationResult.rejected(f"parse error: {exc}")


def format_tech_stack(languages: dict[str, int]) -> str:
    """
    Convert a {language: bytes} dict (from GitHub API) to a human-readable string.
    E.g. "Python (85%), JavaScript (12%), Shell (3%)"
    """
    if not languages:
        return "unknown"
    total = sum(languages.values())
    if total == 0:
        return "unknown"
    top = sorted(languages.items(), key=lambda x: -x[1])[:5]
    return ", ".join(f"{lang} ({round(100 * b / total)}%)" for lang, b in top)
