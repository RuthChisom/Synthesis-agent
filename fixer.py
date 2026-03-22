"""
Review fixer — takes the reviewer's issues and repairs the implementation.

Runs once after a review rejection. If the re-review still fails, the issue is
abandoned (no PR opened). This prevents an infinite fix loop while still giving
the agent a single chance to self-correct.

INPUT:
  - updated_files: {path → content} of all current implementation + test files
  - issues       : list of specific problems reported by PRReviewer

OUTPUT SCHEMA (strict):
{
  "updated_files": [
    { "file_path": "...", "content": "..." }
  ]
}
"""

import json
import logging
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)

_MAX_FILE_CHARS = 10_000

_SYSTEM = """\
You are a senior software engineer fixing a failed code review.

You will receive the current implementation and a precise list of issues found
by a strict reviewer. Your job is to fix ALL listed issues in a single pass.

Rules:
- Fix every issue in the list — do not skip any
- Preserve ALL correct logic that was not flagged
- Keep changes minimal — only touch what is broken
- Output the COMPLETE file content for every file you change
- Only include files you actually changed — omit unchanged files
- Follow the existing code style and conventions

Respond with ONLY valid JSON — no markdown, no commentary:
{
  "updated_files": [
    { "file_path": "<exact path>", "content": "<FULL file content after fixes>" }
  ]
}
"""

_USER_TEMPLATE = """\
## INPUT:
- Previous code:
{updated_files}

- Review issues:
{issues}

## TASK:
Fix ALL issues while preserving correct logic.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FixerResult:
    updated_files: dict[str, str]   # {path → fixed content}

    @property
    def succeeded(self) -> bool:
        return bool(self.updated_files)

    @classmethod
    def empty(cls) -> "FixerResult":
        return cls(updated_files={})


# ---------------------------------------------------------------------------
# ReviewFixer
# ---------------------------------------------------------------------------


class ReviewFixer:
    """
    Fixes review issues in a single claude-sonnet-4-6 call.
    All context is provided up-front — no tool-use needed.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def fix(
        self,
        updated_files: dict[str, str],
        issues: list[str],
    ) -> FixerResult:
        """
        Return a FixerResult containing only the files that were changed.
        Falls back to FixerResult.empty() on any error so the caller can
        treat the fix as unsuccessful and abandon the PR.
        """
        if not issues:
            log.info("ReviewFixer called with no issues — nothing to fix.")
            return FixerResult.empty()

        if not updated_files:
            log.warning("ReviewFixer called with no files — cannot fix.")
            return FixerResult.empty()

        user_message = _USER_TEMPLATE.format(
            updated_files=_format_files(updated_files),
            issues=_format_issues(issues),
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("ReviewFixer API call failed: %s", exc)
            return FixerResult.empty()

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
            log.warning("ReviewFixer returned non-JSON: %s  raw=%r", exc, raw[:200])
            return FixerResult.empty()

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_files(files: dict[str, str]) -> str:
    if not files:
        return "(none)"
    parts: list[str] = []
    for path, content in files.items():
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + f"\n... (truncated at {_MAX_FILE_CHARS} chars)"
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _format_issues(issues: list[str]) -> str:
    return "\n".join(f"- {issue}" for issue in issues)


def _parse_result(data: dict) -> FixerResult:
    try:
        files = {
            str(f["file_path"]): str(f["content"])
            for f in data.get("updated_files", [])
            if f.get("file_path") and f.get("content") is not None
        }
        return FixerResult(updated_files=files)
    except (TypeError, KeyError, ValueError) as exc:
        log.warning("Failed to parse fixer result: %s  data=%r", exc, str(data)[:200])
        return FixerResult.empty()
