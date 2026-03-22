"""
Senior AI system auditor — evaluates whether the agent successfully completed
its task end-to-end, after the PR has been submitted.

INPUT:
  - issue        : issue title + body
  - plan         : architect-level plan (steps, files, edge cases)
  - updated_files: {path → content} of all implementation files
  - test_files   : {path → content} of test files
  - review       : pre-submission review result (approve, issues, priority)
  - pr_status    : current PR state ("submitted" | "merged" | "closed" | "open")

OUTPUT SCHEMA (strict):
{
  "success": true/false,
  "confidence": 0.0-1.0,
  "problems": ["..."],
  "improvements": ["..."]
}
"""

import json
import logging
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger(__name__)

_MAX_FILE_CHARS = 8_000

_SYSTEM = """\
You are a senior AI system auditor.
Your job is to evaluate whether this autonomous bounty agent has successfully completed its task.

Assess the following five dimensions:
1. Issue understanding — was the issue correctly interpreted?
2. Code correctness    — does the implementation actually fix the problem?
3. Test quality        — are the tests meaningful and do they cover the issue?
4. Review handling     — were review concerns addressed properly?
5. PR acceptance odds  — is the PR likely to be accepted by the maintainer?

Scoring guidance:
- success: true only if the fix is correct, tests are present, and the PR is likely to be merged.
- confidence: your certainty in the success verdict (0.0 = total uncertainty, 1.0 = certain).
- problems: specific, actionable descriptions of what went wrong (empty list if none).
- improvements: concrete suggestions for doing better next time (empty list if none).

Respond with ONLY valid JSON — no markdown, no commentary:
{
  "success": true/false,
  "confidence": <0.0-1.0>,
  "problems": ["<specific problem>"],
  "improvements": ["<concrete suggestion>"]
}
"""

_USER_TEMPLATE = """\
## INPUT:
- Issue: {issue}
- Plan: {plan}
- Code changes: {updated_files}
- Tests: {test_files}
- Review result: {review}
- PR status: {pr_status}

## TASK:
Evaluate:
1. Was the issue correctly understood?
2. Does the code fix the problem?
3. Are tests meaningful?
4. Was the review properly handled?
5. Is the PR likely to be accepted?
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AuditResult:
    success: bool
    confidence: float
    problems: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "confidence": self.confidence,
            "problems": self.problems,
            "improvements": self.improvements,
        }

    @classmethod
    def error(cls, reason: str) -> "AuditResult":
        return cls(success=False, confidence=0.0, problems=[f"auditor error: {reason}"])


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class SystemAuditor:
    """
    Post-submission audit of the full agent pipeline.

    Errors conservatively: on any API or parse failure it returns
    AuditResult.error() so the failure is logged without blocking the main loop.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def audit(
        self,
        issue: str,
        plan: str,
        updated_files: dict[str, str],
        test_files: dict[str, str],
        review: str,
        pr_status: str,
    ) -> AuditResult:
        user_message = _USER_TEMPLATE.format(
            issue=_truncate(issue, 600),
            plan=_truncate(plan, 600),
            updated_files=_format_files(updated_files),
            test_files=_format_files(test_files) if test_files else "(none)",
            review=_truncate(review, 400),
            pr_status=pr_status or "unknown",
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("SystemAuditor API call failed: %s", exc)
            return AuditResult.error(str(exc))

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("SystemAuditor returned non-JSON: %s  raw=%r", exc, raw[:200])
            return AuditResult.error("non-JSON response")

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + " …"


def _format_files(files: dict[str, str]) -> str:
    if not files:
        return "(none)"
    parts: list[str] = []
    for path, content in files.items():
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + f"\n... (truncated at {_MAX_FILE_CHARS} chars)"
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _parse_result(data: dict) -> AuditResult:
    try:
        success = bool(data.get("success", False))
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        problems = [str(p) for p in data.get("problems", [])]
        improvements = [str(i) for i in data.get("improvements", [])]
        return AuditResult(
            success=success,
            confidence=confidence,
            problems=problems,
            improvements=improvements,
        )
    except (TypeError, ValueError) as exc:
        log.warning("Failed to parse audit result: %s  data=%r", exc, str(data)[:200])
        return AuditResult.error(f"parse error: {exc}")
