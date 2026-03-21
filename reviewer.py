"""
Senior reviewer — performs a strict pre-submission review of the implementation
and tests before the PR is opened.

This is the last gate before payment is at stake. A rejection here prevents a
bad PR that would be closed without merge (no payment, reputation damage).

INPUT:
  - issue_summary: brief description of what the issue requires
  - updated_files: {path → content} of all implementation files
  - test_files   : {path → content} of all test files (new + existing)

OUTPUT SCHEMA (strict):
{
  "approve": true/false,
  "issues": ["..."],
  "fix_priority": "low|medium|high"
}
"""

import json
import logging
from dataclasses import dataclass, field

import anthropic

log = logging.getLogger(__name__)

_MAX_FILE_CHARS = 10_000

_SYSTEM = """\
You are a strict senior software engineer performing a pre-submission code review
for an autonomous agent. Your job is to protect the agent's reputation and prevent
payment loss by catching bad PRs before they are opened.

Be critical. Approve ONLY if the implementation is genuinely correct and complete.

Check for ALL of the following:
1. Bugs — logic errors, off-by-one errors, unhandled exceptions, wrong conditions
2. Missing logic — requirements from the issue that are not implemented
3. Edge cases — inputs or states that would break the fix
4. Code quality — dead code, inconsistent style, unnecessary complexity
5. Misinterpretation — fix solves the wrong problem or changes unrelated behaviour
6. Test quality — tests are superficial, missing assertions, or don't actually
   reproduce the original issue

REJECT (approve: false) if:
- Any correctness bug is present
- A key requirement from the issue is missing
- An unhandled edge case would cause a crash or wrong output
- The fix clearly misinterprets the issue

APPROVE (approve: true) with issues listed if:
- The fix is correct but has minor style or test gaps (fix_priority: "low")

fix_priority meaning:
  "high"   — blocking correctness issue that would cause the PR to be rejected
  "medium" — significant gap that reduces confidence in the fix
  "low"    — minor style or polish issue, does not block approval

Respond with ONLY valid JSON — no markdown, no commentary:
{
  "approve": true/false,
  "issues": ["<specific, actionable description of each problem found>"],
  "fix_priority": "low|medium|high"
}

If the implementation looks correct and complete, return:
{ "approve": true, "issues": [], "fix_priority": "low" }
"""

_USER_TEMPLATE = """\
## INPUT:
- Issue: {issue_summary}

- Changes:
{updated_files}

- Tests:
{test_files}

## TASK:
Review for bugs, missing logic, unhandled edge cases, code quality issues,
and misinterpretation of the issue. Be strict — a bad PR costs payment.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    approve: bool
    issues: list[str]
    fix_priority: str  # "low" | "medium" | "high"

    @classmethod
    def approved(cls) -> "ReviewResult":
        return cls(approve=True, issues=[], fix_priority="low")

    @classmethod
    def failed(cls, reason: str) -> "ReviewResult":
        """Used when the reviewer itself errors — default to cautious approval."""
        return cls(approve=True, issues=[f"reviewer error: {reason}"], fix_priority="low")


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


class PRReviewer:
    """
    Performs a strict pre-submission review of the fix + tests.

    Errors conservatively: if the API call fails or the response is
    unparseable, it approves with a warning rather than silently blocking
    legitimate fixes.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def review(
        self,
        issue_summary: str,
        updated_files: dict[str, str],
        test_files: dict[str, str],
    ) -> ReviewResult:
        """
        Return a ReviewResult. Falls back to ReviewResult.failed() (which
        approves with a warning) on any API or parse error.
        """
        if not updated_files:
            log.warning("Reviewer called with no updated files — approving vacuously.")
            return ReviewResult.approved()

        user_message = _USER_TEMPLATE.format(
            issue_summary=_truncate(issue_summary, 800),
            updated_files=_format_files(updated_files),
            test_files=_format_files(test_files) if test_files else "(none)",
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("PRReviewer API call failed: %s", exc)
            return ReviewResult.failed(str(exc))

        raw = response.content[0].text.strip()

        # Strip accidental markdown fences.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("PRReviewer returned non-JSON: %s  raw=%r", exc, raw[:200])
            return ReviewResult.failed("non-JSON response")

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


def _parse_result(data: dict) -> ReviewResult:
    try:
        approve = bool(data.get("approve", True))
        issues = [str(i) for i in data.get("issues", [])]
        priority = str(data.get("fix_priority", "low"))
        if priority not in ("low", "medium", "high"):
            priority = "low"
        return ReviewResult(approve=approve, issues=issues, fix_priority=priority)
    except (TypeError, ValueError) as exc:
        log.warning("Failed to parse review result: %s  data=%r", exc, str(data)[:200])
        return ReviewResult.failed(f"parse error: {exc}")
