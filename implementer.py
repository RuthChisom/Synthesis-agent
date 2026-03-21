"""
Senior engineer implementer — takes the architect's plan and actual file contents,
then produces fully updated files with the fix implemented.

This runs after the planner and before the solver. If it produces valid output,
the solver's multi-turn tool-use exploration is skipped entirely.

INPUT:
  - plan         : IssuePlan from the planner
  - files_content: dict[path → current file content] for every file in the plan

OUTPUT SCHEMA (strict):
{
  "updated_files": [
    { "file_path": "...", "content": "FULL FILE CONTENT" }
  ],
  "summary_of_changes": "..."
}
"""

import json
import logging
from dataclasses import dataclass

import anthropic

from planner import IssuePlan

log = logging.getLogger(__name__)

# Max characters of each file sent in the prompt.
# Keeps the context manageable for large files.
_MAX_FILE_CHARS = 12_000

_SYSTEM = """\
You are a senior software engineer implementing a bug fix or feature for an
autonomous coding agent.

You will receive:
1. An implementation plan from the architect (exact steps, files, edge cases)
2. The current content of every file you need to change

Your job:
- Implement the plan precisely and completely
- Modify ONLY the necessary parts — preserve all other behaviour
- Follow the project's existing conventions (naming, style, indentation)
- Keep code minimal and clean — no added comments, no dead code, no extra logging
- Handle every edge case listed in the plan
- Include required tests in the appropriate test file

Respond with ONLY valid JSON — no markdown, no commentary, no extra fields:
{
  "updated_files": [
    { "file_path": "<exact path>", "content": "<FULL file content after changes>" }
  ],
  "summary_of_changes": "<one concise sentence describing what was changed and why>"
}

Rules:
- "content" must be the COMPLETE file — never a partial snippet or a diff
- Only include files you actually changed — omit unchanged files
- file_path must exactly match the path provided in the input
- If a plan step requires a new file, include it with its full initial content
"""

_USER_TEMPLATE = """\
## INPUT:
- Plan:
{plan}

- Files content:
{files_content}

## TASK:
Implement the fix fully. Modify ONLY necessary parts. Preserve existing behavior.
Follow project conventions. Keep code minimal and clean.
"""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class UpdatedFile:
    file_path: str
    content: str


@dataclass
class ImplementationResult:
    updated_files: list[UpdatedFile]
    summary_of_changes: str

    @property
    def succeeded(self) -> bool:
        return bool(self.updated_files)

    @classmethod
    def empty(cls) -> "ImplementationResult":
        return cls(updated_files=[], summary_of_changes="")


# ---------------------------------------------------------------------------
# Implementer
# ---------------------------------------------------------------------------


class SeniorEngineer:
    """
    Produces updated file contents from a plan and raw file inputs.
    Uses claude-sonnet-4-6 — all context is provided up-front so no
    tool-use exploration is needed.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def implement(
        self,
        plan: IssuePlan,
        files_content: dict[str, str],
    ) -> ImplementationResult:
        """
        Return an ImplementationResult.
        Falls back to ImplementationResult.empty() on any error so the
        solver can take over.
        """
        if not plan.steps:
            log.info("Implementer skipped — plan is empty.")
            return ImplementationResult.empty()

        plan_text = plan.to_solver_context()
        files_text = _format_files(files_content)

        user_message = _USER_TEMPLATE.format(
            plan=plan_text,
            files_content=files_text,
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("Implementer API call failed: %s", exc)
            return ImplementationResult.empty()

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
            log.warning("Implementer returned non-JSON: %s  raw=%r", exc, raw[:200])
            return ImplementationResult.empty()

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_files(files_content: dict[str, str]) -> str:
    """
    Format each file as a fenced code block for readability in the prompt.
    Truncates files longer than _MAX_FILE_CHARS with a visible marker.
    """
    if not files_content:
        return "(no files provided)"

    parts: list[str] = []
    for path, content in files_content.items():
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + f"\n... (truncated at {_MAX_FILE_CHARS} chars)"
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _parse_result(data: dict) -> ImplementationResult:
    """Parse the raw JSON dict into an ImplementationResult."""
    try:
        updated_files = [
            UpdatedFile(
                file_path=str(f["file_path"]),
                content=str(f["content"]),
            )
            for f in data.get("updated_files", [])
            if f.get("file_path") and f.get("content") is not None
        ]
        summary = str(data.get("summary_of_changes", ""))
        return ImplementationResult(updated_files=updated_files, summary_of_changes=summary)
    except (TypeError, KeyError, ValueError) as exc:
        log.warning("Failed to parse implementation result: %s  data=%r", exc, str(data)[:200])
        return ImplementationResult.empty()
