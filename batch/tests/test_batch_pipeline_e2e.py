"""End-to-end tests for batch processing pipeline.

Tests the full pipeline: clone -> process -> question generation
with focus on name collision scenarios.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add batch directory to path for imports
BATCH_DIR = Path(__file__).parent.parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BATCH_DIR))

from batch_processor import process_single_repo
from batch_question_generator import get_repo_path_for_graph


class TestNameCollisionPrevention:
    """Tests that repos with same name but different owners produce unique outputs."""

    @pytest.mark.e2e
    def test_same_name_repos_produce_separate_graphs(
        self, temp_workspace: Path, mock_repos: dict[str, Path]
    ) -> None:
        """Two repos with same name but different owners produce unique graph files."""
        graphs_dir = temp_workspace / "graphs"

        # Process both repos
        for repo_path in mock_repos.values():
            result = process_single_repo((repo_path, graphs_dir))
            assert result.success, f"Processing failed: {result.error}"

        # Should have TWO separate graph files
        graph_files = list(graphs_dir.glob("*.json"))
        assert len(graph_files) == 2, f"Expected 2 graph files, got {len(graph_files)}: {[f.name for f in graph_files]}"

        # Files should be owner-qualified
        graph_names = {f.stem for f in graph_files}
        assert "owner1__click" in graph_names, f"Expected owner1__click in {graph_names}"
        assert "owner2__click" in graph_names, f"Expected owner2__click in {graph_names}"

    @pytest.mark.e2e
    def test_old_behavior_causes_collision(
        self, temp_workspace: Path, mock_repos: dict[str, Path]
    ) -> None:
        """Demonstrates that without fix, repos would overwrite each other.

        This test documents the bug - with owner__repo naming, both files exist.
        With old repo.name naming, only one would exist.
        """
        graphs_dir = temp_workspace / "graphs"

        # Process both repos
        for repo_path in mock_repos.values():
            process_single_repo((repo_path, graphs_dir))

        # With fix: 2 files (owner1__click.json, owner2__click.json)
        # Without fix: 1 file (click.json) - later overwrites earlier
        graph_files = list(graphs_dir.glob("*.json"))
        assert len(graph_files) == 2, "Name collision detected - only one graph file exists"


class TestGraphToRepoMatching:
    """Tests that graph files correctly match back to their source repos."""

    @pytest.mark.e2e
    def test_graph_matches_correct_repo(
        self, temp_workspace: Path, mock_repos: dict[str, Path]
    ) -> None:
        """Each graph file matches back to its correct source repo."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Process repos
        for repo_path in mock_repos.values():
            result = process_single_repo((repo_path, graphs_dir))
            assert result.success, f"Processing failed: {result.error}"

        # Each graph should match its correct repo
        for graph_file in graphs_dir.glob("*.json"):
            matched_repo = get_repo_path_for_graph(graph_file, clones_dir)
            assert matched_repo is not None, f"No repo found for {graph_file.name}"

            # Verify match is correct owner
            owner = graph_file.stem.split("__")[0]
            assert matched_repo.parent.name == owner, (
                f"Graph {graph_file.name} matched to wrong owner: "
                f"expected {owner}, got {matched_repo.parent.name}"
            )

    @pytest.mark.e2e
    def test_deterministic_matching(
        self, temp_workspace: Path, mock_repos: dict[str, Path]
    ) -> None:
        """Graph-to-repo matching is deterministic, not random first-match."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Process repos
        for repo_path in mock_repos.values():
            process_single_repo((repo_path, graphs_dir))

        # Run matching multiple times - should always get same result
        for graph_file in graphs_dir.glob("*.json"):
            results = [
                get_repo_path_for_graph(graph_file, clones_dir)
                for _ in range(5)
            ]
            # All results should be identical
            assert all(r == results[0] for r in results), (
                f"Non-deterministic matching for {graph_file.name}: {results}"
            )


class TestSingleRepoProcessing:
    """Tests for single repo processing (basic functionality)."""

    @pytest.mark.e2e
    def test_single_repo_produces_graph(
        self, temp_workspace: Path, single_repo: Path
    ) -> None:
        """Single repo is processed correctly with owner prefix."""
        graphs_dir = temp_workspace / "graphs"

        result = process_single_repo((single_repo, graphs_dir))
        assert result.success, f"Processing failed: {result.error}"

        # Graph file should exist with owner__repo naming
        graph_files = list(graphs_dir.glob("*.json"))
        assert len(graph_files) == 1, f"Expected 1 graph file, got {len(graph_files)}"

        # Should be named testowner__myrepo.json
        assert graph_files[0].stem == "testowner__myrepo", (
            f"Expected testowner__myrepo, got {graph_files[0].stem}"
        )

    @pytest.mark.e2e
    def test_graph_contains_valid_data(
        self, temp_workspace: Path, single_repo: Path
    ) -> None:
        """Generated graph contains valid nodes and relationships."""
        graphs_dir = temp_workspace / "graphs"

        result = process_single_repo((single_repo, graphs_dir))
        assert result.success

        # Load and validate graph
        graph_file = graphs_dir / "testowner__myrepo.json"
        with open(graph_file) as f:
            data = json.load(f)

        assert "nodes" in data
        assert "relationships" in data
        assert "metadata" in data
        assert data["metadata"]["total_nodes"] > 0


class TestBackwardsCompatibility:
    """Tests for backwards compatibility with old-style graph files."""

    @pytest.mark.e2e
    def test_old_style_graph_fallback_search(
        self, temp_workspace: Path
    ) -> None:
        """Old-style graph files (no owner prefix) fall back to search."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Create a single repo
        repo = clones_dir / "standalone" / "myrepo"
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "main.py").write_text("def main(): pass")

        # Create old-style graph file (just repo name)
        old_graph = graphs_dir / "myrepo.json"
        old_graph.write_text('{"nodes":[],"relationships":[],"metadata":{}}')

        # Should find the repo via fallback search
        matched = get_repo_path_for_graph(old_graph, clones_dir)
        assert matched is not None, "Old-style graph should find repo via search"
        assert matched.name == "myrepo"


class TestSourceExtraction:
    """Tests that source extraction matches the correct repo."""

    @pytest.mark.e2e
    def test_extracted_source_contains_owner_identifier(
        self, temp_workspace: Path, mock_repos: dict[str, Path]
    ) -> None:
        """Source code extracted from graph matches the correct repo's content."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Process repos
        for repo_path in mock_repos.values():
            process_single_repo((repo_path, graphs_dir))

        # Import here to avoid import issues
        from codebase_rag.graph_loader import GraphLoader
        from codebase_rag.node_text_extractor import NodeTextExtractor

        # For each graph, verify source extraction
        for graph_file in graphs_dir.glob("*.json"):
            repo_path = get_repo_path_for_graph(graph_file, clones_dir)
            assert repo_path is not None

            owner = graph_file.stem.split("__")[0]

            # Load graph and extract a function's source
            loader = GraphLoader(str(graph_file))
            functions = loader.find_nodes_by_label("Function")

            if functions:
                extractor = NodeTextExtractor(graph_file, repo_path)
                result = extractor.extract(functions[0].node_id)

                # Source should contain owner-specific content
                if result.code_chunk:
                    assert owner in result.code_chunk, (
                        f"Source from {graph_file.name} doesn't contain '{owner}': "
                        f"{result.code_chunk[:100]}..."
                    )


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.e2e
    def test_missing_repo_returns_none(
        self, temp_workspace: Path
    ) -> None:
        """get_repo_path_for_graph returns None for missing repos."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Create a graph file with no matching repo
        orphan_graph = graphs_dir / "nonexistent__repo.json"
        orphan_graph.write_text('{"nodes":[],"relationships":[],"metadata":{}}')

        matched = get_repo_path_for_graph(orphan_graph, clones_dir)
        assert matched is None

    @pytest.mark.e2e
    def test_handles_repos_without_git_dir(
        self, temp_workspace: Path
    ) -> None:
        """Directories without .git are not matched as repos."""
        clones_dir = temp_workspace / "clones"
        graphs_dir = temp_workspace / "graphs"

        # Create directory that looks like a repo but has no .git
        not_a_repo = clones_dir / "owner" / "notarepo"
        not_a_repo.mkdir(parents=True)
        (not_a_repo / "main.py").write_text("print('hi')")

        # Create graph
        graph = graphs_dir / "owner__notarepo.json"
        graph.write_text('{"nodes":[],"relationships":[],"metadata":{}}')

        matched = get_repo_path_for_graph(graph, clones_dir)
        assert matched is None, "Should not match directory without .git"
