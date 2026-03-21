"""
Claude-powered GitHub issue solver.

Flow:
  1. Triage — decide if the issue is solvable autonomously (no design questions,
     no access to databases/secrets, no UI-only work, etc.)
  2. Explore — Claude reads files via tools until it has enough context.
  3. Generate — Claude calls submit_fix() with the exact file changes needed.

Returns a Solution (file changes + PR metadata) or None if the issue is
unsolvable or confidence is too low.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from github_client import GithubClient, IssueInfo

log = logging.getLogger(__name__)

MAX_EXPLORE_TURNS = 12   # hard stop on tool-use iterations
MIN_CONFIDENCE = "medium"  # reject "low" confidence fixes

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class FileChange:
    path: str
    content: str
    action: str  # "modify" | "create" | "delete"


@dataclass
class Solution:
    changes: list[FileChange]
    pr_title: str
    pr_body: str
    confidence: str  # "high" | "medium" | "low"
    branch_name: str = field(init=False)

    def __post_init__(self) -> None:
        slug = re.sub(r"[^a-z0-9]+", "-", self.pr_title.lower()).strip("-")[:50]
        self.branch_name = f"agent-fix/{slug}"

    @property
    def file_map(self) -> dict[str, str]:
        """Return {path: content} for all non-delete changes."""
        return {c.path: c.content for c in self.changes if c.action != "delete"}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "name": "list_directory",
        "description": (
            "List files and sub-directories at a path in the repository. "
            "Use '' for the repo root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to repo root, e.g. '' or 'src/lib'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the full content of a source file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root, e.g. 'src/utils.py'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "submit_fix",
        "description": (
            "Submit the completed fix. Call this ONLY when you have read all relevant "
            "files and are ready to provide the exact, complete file contents. "
            "Do not call it with partial or placeholder code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "description": "List of file changes that together fix the issue.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to repo root.",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["modify", "create", "delete"],
                            },
                            "content": {
                                "type": "string",
                                "description": (
                                    "Full file content after the change. "
                                    "Required for modify/create. Use '' for delete."
                                ),
                            },
                        },
                        "required": ["path", "action", "content"],
                    },
                },
                "pr_title": {
                    "type": "string",
                    "description": "Short PR title (≤ 72 chars), starting with a verb.",
                },
                "pr_body": {
                    "type": "string",
                    "description": (
                        "PR description: what changed and why. "
                        "Include 'Closes #<issue_number>'."
                    ),
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": (
                        "Your confidence that this fix is correct and complete. "
                        "Use 'low' only if you are guessing."
                    ),
                },
            },
            "required": ["changes", "pr_title", "pr_body", "confidence"],
        },
    },
]

# ---------------------------------------------------------------------------
# IssueSolver
# ---------------------------------------------------------------------------


class IssueSolver:
    def __init__(self, anthropic_api_key: str, github_client: GithubClient):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        self._gh = github_client

    def solve(self, issue: IssueInfo, languages: dict[str, int]) -> Optional[Solution]:
        """
        Attempt to solve the issue by reading repo files and generating a fix.
        Returns a Solution, or None if no fix could be generated.
        """
        lang_summary = ", ".join(
            f"{lang} ({bytes_:,} bytes)"
            for lang, bytes_ in sorted(languages.items(), key=lambda x: -x[1])[:5]
        )

        system_prompt = f"""\
You are an autonomous coding agent solving GitHub issues.
You have read-only access to the repository via tools, and will submit a fix
by calling submit_fix() with the complete, correct file contents.

Repository: {issue.repo_full_name}
Primary languages: {lang_summary or 'unknown'}

Rules:
1. Read the relevant files before making changes — do not guess at content.
2. Make the minimal change that fixes the issue. Do not refactor unrelated code.
3. Preserve existing code style (indentation, naming conventions).
4. Only call submit_fix() when you are confident the fix is complete and correct.
5. If you discover the issue cannot be fixed with code changes alone, call
   submit_fix() with confidence="low" and explain in pr_body.
"""

        messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    f"Please fix issue #{issue.number}: {issue.title}\n\n"
                    f"{issue.body}\n\n"
                    f"Issue URL: {issue.url}"
                ),
            }
        ]

        solution: Optional[Solution] = None

        for turn in range(MAX_EXPLORE_TURNS):
            response = self._client.messages.create(
                model="claude-opus-4-6",
                max_tokens=8192,
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                # Claude finished without calling submit_fix — no solution.
                log.info("Issue #%d: Claude ended without a fix.", issue.number)
                break

            if response.stop_reason != "tool_use":
                log.warning(
                    "Issue #%d: unexpected stop_reason=%s",
                    issue.number,
                    response.stop_reason,
                )
                break

            # Process tool calls.
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                result_content = self._execute_tool(
                    block.name, block.input, issue.repo_full_name
                )

                # submit_fix terminates the loop.
                if block.name == "submit_fix" and result_content.get("accepted"):
                    inp = block.input
                    changes = [
                        FileChange(
                            path=c["path"],
                            content=c["content"],
                            action=c["action"],
                        )
                        for c in inp["changes"]
                    ]
                    solution = Solution(
                        changes=changes,
                        pr_title=inp["pr_title"],
                        pr_body=inp["pr_body"],
                        confidence=inp["confidence"],
                    )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result_content),
                    }
                )

            if solution is not None:
                break

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        if solution is None:
            return None

        if solution.confidence == "low":
            log.info(
                "Issue #%d: solution confidence is 'low' — skipping.",
                issue.number,
            )
            return None

        return solution

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool(
        self, name: str, inputs: dict, repo_full_name: str
    ) -> dict:
        if name == "list_directory":
            path = inputs.get("path", "")
            entries = self._gh.list_directory(repo_full_name, path)
            return {"entries": entries, "count": len(entries)}

        elif name == "read_file":
            path = inputs["path"]
            content = self._gh.read_file(repo_full_name, path)
            if content is None:
                return {"error": f"File not found: {path}"}
            # Truncate very large files to avoid token overflow.
            if len(content) > 40_000:
                content = content[:40_000] + "\n\n[... truncated at 40 000 chars ...]"
            return {"path": path, "content": content}

        elif name == "submit_fix":
            # Validate non-empty changes list.
            changes = inputs.get("changes", [])
            if not changes:
                return {"accepted": False, "reason": "changes list is empty"}
            return {"accepted": True}

        else:
            return {"error": f"Unknown tool: {name}"}
