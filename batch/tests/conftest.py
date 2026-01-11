"""Fixtures for batch processing E2E tests."""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Creates isolated workspace with clone_dir, output_dir, questions_dir."""
    temp_dir = tempfile.mkdtemp()
    workspace = Path(temp_dir)
    (workspace / "clones").mkdir()
    (workspace / "graphs").mkdir()
    (workspace / "questions").mkdir()
    yield workspace
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_repos(temp_workspace: Path) -> dict[str, Path]:
    """Creates mock repos simulating same-name-different-owner scenario.

    Creates two repos with the same name but different owners:
    - owner1/click with code containing "owner1"
    - owner2/click with code containing "owner2"

    This allows tests to verify that source extraction matches the correct repo.
    """
    clones_dir = temp_workspace / "clones"

    # owner1/click - simple click repo
    owner1_click = clones_dir / "owner1" / "click"
    owner1_click.mkdir(parents=True)
    (owner1_click / ".git").mkdir()
    (owner1_click / "click.py").write_text(
        '''"""Click module for owner1."""


def cli():
    """CLI entry point for owner1."""
    print("owner1 click")


def helper():
    """Helper function."""
    return "owner1"
'''
    )

    # owner2/click - different click repo
    owner2_click = clones_dir / "owner2" / "click"
    owner2_click.mkdir(parents=True)
    (owner2_click / ".git").mkdir()
    (owner2_click / "click.py").write_text(
        '''"""Click module for owner2."""


def cli():
    """CLI entry point for owner2."""
    print("owner2 click")


def helper():
    """Helper function."""
    return "owner2"
'''
    )

    return {
        "owner1/click": owner1_click,
        "owner2/click": owner2_click,
    }


@pytest.fixture
def single_repo(temp_workspace: Path) -> Path:
    """Creates a single repo for basic tests."""
    clones_dir = temp_workspace / "clones"
    repo = clones_dir / "testowner" / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "main.py").write_text(
        '''"""Main module."""


def main():
    """Main entry point."""
    print("Hello world")


def helper():
    """Helper function."""
    return 42
'''
    )
    return repo
