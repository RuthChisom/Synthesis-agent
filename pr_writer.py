"""
PR writer — generates a professional, trust-building pull request description.

Runs after review approval and before fork/push. Its output replaces the
auto-generated pr_title/pr_body on the Solution so maintainers see a clean,
human-quality PR rather than a mechanical one-liner.

INPUT:
  - issue_title   : GitHub issue title
  - issue_body    : GitHub issue description
  - changes_summary: short prose description of what was changed and why

OUTPUT (MARKDOWN):
  ### Title:
  Fix: <short clear title>

  ### Description:
  ...

  ### Changes:
  - ...

  ### Testing:
  - ...

  ### Checklist:
  - [x] Issue addressed
  - [x] Tests added/updated
  - [x] No breaking changes

  ### Notes:
  ...
"""

import logging
import re
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)

_SYSTEM = """\
You are an expert open-source developer writing a pull request for a contribution
to an external repository. Your goal is to maximize the chance that a maintainer
reads, understands, and merges the PR.

Write a professional PR that:
- Clearly explains the root cause and the fix in plain language
- References the issue number (use "Closes #<N>" format)
- Shows that you understood the codebase and the maintainer's intent
- Demonstrates caution: no breaking changes, tests added, minimal scope
- Builds trust — do not oversell; be precise and honest

Respond with the PR description in this EXACT markdown format.
Do NOT add any text before "### Title:" or after the Notes section.

### Title:
Fix: <short clear title (≤ 60 chars, starts with a verb)>

### Description:
<2–4 sentences: what the issue was, why it happened, how it is fixed>

### Changes:
- <bullet per logical change — one line each, specific>

### Testing:
- <bullet per test added or scenario verified>

### Checklist:
- [x] Issue addressed
- [x] Tests added/updated
- [x] No breaking changes

### Notes:
<optional: anything the maintainer should know — if nothing, write "None">
"""

_USER_TEMPLATE = """\
## INPUT:
- Issue title: {issue_title}
- Issue description: {issue_body}
- Changes summary: {changes_summary}

## TASK:
Write a professional PR that clearly explains the fix, references the issue,
shows understanding of the codebase, and builds trust with maintainers.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PRDraft:
    title: str    # plain text, ≤ 72 chars
    body: str     # full markdown body (everything after ### Title:)

    @classmethod
    def fallback(cls, issue_title: str, changes_summary: str, issue_number: int) -> "PRDraft":
        """Minimal safe PR if the writer fails."""
        title = f"Fix: {issue_title}"[:72]
        body = (
            f"{changes_summary}\n\n"
            f"Closes #{issue_number}"
        )
        return cls(title=title, body=body)


# ---------------------------------------------------------------------------
# PRWriter
# ---------------------------------------------------------------------------


class PRWriter:
    """
    Generates a polished PR title + body for the approved solution.
    Single claude-sonnet-4-6 call — purely text generation, no tools needed.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def write(
        self,
        issue_title: str,
        issue_body: str,
        changes_summary: str,
        issue_number: int,
    ) -> PRDraft:
        """
        Return a PRDraft. Falls back to PRDraft.fallback() on any error so
        the PR can still be submitted with a basic description.
        """
        user_message = _USER_TEMPLATE.format(
            issue_title=issue_title,
            issue_body=_truncate(issue_body, 800),
            changes_summary=_truncate(changes_summary, 400),
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("PRWriter API call failed: %s", exc)
            return PRDraft.fallback(issue_title, changes_summary, issue_number)

        raw = response.content[0].text.strip()
        return _parse_draft(raw, issue_title, changes_summary, issue_number)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + " …"


def _parse_draft(
    raw: str,
    issue_title: str,
    changes_summary: str,
    issue_number: int,
) -> PRDraft:
    """
    Extract the title line and body from the markdown template output.

    Expected shape:
        ### Title:
        Fix: <title text>

        ### Description:
        ...
    """
    title = ""
    body_start = 0

    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "### Title:":
            # Title is on the very next non-empty line.
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if candidate:
                    # Strip a leading "Fix: " prefix that the model may echo back
                    # so we can re-add it cleanly, or just use as-is.
                    title = candidate[:72]
                    body_start = j + 1
                    break
            break

    body_lines = lines[body_start:]
    # Drop leading blank lines before "### Description:".
    while body_lines and not body_lines[0].strip():
        body_lines = body_lines[1:]

    body = "\n".join(body_lines).strip()

    if not title or not body:
        log.warning("PRWriter output missing title or body — using fallback.")
        return PRDraft.fallback(issue_title, changes_summary, issue_number)

    # Ensure the issue reference is present in the body.
    closes_ref = f"Closes #{issue_number}"
    if closes_ref not in body:
        body = body + f"\n\n{closes_ref}"

    return PRDraft(title=title, body=body)
