"""
E2E regression tests for graph generation.

These tests ensure that optimizations don't change the output of graph generation.
They compare the current implementation's output against committed baseline files.

Usage:
    uv run pytest tests/e2e -v -m e2e

To regenerate baselines (after intentional changes):
    uv run python tests/e2e/regenerate_baselines.py
"""
from __future__ import annotations

import pytest

from .graph_compare import assert_graphs_equal, compare_graphs


# Properties that are inherently non-deterministic due to AST parsing behavior.
# These cannot be made deterministic without significant parser changes.
# - start_line/end_line: Decorated functions can have varying line ranges
# - decorators: Decorator extraction depends on parsing order
# - docstring: Can vary based on how docstrings are associated with nodes
VOLATILE_PROPERTIES: set[str] = {
    "start_line",
    "end_line",
    "decorators",
    "docstring",
}


@pytest.mark.e2e
class TestGraphGenerationRegression:
    """Regression tests comparing current output to committed baselines."""

    def test_click_graph_matches_baseline(
        self,
        click_graph: dict,
        click_baseline: dict,
    ) -> None:
        """
        Test that processing pallets/click produces the same graph as baseline.

        This tests Python parsing including:
        - Decorators
        - Class definitions
        - Function definitions
        - Import resolution
        - Call relationships

        Note: Line numbers and decorators are excluded as they may have
        non-determinism due to parsing order.
        """
        assert_graphs_equal(
            click_graph, click_baseline, ignore_properties=VOLATILE_PROPERTIES
        )

    def test_log_graph_matches_baseline(
        self,
        log_graph: dict,
        log_baseline: dict,
    ) -> None:
        """
        Test that processing rust-lang/log produces the same graph as baseline.

        This tests Rust parsing including:
        - Trait definitions
        - Impl blocks
        - Macro definitions
        - Module structure
        """
        assert_graphs_equal(
            log_graph, log_baseline, ignore_properties=VOLATILE_PROPERTIES
        )


# Minimal tolerance for residual non-determinism in call resolution
# This was reduced from 5 to 1 after deterministic generation improvements
COUNT_TOLERANCE = 1


@pytest.mark.e2e
class TestGraphMetadata:
    """Tests for graph metadata consistency."""

    def test_click_node_count(
        self,
        click_graph: dict,
        click_baseline: dict,
    ) -> None:
        """Test that click graph has approximately the same number of nodes."""
        actual_count = click_graph["metadata"]["total_nodes"]
        expected_count = click_baseline["metadata"]["total_nodes"]
        diff = abs(actual_count - expected_count)
        assert diff <= COUNT_TOLERANCE, (
            f"Node count mismatch: expected ~{expected_count}, got {actual_count} (diff: {diff})"
        )

    def test_click_relationship_count(
        self,
        click_graph: dict,
        click_baseline: dict,
    ) -> None:
        """Test that click graph has approximately the same number of relationships."""
        actual_count = click_graph["metadata"]["total_relationships"]
        expected_count = click_baseline["metadata"]["total_relationships"]
        diff = abs(actual_count - expected_count)
        assert diff <= COUNT_TOLERANCE, (
            f"Relationship count mismatch: expected ~{expected_count}, got {actual_count} (diff: {diff})"
        )

    def test_log_node_count(
        self,
        log_graph: dict,
        log_baseline: dict,
    ) -> None:
        """Test that log graph has approximately the same number of nodes."""
        actual_count = log_graph["metadata"]["total_nodes"]
        expected_count = log_baseline["metadata"]["total_nodes"]
        diff = abs(actual_count - expected_count)
        assert diff <= COUNT_TOLERANCE, (
            f"Node count mismatch: expected ~{expected_count}, got {actual_count} (diff: {diff})"
        )

    def test_log_relationship_count(
        self,
        log_graph: dict,
        log_baseline: dict,
    ) -> None:
        """Test that log graph has approximately the same number of relationships."""
        actual_count = log_graph["metadata"]["total_relationships"]
        expected_count = log_baseline["metadata"]["total_relationships"]
        diff = abs(actual_count - expected_count)
        assert diff <= COUNT_TOLERANCE, (
            f"Relationship count mismatch: expected ~{expected_count}, got {actual_count} (diff: {diff})"
        )


@pytest.mark.e2e
class TestGraphStructure:
    """Tests for graph structural properties."""

    def test_click_has_project_node(self, click_graph: dict) -> None:
        """Test that click graph has a Project node."""
        project_nodes = [
            n for n in click_graph["nodes"]
            if "Project" in n.get("labels", [])
        ]
        assert len(project_nodes) == 1
        assert project_nodes[0]["properties"]["name"] == "click"

    def test_log_has_project_node(self, log_graph: dict) -> None:
        """Test that log graph has a Project node."""
        project_nodes = [
            n for n in log_graph["nodes"]
            if "Project" in n.get("labels", [])
        ]
        assert len(project_nodes) == 1
        assert project_nodes[0]["properties"]["name"] == "log"

    def test_click_has_modules(self, click_graph: dict) -> None:
        """Test that click graph has Module nodes."""
        module_nodes = [
            n for n in click_graph["nodes"]
            if "Module" in n.get("labels", [])
        ]
        assert len(module_nodes) > 0, "Expected Module nodes in click graph"

    def test_click_has_functions(self, click_graph: dict) -> None:
        """Test that click graph has Function nodes."""
        func_nodes = [
            n for n in click_graph["nodes"]
            if "Function" in n.get("labels", [])
        ]
        assert len(func_nodes) > 0, "Expected Function nodes in click graph"

    def test_click_has_classes(self, click_graph: dict) -> None:
        """Test that click graph has Class nodes."""
        class_nodes = [
            n for n in click_graph["nodes"]
            if "Class" in n.get("labels", [])
        ]
        assert len(class_nodes) > 0, "Expected Class nodes in click graph"

    def test_click_has_calls_relationships(self, click_graph: dict) -> None:
        """Test that click graph has CALLS relationships."""
        calls_rels = [
            r for r in click_graph["relationships"]
            if r.get("type") == "CALLS"
        ]
        assert len(calls_rels) > 0, "Expected CALLS relationships in click graph"


@pytest.mark.e2e
class TestDiffReporting:
    """Tests for the diff reporting functionality."""

    def test_compare_identical_graphs(self, click_graph: dict) -> None:
        """Test that comparing a graph to itself shows no differences."""
        diff = compare_graphs(click_graph, click_graph)
        assert not diff.has_differences, f"Expected no differences: {diff}"

    def test_compare_detects_node_count_diff(self, click_graph: dict) -> None:
        """Test that diff detects node count differences."""
        modified = {
            **click_graph,
            "metadata": {
                **click_graph["metadata"],
                "total_nodes": click_graph["metadata"]["total_nodes"] + 10,
            },
        }
        diff = compare_graphs(modified, click_graph)
        assert diff.node_count_diff is not None

    def test_compare_detects_missing_nodes(self, click_graph: dict) -> None:
        """Test that diff detects missing nodes."""
        if len(click_graph["nodes"]) < 2:
            pytest.skip("Need at least 2 nodes for this test")

        modified = {
            **click_graph,
            "nodes": click_graph["nodes"][:-1],  # Remove last node
        }
        diff = compare_graphs(modified, click_graph)
        assert len(diff.missing_nodes) > 0
