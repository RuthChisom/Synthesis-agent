"""
Test engineer — writes tests that reproduce the issue, validate the fix,
and cover edge cases.

Runs after the implementer produces updated files. Its output is merged into
the solution's changes so tests ship in the same PR as the fix.

INPUT:
  - summary      : brief issue description
  - updated_files: {path → content} of files the implementer changed
  - test_files   : {path → content} of existing test files in the repo

OUTPUT SCHEMA (strict):
{
  "test_files": [
    { "file_path": "...", "content": "..." }
  ]
}
"""

import json
import logging
import re
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)

# Patterns that identify test/spec files across common project layouts.
_TEST_PATH_RE = re.compile(
    r"(^|/)("
    r"test[_s]?/|tests/|spec/|__tests__/"          # test directories
    r"|test_[^/]+\.[a-z]+$"                         # test_foo.py
    r"|[^/]+_test\.[a-z]+$"                         # foo_test.py
    r"|[^/]+\.test\.[a-z]+$"                        # foo.test.ts
    r"|[^/]+\.spec\.[a-z]+$"                        # foo.spec.ts
    r")",
    re.IGNORECASE,
)

# Max characters per file in the prompt.
_MAX_FILE_CHARS = 8_000

_SYSTEM = """\
You are a senior test engineer writing tests for an autonomous coding agent.

Your tests must:
1. Reproduce the original bug (a test that would FAIL before the fix)
2. Validate that the fix is correct (the same test must PASS after the fix)
3. Cover every edge case listed in the plan

Rules:
- Use the project's existing test framework and conventions (inferred from test_files)
- Import only from the project's own modules and its declared dependencies
- Write minimal, focused tests — one assertion per case, clear names
- If adding to an existing test file, output the FULL updated file content
- If creating a new test file, follow the naming convention of existing test files
- Do NOT duplicate tests that already exist

Respond with ONLY valid JSON — no markdown, no commentary:
{
  "test_files": [
    { "file_path": "<exact path>", "content": "<FULL file content>" }
  ]
}

If no meaningful tests can be written (e.g. the fix is documentation-only),
return: { "test_files": [] }
"""

_USER_TEMPLATE = """\
## INPUT:
- Issue summary: {summary}

- Updated files:
{updated_files}

- Existing tests:
{test_files}

## TASK:
Write tests that reproduce the issue, validate the fix, and cover edge cases.
"""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TestFile:
    file_path: str
    content: str


@dataclass
class TestWriterResult:
    test_files: list[TestFile]

    @property
    def succeeded(self) -> bool:
        return bool(self.test_files)

    @classmethod
    def empty(cls) -> "TestWriterResult":
        return cls(test_files=[])


# ---------------------------------------------------------------------------
# TestEngineer
# ---------------------------------------------------------------------------


class TestEngineer:
    """
    Writes tests for the fix produced by SeniorEngineer.
    Single claude-sonnet-4-6 call — all context is provided up-front.
    """

    def __init__(self, anthropic_api_key: str):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)

    def write_tests(
        self,
        summary: str,
        updated_files: dict[str, str],
        test_files: dict[str, str],
    ) -> TestWriterResult:
        """
        Return a TestWriterResult.
        Falls back to TestWriterResult.empty() on any error — tests are
        additive and must never block the PR submission.
        """
        if not updated_files:
            log.info("TestEngineer skipped — no updated files provided.")
            return TestWriterResult.empty()

        user_message = _USER_TEMPLATE.format(
            summary=_truncate(summary, 600),
            updated_files=_format_files(updated_files),
            test_files=_format_files(test_files) if test_files else "(none found)",
        )

        try:
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            log.error("TestEngineer API call failed: %s", exc)
            return TestWriterResult.empty()

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
            log.warning("TestEngineer returned non-JSON: %s  raw=%r", exc, raw[:200])
            return TestWriterResult.empty()

        return _parse_result(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_test_paths(file_tree: str) -> list[str]:
    """
    Given a newline-separated file tree string, return paths that look like
    test files. Used by main.py to decide which files to read as test context.
    Capped at 20 files to keep the prompt size bounded.
    """
    paths = [p.strip() for p in file_tree.splitlines() if p.strip()]
    return [p for p in paths if _TEST_PATH_RE.search(p)][:20]


def is_test_file(path: str) -> bool:
    """Return True if the given file path looks like a test file."""
    return bool(_TEST_PATH_RE.search(path))


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


def _parse_result(data: dict) -> TestWriterResult:
    try:
        test_files = [
            TestFile(
                file_path=str(f["file_path"]),
                content=str(f["content"]),
            )
            for f in data.get("test_files", [])
            if f.get("file_path") and f.get("content") is not None
        ]
        return TestWriterResult(test_files=test_files)
    except (TypeError, KeyError, ValueError) as exc:
        log.warning("Failed to parse test result: %s  data=%r", exc, str(data)[:200])
        return TestWriterResult.empty()
