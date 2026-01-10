"""
Graph comparison utilities for e2e regression tests.

Provides deterministic comparison of graph JSON outputs to ensure
optimizations don't change the output.
"""
from __future__ import annotations

from typing import Any


def normalize_graph(graph_data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize graph data for deterministic comparison.

    Sorts nodes and relationships to ensure order-independent comparison.

    Args:
        graph_data: Raw graph data with nodes, relationships, metadata

    Returns:
        Normalized graph with sorted nodes and relationships
    """
    # Sort nodes by qualified_name (or name for nodes without qualified_name)
    nodes = sorted(
        graph_data.get("nodes", []),
        key=lambda n: (
            n.get("labels", [""])[0],
            n.get("properties", {}).get("qualified_name", "")
            or n.get("properties", {}).get("name", "")
            or n.get("properties", {}).get("path", ""),
        ),
    )

    # Sort relationships by (from_key, type, to_key)
    relationships = sorted(
        graph_data.get("relationships", []),
        key=lambda r: (
            r.get("from_key", ""),
            r.get("type", ""),
            r.get("to_key", ""),
        ),
    )

    return {
        "nodes": nodes,
        "relationships": relationships,
        "metadata": graph_data.get("metadata", {}),
    }


def get_node_signature(node: dict[str, Any]) -> str:
    """Get a unique signature for a node for diff reporting."""
    labels = node.get("labels", [])
    props = node.get("properties", {})
    qn = props.get("qualified_name") or props.get("name") or props.get("path", "")
    return f"{labels[0] if labels else 'UNKNOWN'}:{qn}"


def get_rel_signature(rel: dict[str, Any]) -> str:
    """Get a unique signature for a relationship for diff reporting."""
    return f"({rel.get('from_key', '?')})-[{rel.get('type', '?')}]->({rel.get('to_key', '?')})"


class GraphDiff:
    """Represents differences between two graphs."""

    def __init__(self) -> None:
        self.node_count_diff: tuple[int, int] | None = None
        self.rel_count_diff: tuple[int, int] | None = None
        self.missing_nodes: list[str] = []
        self.extra_nodes: list[str] = []
        self.missing_rels: list[str] = []
        self.extra_rels: list[str] = []
        self.node_property_diffs: list[tuple[str, str, Any, Any]] = []

    @property
    def has_differences(self) -> bool:
        """Check if there are any differences."""
        return bool(
            self.node_count_diff
            or self.rel_count_diff
            or self.missing_nodes
            or self.extra_nodes
            or self.missing_rels
            or self.extra_rels
            or self.node_property_diffs
        )

    def __str__(self) -> str:
        """Human-readable diff summary."""
        lines = []
        if self.node_count_diff:
            lines.append(
                f"Node count: expected {self.node_count_diff[0]}, got {self.node_count_diff[1]}"
            )
        if self.rel_count_diff:
            lines.append(
                f"Relationship count: expected {self.rel_count_diff[0]}, got {self.rel_count_diff[1]}"
            )
        if self.missing_nodes:
            lines.append(f"Missing nodes ({len(self.missing_nodes)}):")
            for sig in self.missing_nodes[:10]:
                lines.append(f"  - {sig}")
            if len(self.missing_nodes) > 10:
                lines.append(f"  ... and {len(self.missing_nodes) - 10} more")
        if self.extra_nodes:
            lines.append(f"Extra nodes ({len(self.extra_nodes)}):")
            for sig in self.extra_nodes[:10]:
                lines.append(f"  + {sig}")
            if len(self.extra_nodes) > 10:
                lines.append(f"  ... and {len(self.extra_nodes) - 10} more")
        if self.missing_rels:
            lines.append(f"Missing relationships ({len(self.missing_rels)}):")
            for sig in self.missing_rels[:10]:
                lines.append(f"  - {sig}")
            if len(self.missing_rels) > 10:
                lines.append(f"  ... and {len(self.missing_rels) - 10} more")
        if self.extra_rels:
            lines.append(f"Extra relationships ({len(self.extra_rels)}):")
            for sig in self.extra_rels[:10]:
                lines.append(f"  + {sig}")
            if len(self.extra_rels) > 10:
                lines.append(f"  ... and {len(self.extra_rels) - 10} more")
        if self.node_property_diffs:
            lines.append(f"Property differences ({len(self.node_property_diffs)}):")
            for node_sig, prop, expected, actual in self.node_property_diffs[:5]:
                lines.append(f"  {node_sig}.{prop}: {expected!r} != {actual!r}")
            if len(self.node_property_diffs) > 5:
                lines.append(f"  ... and {len(self.node_property_diffs) - 5} more")
        return "\n".join(lines) if lines else "No differences"


def compare_graphs(actual: dict[str, Any], expected: dict[str, Any]) -> GraphDiff:
    """
    Compare two graphs and return detailed differences.

    Args:
        actual: The graph produced by the current implementation
        expected: The baseline graph to compare against

    Returns:
        GraphDiff object with all differences
    """
    diff = GraphDiff()

    actual_norm = normalize_graph(actual)
    expected_norm = normalize_graph(expected)

    # Compare metadata counts
    actual_nodes = actual_norm["metadata"].get("total_nodes", len(actual_norm["nodes"]))
    expected_nodes = expected_norm["metadata"].get(
        "total_nodes", len(expected_norm["nodes"])
    )
    if actual_nodes != expected_nodes:
        diff.node_count_diff = (expected_nodes, actual_nodes)

    actual_rels = actual_norm["metadata"].get(
        "total_relationships", len(actual_norm["relationships"])
    )
    expected_rels = expected_norm["metadata"].get(
        "total_relationships", len(expected_norm["relationships"])
    )
    if actual_rels != expected_rels:
        diff.rel_count_diff = (expected_rels, actual_rels)

    # Build signature sets for nodes
    actual_node_sigs = {get_node_signature(n): n for n in actual_norm["nodes"]}
    expected_node_sigs = {get_node_signature(n): n for n in expected_norm["nodes"]}

    diff.missing_nodes = [
        sig for sig in expected_node_sigs if sig not in actual_node_sigs
    ]
    diff.extra_nodes = [sig for sig in actual_node_sigs if sig not in expected_node_sigs]

    # Compare node properties for matching nodes
    for sig in actual_node_sigs:
        if sig in expected_node_sigs:
            actual_props = actual_node_sigs[sig].get("properties", {})
            expected_props = expected_node_sigs[sig].get("properties", {})
            for key in set(actual_props.keys()) | set(expected_props.keys()):
                actual_val = actual_props.get(key)
                expected_val = expected_props.get(key)
                if actual_val != expected_val:
                    diff.node_property_diffs.append(
                        (sig, key, expected_val, actual_val)
                    )

    # Build signature sets for relationships
    actual_rel_sigs = {get_rel_signature(r) for r in actual_norm["relationships"]}
    expected_rel_sigs = {get_rel_signature(r) for r in expected_norm["relationships"]}

    diff.missing_rels = [sig for sig in expected_rel_sigs if sig not in actual_rel_sigs]
    diff.extra_rels = [sig for sig in actual_rel_sigs if sig not in expected_rel_sigs]

    return diff


def assert_graphs_equal(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    ignore_properties: set[str] | None = None,
    count_tolerance: int = 5,
) -> None:
    """
    Assert two graphs are semantically equal.

    Raises AssertionError with detailed diff on mismatch.

    Args:
        actual: The graph produced by the current implementation
        expected: The baseline graph to compare against
        ignore_properties: Optional set of property names to ignore in comparison
        count_tolerance: Allow this many nodes/relationships difference (for non-determinism)
    """
    diff = compare_graphs(actual, expected)

    # Filter out ignored property diffs
    if ignore_properties and diff.node_property_diffs:
        diff.node_property_diffs = [
            (sig, prop, exp, act)
            for sig, prop, exp, act in diff.node_property_diffs
            if prop not in ignore_properties
        ]

    # Apply tolerance to count differences
    if diff.node_count_diff:
        expected_nodes, actual_nodes = diff.node_count_diff
        if abs(expected_nodes - actual_nodes) <= count_tolerance:
            diff.node_count_diff = None

    if diff.rel_count_diff:
        expected_rels, actual_rels = diff.rel_count_diff
        if abs(expected_rels - actual_rels) <= count_tolerance:
            diff.rel_count_diff = None

    if diff.has_differences:
        raise AssertionError(f"Graphs differ:\n{diff}")
