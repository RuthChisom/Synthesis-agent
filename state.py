"""
Persistent job state for the issue-solving agent.

Stored in state.json as:
{
  "<issue_url>": {
    "status": "submitted" | "merged" | "skipped" | "failed",
    "repo": "owner/repo",
    "issue_number": 42,
    "pr_url": "...",
    "pr_number": 7,
    "bounty_eth": 0.05,
    "branch": "agent-fix/...",
    "submitted_at": "2026-03-21T00:00:00Z",
    "merged_at": "2026-03-21T01:00:00Z"   # null until merged
  }
}

All writes are atomic (write-to-temp + rename) to prevent corruption on crash.
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional

STATE_FILE = Path(__file__).parent / "state.json"


# ---------------------------------------------------------------------------
# Job record
# ---------------------------------------------------------------------------


@dataclass
class Job:
    issue_url: str
    status: str          # submitted | merged | skipped | failed
    repo: str
    issue_number: int
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    bounty_eth: Optional[float] = None
    branch: Optional[str] = None
    submitted_at: Optional[str] = None
    merged_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# State store
# ---------------------------------------------------------------------------


class State:
    def __init__(self, path: Path = STATE_FILE):
        self._path = path
        self._jobs: dict[str, Job] = {}
        self._load()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def is_known(self, issue_url: str) -> bool:
        return issue_url in self._jobs

    def get(self, issue_url: str) -> Optional[Job]:
        return self._jobs.get(issue_url)

    def all_submitted(self) -> Iterator[Job]:
        for job in self._jobs.values():
            if job.status == "submitted":
                yield job

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for job in self._jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def mark_skipped(self, issue_url: str, repo: str, issue_number: int) -> None:
        self._jobs[issue_url] = Job(
            issue_url=issue_url,
            status="skipped",
            repo=repo,
            issue_number=issue_number,
        )
        self._save()

    def mark_failed(self, issue_url: str, repo: str, issue_number: int) -> None:
        self._jobs[issue_url] = Job(
            issue_url=issue_url,
            status="failed",
            repo=repo,
            issue_number=issue_number,
        )
        self._save()

    def mark_submitted(
        self,
        issue_url: str,
        repo: str,
        issue_number: int,
        pr_url: str,
        pr_number: int,
        bounty_eth: float,
        branch: str,
    ) -> None:
        self._jobs[issue_url] = Job(
            issue_url=issue_url,
            status="submitted",
            repo=repo,
            issue_number=issue_number,
            pr_url=pr_url,
            pr_number=pr_number,
            bounty_eth=bounty_eth,
            branch=branch,
            submitted_at=_now(),
        )
        self._save()

    def mark_merged(self, issue_url: str) -> None:
        job = self._jobs.get(issue_url)
        if job:
            job.status = "merged"
            job.merged_at = _now()
            self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        with open(self._path) as f:
            raw = json.load(f)
        for url, d in raw.items():
            try:
                self._jobs[url] = Job.from_dict(d)
            except (TypeError, KeyError):
                pass  # ignore corrupt entries

    def _save(self) -> None:
        data = {url: job.to_dict() for url, job in self._jobs.items()}
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, self._path)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
