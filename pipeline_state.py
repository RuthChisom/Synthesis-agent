"""
Pipeline state tracker and final result for a single issue-solving run.

PipelineState records which steps completed successfully. Call
verify_complete() at the end of the pipeline to catch any skipped stages.

FinalResult is the structured output returned by process_issue() and
logged / stored for every issue the agent attempts.
"""

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PipelineIncompleteError(Exception):
    """Raised when verify_complete() finds required steps that did not run."""


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------

@dataclass
class PipelineState:
    """
    Boolean flags updated as each pipeline stage completes.

    discovered — evaluator returned a valid verdict
    planned    — planner produced a plan (even if empty; presence = ran)
    built      — implementer or solver produced a solution
    tested     — test engineer wrote at least one test file
    reviewed   — reviewer ran at least once
    approved   — reviewer approved (may be after fix cycles)
    pr_created — pull request was opened on GitHub
    """
    discovered: bool = False
    planned:    bool = False
    built:      bool = False
    tested:     bool = False
    reviewed:   bool = False
    approved:   bool = False
    pr_created: bool = False

    # Steps that must be True for the pipeline to be considered complete.
    _REQUIRED: tuple = ("discovered", "planned", "built", "reviewed", "approved", "pr_created")

    def verify_complete(self) -> None:
        """Raise PipelineIncompleteError if any required step is False."""
        missing = [step for step in self._REQUIRED if not getattr(self, step)]
        if missing:
            raise PipelineIncompleteError(
                f"Pipeline incomplete — steps not finished: {missing}"
            )

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "planned":    self.planned,
            "built":      self.built,
            "tested":     self.tested,
            "reviewed":   self.reviewed,
            "approved":   self.approved,
            "pr_created": self.pr_created,
        }


# ---------------------------------------------------------------------------
# FinalResult
# ---------------------------------------------------------------------------

@dataclass
class FinalResult:
    """
    Structured output returned by process_issue() for every attempt.

    issue      — issue title (human-readable label)
    status     — "SUCCESS" if a PR was opened and the audit passed; "FAILED" otherwise
    pr_url     — GitHub PR URL if one was created, else None
    confidence — auditor confidence score (0–1); evaluator probability if audit not reached
    errors     — list of error/problem messages accumulated during the run
    pipeline   — snapshot of PipelineState flags at the time of return
    """
    issue: str
    status: Literal["SUCCESS", "FAILED"]
    pr_url: str | None
    confidence: float
    errors: list[str] = field(default_factory=list)
    pipeline: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "issue":      self.issue,
            "status":     self.status,
            "pr_url":     self.pr_url,
            "confidence": self.confidence,
            "errors":     self.errors,
            "pipeline":   self.pipeline,
        }
