"""
GitHub Cloner for Code Graph RAG

Clone repositories with retry logic and state persistence for resumability.

Usage:
    from batch.github_cloner import GitHubCloner, CloneResult

    cloner = GitHubCloner(clone_dir=Path("./clones"))
    result = cloner.clone_repo("https://github.com/facebook/react")
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass
class CloneResult:
    """Result of a clone operation."""
    github_url: str
    local_path: Path | None
    success: bool
    error: str | None
    duration_seconds: float
    retry_count: int

    def to_dict(self) -> dict:
        return {
            "github_url": self.github_url,
            "local_path": str(self.local_path) if self.local_path else None,
            "success": self.success,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "retry_count": self.retry_count,
        }


@dataclass
class CloneState:
    """Persisted state for resume support."""
    version: str = "1.0"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed: list[str] = field(default_factory=list)  # URLs successfully cloned
    failed: dict[str, int] = field(default_factory=dict)  # URL -> retry count
    in_progress: str | None = None  # Currently cloning

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "started_at": self.started_at,
            "last_updated": self.last_updated,
            "completed": self.completed,
            "failed": self.failed,
            "in_progress": self.in_progress,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CloneState:
        return cls(
            version=data.get("version", "1.0"),
            started_at=data.get("started_at", datetime.now(timezone.utc).isoformat()),
            last_updated=data.get("last_updated", datetime.now(timezone.utc).isoformat()),
            completed=data.get("completed", []),
            failed=data.get("failed", {}),
            in_progress=data.get("in_progress"),
        )


# Callback type for progress updates
CloneCallback = Callable[[CloneResult], None] | None


class GitHubCloner:
    """
    Clone GitHub repositories with retry logic and state persistence.

    Features:
    - Shallow clones (--depth 1) for speed
    - Exponential backoff on failures
    - State persistence for resume
    - Rate limit detection and handling
    """

    def __init__(
        self,
        clone_dir: Path,
        max_retries: int = 3,
        base_retry_delay: float = 5.0,
        shallow: bool = True,
        state_file: Path | None = None,
    ):
        self.clone_dir = clone_dir
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.shallow = shallow
        self.state_file = state_file or (clone_dir / ".clone_state.json")
        self.state = CloneState()

        # Ensure clone directory exists
        self.clone_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> None:
        """Load state from disk if it exists."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    self.state = CloneState.from_dict(json.load(f))
                print(f"Loaded state: {len(self.state.completed)} completed, {len(self.state.failed)} failed")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Could not load state file: {e}")
                self.state = CloneState()

    def save_state(self) -> None:
        """Save state to disk."""
        self.state.last_updated = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, "w") as f:
            json.dump(self.state.to_dict(), f, indent=2)

    def get_local_path(self, github_url: str) -> Path:
        """Get local path for a GitHub URL."""
        # Extract owner/repo from URL
        # https://github.com/owner/repo -> owner/repo
        parts = github_url.rstrip("/").split("/")
        if len(parts) >= 2:
            owner = parts[-2]
            repo = parts[-1].replace(".git", "")
            return self.clone_dir / owner / repo
        raise ValueError(f"Invalid GitHub URL: {github_url}")

    def is_cloned(self, github_url: str) -> bool:
        """Check if a repo is already cloned."""
        local_path = self.get_local_path(github_url)
        return (local_path / ".git").exists()

    def clone_repo(
        self,
        github_url: str,
        on_complete: CloneCallback = None,
    ) -> CloneResult:
        """
        Clone a single repository.

        Returns CloneResult with success/failure info.
        """
        start_time = time.time()
        local_path = self.get_local_path(github_url)
        retry_count = 0

        # Check if already completed
        if github_url in self.state.completed:
            if self.is_cloned(github_url):
                result = CloneResult(
                    github_url=github_url,
                    local_path=local_path,
                    success=True,
                    error=None,
                    duration_seconds=0,
                    retry_count=0,
                )
                if on_complete:
                    on_complete(result)
                return result

        # Check if previously failed too many times
        if self.state.failed.get(github_url, 0) >= self.max_retries:
            result = CloneResult(
                github_url=github_url,
                local_path=None,
                success=False,
                error="Max retries exceeded in previous run",
                duration_seconds=0,
                retry_count=self.state.failed[github_url],
            )
            if on_complete:
                on_complete(result)
            return result

        # Mark as in progress
        self.state.in_progress = github_url
        self.save_state()

        # Build clone command
        cmd = ["git", "clone"]
        if self.shallow:
            cmd.extend(["--depth", "1", "--single-branch"])
        cmd.extend([github_url, str(local_path)])

        # Retry loop
        last_error: str | None = None
        while retry_count < self.max_retries:
            try:
                # Remove existing directory if partial clone
                if local_path.exists():
                    shutil.rmtree(local_path)

                # Ensure parent directory exists
                local_path.parent.mkdir(parents=True, exist_ok=True)

                # Run clone
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                )

                if result.returncode == 0:
                    # Success
                    duration = time.time() - start_time
                    self.state.completed.append(github_url)
                    self.state.in_progress = None
                    if github_url in self.state.failed:
                        del self.state.failed[github_url]
                    self.save_state()

                    clone_result = CloneResult(
                        github_url=github_url,
                        local_path=local_path,
                        success=True,
                        error=None,
                        duration_seconds=duration,
                        retry_count=retry_count,
                    )
                    if on_complete:
                        on_complete(clone_result)
                    return clone_result

                # Clone failed
                last_error = result.stderr.strip()

                # Check for rate limiting (403)
                if "403" in last_error or "rate limit" in last_error.lower():
                    print(f"  Rate limited, waiting 60s...")
                    time.sleep(60)
                    retry_count += 1
                    continue

                # Check for not found (404)
                if "404" in last_error or "not found" in last_error.lower():
                    # Permanent failure
                    break

                # Check for auth required
                if "401" in last_error or "authentication" in last_error.lower():
                    # Permanent failure
                    break

                # Retry with backoff
                retry_count += 1
                delay = self.base_retry_delay * (2 ** (retry_count - 1))
                print(f"  Clone failed, retrying in {delay:.1f}s...")
                time.sleep(delay)

            except subprocess.TimeoutExpired:
                last_error = "Clone timed out after 5 minutes"
                retry_count += 1
                continue

            except Exception as e:
                last_error = str(e)
                retry_count += 1
                continue

        # Failed after all retries
        duration = time.time() - start_time
        self.state.failed[github_url] = retry_count
        self.state.in_progress = None
        self.save_state()

        clone_result = CloneResult(
            github_url=github_url,
            local_path=None,
            success=False,
            error=last_error,
            duration_seconds=duration,
            retry_count=retry_count,
        )
        if on_complete:
            on_complete(clone_result)
        return clone_result

    def clone_repos(
        self,
        github_urls: list[str],
        on_complete: CloneCallback = None,
        skip_completed: bool = True,
    ) -> list[CloneResult]:
        """
        Clone multiple repositories sequentially.

        Args:
            github_urls: List of GitHub URLs to clone
            on_complete: Callback for each completed clone
            skip_completed: Skip repos already in completed state

        Returns:
            List of CloneResult objects
        """
        results: list[CloneResult] = []

        for url in github_urls:
            if skip_completed and url in self.state.completed and self.is_cloned(url):
                # Already done, create a result for it
                result = CloneResult(
                    github_url=url,
                    local_path=self.get_local_path(url),
                    success=True,
                    error=None,
                    duration_seconds=0,
                    retry_count=0,
                )
                results.append(result)
                if on_complete:
                    on_complete(result)
                continue

            result = self.clone_repo(url, on_complete=on_complete)
            results.append(result)

        return results

    def get_pending_urls(self, github_urls: list[str]) -> list[str]:
        """Get URLs that haven't been successfully cloned yet."""
        return [
            url for url in github_urls
            if url not in self.state.completed or not self.is_cloned(url)
        ]

    def get_clone_summary(self) -> dict:
        """Get summary of clone state."""
        return {
            "completed": len(self.state.completed),
            "failed": len(self.state.failed),
            "in_progress": self.state.in_progress,
        }
