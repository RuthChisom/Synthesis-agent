"""
GitHub API wrapper for the issue-solving agent.

Responsibilities:
  - Fetch open issues from target repos
  - Fork repos into the agent's namespace
  - Push multi-file code changes via the Git Data API (no local clone needed)
  - Open pull requests
  - Poll PR merge status
"""

import base64
import logging
import time
from dataclasses import dataclass
from typing import Optional

from github import Github, GithubException
from github.InputGitTreeElement import InputGitTreeElement

log = logging.getLogger(__name__)


@dataclass
class IssueInfo:
    number: int
    title: str
    body: str
    url: str
    repo_full_name: str
    comment_bodies: list[str]
    labels: list[str]


@dataclass
class PRInfo:
    url: str
    number: int
    state: str   # "open" | "closed" | "merged"
    merged: bool


class GithubClient:
    def __init__(self, token: str, agent_username: str):
        self._gh = Github(token)
        self._agent_username = agent_username

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def get_open_issues(self, repo_full_name: str) -> list[IssueInfo]:
        """Return all open issues for *repo_full_name* (excluding PRs)."""
        repo = self._gh.get_repo(repo_full_name)
        issues = []
        for issue in repo.get_issues(state="open"):
            if issue.pull_request:
                continue  # skip PRs listed as issues
            comment_bodies = [c.body for c in issue.get_comments()]
            issues.append(
                IssueInfo(
                    number=issue.number,
                    title=issue.title,
                    body=issue.body or "",
                    url=issue.html_url,
                    repo_full_name=repo_full_name,
                    comment_bodies=comment_bodies,
                    labels=[lbl.name for lbl in issue.labels],
                )
            )
        return issues

    # ------------------------------------------------------------------
    # Repository content (for the solver)
    # ------------------------------------------------------------------

    def list_directory(self, repo_full_name: str, path: str) -> list[str]:
        """Return file/dir names inside *path* in the default branch."""
        repo = self._gh.get_repo(repo_full_name)
        try:
            contents = repo.get_contents(path)
            if isinstance(contents, list):
                return [c.path for c in contents]
            return [contents.path]
        except GithubException as exc:
            log.warning("list_directory %s/%s: %s", repo_full_name, path, exc)
            return []

    def read_file(self, repo_full_name: str, path: str) -> Optional[str]:
        """Return the decoded text content of a file, or None on error."""
        repo = self._gh.get_repo(repo_full_name)
        try:
            content = repo.get_contents(path)
            if isinstance(content, list):
                return None  # path is a directory
            raw = content.decoded_content
            return raw.decode("utf-8", errors="replace")
        except GithubException as exc:
            log.warning("read_file %s/%s: %s", repo_full_name, path, exc)
            return None

    def get_languages(self, repo_full_name: str) -> dict[str, int]:
        """Return {language: bytes} for the repo."""
        repo = self._gh.get_repo(repo_full_name)
        return dict(repo.get_languages())

    # ------------------------------------------------------------------
    # Fork + branch + commit + PR
    # ------------------------------------------------------------------

    def ensure_fork(self, repo_full_name: str) -> str:
        """
        Fork *repo_full_name* into the agent's namespace.
        Returns the fork's full name (agent_username/repo_name).
        Idempotent — returns existing fork if already present.
        """
        repo = self._gh.get_repo(repo_full_name)
        fork_name = f"{self._agent_username}/{repo.name}"
        try:
            fork = self._gh.get_repo(fork_name)
            log.info("Fork already exists: %s", fork_name)
            return fork.full_name
        except GithubException:
            pass
        fork = repo.create_fork()
        # GitHub forks are created asynchronously; wait for it to be ready.
        for _ in range(10):
            try:
                self._gh.get_repo(fork.full_name)
                break
            except GithubException:
                time.sleep(3)
        log.info("Created fork: %s", fork.full_name)
        return fork.full_name

    def push_changes_and_open_pr(
        self,
        base_repo_full_name: str,
        fork_full_name: str,
        branch_name: str,
        file_changes: dict[str, str],
        pr_title: str,
        pr_body: str,
        issue_number: int,
    ) -> PRInfo:
        """
        Push *file_changes* ({path: content}) to a new branch on the fork,
        then open a PR against *base_repo_full_name*.

        Uses the GitHub Git Data API — no local clone required.
        Returns PRInfo for the created pull request.
        """
        fork = self._gh.get_repo(fork_full_name)
        base_repo = self._gh.get_repo(base_repo_full_name)

        # Determine the default branch of the base repo.
        default_branch = base_repo.default_branch

        # Get current HEAD of default branch in the fork.
        ref = fork.get_git_ref(f"heads/{default_branch}")
        base_commit_sha = ref.object.sha
        base_commit = fork.get_git_commit(base_commit_sha)
        base_tree_sha = base_commit.tree.sha

        # Create blobs for each changed file.
        elements = []
        for path, content in file_changes.items():
            blob = fork.create_git_blob(
                base64.b64encode(content.encode()).decode(),
                "base64",
            )
            elements.append(
                InputGitTreeElement(
                    path=path,
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )
            )

        # Create new tree on top of the base tree.
        new_tree = fork.create_git_tree(
            elements,
            base_tree=fork.get_git_tree(base_tree_sha),
        )

        # Create commit.
        commit_message = f"{pr_title}\n\nCloses #{issue_number}"
        new_commit = fork.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[base_commit],
        )

        # Create the feature branch pointing at the new commit.
        fork.create_git_ref(f"refs/heads/{branch_name}", new_commit.sha)
        log.info("Pushed branch %s to %s", branch_name, fork_full_name)

        # Open PR from fork branch into base repo default branch.
        pr = base_repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=f"{self._agent_username}:{branch_name}",
            base=default_branch,
        )
        log.info("Opened PR #%d: %s", pr.number, pr.html_url)
        return PRInfo(
            url=pr.html_url,
            number=pr.number,
            state=pr.state,
            merged=pr.merged,
        )

    # ------------------------------------------------------------------
    # PR monitoring
    # ------------------------------------------------------------------

    def get_pr_info(self, repo_full_name: str, pr_number: int) -> PRInfo:
        """Fetch the current state of a pull request."""
        repo = self._gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        return PRInfo(
            url=pr.html_url,
            number=pr.number,
            state=pr.state,
            merged=pr.merged,
        )
