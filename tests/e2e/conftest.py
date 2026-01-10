"""
E2E test fixtures for graph generation regression tests.

Provides session-scoped fixtures for cloning test repos and loading baselines.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.tmpdir import TempPathFactory

# Test repository configurations
# Pinned to specific commits for deterministic line numbers
TEST_REPOS = {
    "click": {
        "url": "https://github.com/pallets/click",
        "commit": "8.1.8",  # Tag for stable baseline
        "language": "python",
        "description": "Python CLI framework (~15k LOC)",
    },
    "log": {
        "url": "https://github.com/rust-lang/log",
        "commit": "0.4.27",  # Tag for stable baseline
        "language": "rust",
        "description": "Rust logging crate (~3k LOC)",
    },
}

# Path to committed baseline files
BASELINES_DIR = Path(__file__).parent / "baselines"

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _clone_repo(url: str, dest: Path, commit: str | None = None) -> None:
    """Clone a git repository at a specific commit/tag."""
    if commit:
        # Clone with enough depth to find the tag/commit
        subprocess.run(
            ["git", "clone", "--branch", commit, "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
        )


def _process_repo(repo_path: Path, output_dir: Path) -> Path:
    """Process a repo and return the output JSON path."""
    from batch.batch_processor import process_single_repo

    result = process_single_repo((repo_path, output_dir))
    if not result.success:
        raise RuntimeError(f"Failed to process {repo_path}: {result.error}")
    return Path(result.output_path) if result.output_path else output_dir / f"{repo_path.name}.json"


@pytest.fixture(scope="session")
def cloned_repos(tmp_path_factory: TempPathFactory) -> dict[str, Path]:
    """
    Clone all test repos once per test session.

    Returns dict mapping repo name to local path.
    """
    clone_dir = tmp_path_factory.mktemp("repos")
    repos = {}

    for name, config in TEST_REPOS.items():
        repo_path = clone_dir / name
        try:
            _clone_repo(config["url"], repo_path, config.get("commit"))
            repos[name] = repo_path
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Failed to clone {name}: {e}")

    return repos


@pytest.fixture(scope="session")
def baseline_graphs() -> dict[str, dict]:
    """
    Load committed baseline graphs.

    Returns dict mapping repo name to parsed JSON graph data.
    """
    baselines = {}

    for name in TEST_REPOS:
        baseline_file = BASELINES_DIR / f"{name}.json"
        if baseline_file.exists():
            with open(baseline_file, encoding="utf-8") as f:
                baselines[name] = json.load(f)

    if not baselines:
        pytest.skip(
            "No baseline files found. Run regenerate_baselines.py first."
        )

    return baselines


@pytest.fixture(scope="session")
def processed_graphs(
    cloned_repos: dict[str, Path],
    tmp_path_factory: TempPathFactory,
) -> dict[str, dict]:
    """
    Process all cloned repos and return their graph outputs.

    Returns dict mapping repo name to parsed JSON graph data.
    """
    output_dir = tmp_path_factory.mktemp("output")
    graphs = {}

    for name, repo_path in cloned_repos.items():
        try:
            output_path = _process_repo(repo_path, output_dir)
            with open(output_path, encoding="utf-8") as f:
                graphs[name] = json.load(f)
        except Exception as e:
            pytest.fail(f"Failed to process {name}: {e}")

    return graphs


# Individual repo fixtures for more granular testing


@pytest.fixture(scope="session")
def click_repo(cloned_repos: dict[str, Path]) -> Path:
    """Get the cloned click repo path."""
    if "click" not in cloned_repos:
        pytest.skip("click repo not available")
    return cloned_repos["click"]


@pytest.fixture(scope="session")
def log_repo(cloned_repos: dict[str, Path]) -> Path:
    """Get the cloned log repo path."""
    if "log" not in cloned_repos:
        pytest.skip("log repo not available")
    return cloned_repos["log"]


@pytest.fixture(scope="session")
def click_baseline(baseline_graphs: dict[str, dict]) -> dict:
    """Get the click baseline graph."""
    if "click" not in baseline_graphs:
        pytest.skip("click baseline not available")
    return baseline_graphs["click"]


@pytest.fixture(scope="session")
def log_baseline(baseline_graphs: dict[str, dict]) -> dict:
    """Get the log baseline graph."""
    if "log" not in baseline_graphs:
        pytest.skip("log baseline not available")
    return baseline_graphs["log"]


@pytest.fixture(scope="session")
def click_graph(processed_graphs: dict[str, dict]) -> dict:
    """Get the processed click graph."""
    if "click" not in processed_graphs:
        pytest.skip("click graph not processed")
    return processed_graphs["click"]


@pytest.fixture(scope="session")
def log_graph(processed_graphs: dict[str, dict]) -> dict:
    """Get the processed log graph."""
    if "log" not in processed_graphs:
        pytest.skip("log graph not processed")
    return processed_graphs["log"]
