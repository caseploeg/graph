#!/usr/bin/env python3
"""
Regenerate baseline JSON files for e2e tests.

This script clones test repositories, processes them with the current
graph generation implementation, and saves the outputs as baseline files.

WARNING: Baseline changes require explicit approval in CI!
         Use --force to skip confirmation prompt.

Usage:
    uv run python tests/e2e/regenerate_baselines.py [--force]

After running, commit with the required marker:
    git add tests/e2e/baselines/
    git commit -m "[update-baselines] Regenerate e2e baselines after <reason>"
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Test repositories to generate baselines for
# Pinned to specific tags for deterministic line numbers
REPOS = {
    "click": {
        "url": "https://github.com/pallets/click",
        "commit": "8.1.8",
    },
    "log": {
        "url": "https://github.com/rust-lang/log",
        "commit": "0.4.27",
    },
}

BASELINES_DIR = Path(__file__).parent / "baselines"


def clone_repo(url: str, dest: Path, commit: str | None = None) -> None:
    """Clone a git repository at a specific commit/tag."""
    print(f"  Cloning {url} @ {commit or 'HEAD'}...")
    if commit:
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


def process_repo(repo_path: Path, output_dir: Path) -> Path:
    """Process a repo and return the output JSON path."""
    from batch.batch_processor import process_single_repo

    result = process_single_repo((repo_path, output_dir))
    if not result.success:
        raise RuntimeError(f"Failed to process {repo_path}: {result.error}")

    output_path = output_dir / f"{repo_path.name}.json"
    if not output_path.exists():
        raise RuntimeError(f"Output file not found: {output_path}")

    return output_path


def main() -> int:
    """Main entry point."""
    force = "--force" in sys.argv or "-f" in sys.argv

    print("=" * 60)
    print("Regenerating E2E Test Baselines")
    print("=" * 60)
    print()

    if not force:
        print("WARNING: This will regenerate baseline files!")
        print()
        print("Baseline changes require explicit CI approval via:")
        print("  - PR label: 'update-baselines'")
        print("  - Commit message containing: '[update-baselines]'")
        print()
        response = input("Continue? [y/N] ").strip().lower()
        if response != "y":
            print("Aborted.")
            return 1
        print()

    # Ensure baselines directory exists
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)

    # Create temporary directory for cloning and processing
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        clone_dir = tmpdir_path / "repos"
        output_dir = tmpdir_path / "output"
        clone_dir.mkdir()
        output_dir.mkdir()

        successes = []
        failures = []

        for name, config in REPOS.items():
            print(f"Processing {name}...")
            repo_path = clone_dir / name

            try:
                # Clone
                clone_repo(config["url"], repo_path, config.get("commit"))

                # Process
                print(f"  Generating graph...")
                output_path = process_repo(repo_path, output_dir)

                # Load and pretty-print for consistent formatting
                with open(output_path, encoding="utf-8") as f:
                    graph_data = json.load(f)

                # Save to baselines
                baseline_path = BASELINES_DIR / f"{name}.json"
                with open(baseline_path, "w", encoding="utf-8") as f:
                    json.dump(graph_data, f, indent=2, ensure_ascii=False)

                # Report stats
                node_count = graph_data["metadata"]["total_nodes"]
                rel_count = graph_data["metadata"]["total_relationships"]
                print(f"  -> {baseline_path}")
                print(f"     Nodes: {node_count}, Relationships: {rel_count}")
                print()

                successes.append(name)

            except Exception as e:
                print(f"  ERROR: {e}")
                print()
                failures.append((name, str(e)))

    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Successful: {len(successes)}")
    for name in successes:
        print(f"  - {name}")
    if failures:
        print(f"Failed: {len(failures)}")
        for name, error in failures:
            print(f"  - {name}: {error}")

    print()
    if successes:
        print("Next steps:")
        print("  1. Review the generated baseline files in tests/e2e/baselines/")
        print("  2. Commit with the required marker for CI approval:")
        print("     git add tests/e2e/baselines/")
        print("     git commit -m '[update-baselines] Regenerate after <describe change>'")
        print()
        print("  Or add 'update-baselines' label to your PR.")
    print()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
