"""Tests for NodeTextExtractor, especially nested function handling."""
from __future__ import annotations

import json
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from codebase_rag.node_text_extractor import NodeTextExtractor, NodeTextResult


@pytest.fixture
def temp_repo() -> Generator[Path, None, None]:
    """Create a temporary repository with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create a Python file with nested functions
        test_file = repo_path / "nested.py"
        test_file.write_text(
            """\
def outer_function():
    x = 1

    def inner_function():
        y = 2
        return y

    def deeply_nested():
        def level3():
            z = 3
            return z
        return level3()

    return inner_function() + deeply_nested()


class OuterClass:
    def method(self):
        pass


def regular_function():
    return 42
"""
        )

        yield repo_path


def create_nested_function_graph(repo_path: Path) -> dict:
    """Create a graph with nested function relationships."""
    return {
        "nodes": [
            # Module node
            {
                "node_id": 1,
                "labels": ["Module"],
                "properties": {
                    "name": "nested",
                    "qualified_name": "test.nested",
                    "path": "nested.py",
                },
            },
            # Outer function (defined by Module)
            {
                "node_id": 2,
                "labels": ["Function"],
                "properties": {
                    "name": "outer_function",
                    "qualified_name": "test.nested.outer_function",
                    "start_line": 1,
                    "end_line": 15,
                },
            },
            # Inner function (defined by outer_function - nested!)
            {
                "node_id": 3,
                "labels": ["Function"],
                "properties": {
                    "name": "inner_function",
                    "qualified_name": "test.nested.outer_function.inner_function",
                    "start_line": 4,
                    "end_line": 6,
                },
            },
            # Deeply nested function (defined by outer_function)
            {
                "node_id": 4,
                "labels": ["Function"],
                "properties": {
                    "name": "deeply_nested",
                    "qualified_name": "test.nested.outer_function.deeply_nested",
                    "start_line": 8,
                    "end_line": 12,
                },
            },
            # Level 3 nested function (defined by deeply_nested)
            {
                "node_id": 5,
                "labels": ["Function"],
                "properties": {
                    "name": "level3",
                    "qualified_name": "test.nested.outer_function.deeply_nested.level3",
                    "start_line": 9,
                    "end_line": 11,
                },
            },
            # Regular class (lines 18-20 in 1-based, includes class line)
            {
                "node_id": 6,
                "labels": ["Class"],
                "properties": {
                    "name": "OuterClass",
                    "qualified_name": "test.nested.OuterClass",
                    "start_line": 17,
                    "end_line": 20,
                },
            },
            # Method in class (lines 19-20 in 1-based)
            {
                "node_id": 7,
                "labels": ["Method"],
                "properties": {
                    "name": "method",
                    "qualified_name": "test.nested.OuterClass.method",
                    "start_line": 18,
                    "end_line": 19,
                },
            },
            # Regular function (direct child of module)
            {
                "node_id": 8,
                "labels": ["Function"],
                "properties": {
                    "name": "regular_function",
                    "qualified_name": "test.nested.regular_function",
                    "start_line": 22,
                    "end_line": 23,
                },
            },
        ],
        "relationships": [
            # Module defines outer_function
            {"from_id": 1, "to_id": 2, "type": "DEFINES", "properties": {}},
            # outer_function defines inner_function (nested!)
            {"from_id": 2, "to_id": 3, "type": "DEFINES", "properties": {}},
            # outer_function defines deeply_nested
            {"from_id": 2, "to_id": 4, "type": "DEFINES", "properties": {}},
            # deeply_nested defines level3 (3 levels deep!)
            {"from_id": 4, "to_id": 5, "type": "DEFINES", "properties": {}},
            # Module defines OuterClass
            {"from_id": 1, "to_id": 6, "type": "DEFINES", "properties": {}},
            # OuterClass defines method
            {"from_id": 6, "to_id": 7, "type": "DEFINES_METHOD", "properties": {}},
            # Module defines regular_function
            {"from_id": 1, "to_id": 8, "type": "DEFINES", "properties": {}},
        ],
        "metadata": {
            "total_nodes": 8,
            "total_relationships": 7,
            "exported_at": "2025-01-01T00:00:00Z",
        },
    }


@pytest.fixture
def graph_file(temp_repo: Path) -> Generator[str, None, None]:
    """Create a temporary graph file."""
    graph_data = create_nested_function_graph(temp_repo)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(graph_data, f)
        f.flush()
        yield f.name
    Path(f.name).unlink()


@pytest.fixture
def extractor(graph_file: str, temp_repo: Path) -> NodeTextExtractor:
    """Create a NodeTextExtractor with test data."""
    return NodeTextExtractor(graph_file, temp_repo)


class TestNestedFunctionExtraction:
    """Tests for extracting source from nested functions."""

    def test_extract_regular_function(self, extractor: NodeTextExtractor) -> None:
        """Regular function at module level should work."""
        result = extractor.extract(8)  # regular_function
        assert result.error is None
        assert result.code_chunk is not None
        assert "return 42" in result.code_chunk
        assert result.qualified_name == "test.nested.regular_function"

    def test_extract_nested_function(self, extractor: NodeTextExtractor) -> None:
        """Nested function (inside another function) should work."""
        result = extractor.extract(3)  # inner_function
        assert result.error is None, f"Error: {result.error}"
        assert result.code_chunk is not None
        assert "y = 2" in result.code_chunk
        assert result.qualified_name == "test.nested.outer_function.inner_function"

    def test_extract_deeply_nested_function(self, extractor: NodeTextExtractor) -> None:
        """Deeply nested function (2 levels) should work."""
        result = extractor.extract(4)  # deeply_nested
        assert result.error is None, f"Error: {result.error}"
        assert result.code_chunk is not None
        assert "def level3" in result.code_chunk
        assert result.qualified_name == "test.nested.outer_function.deeply_nested"

    def test_extract_triple_nested_function(self, extractor: NodeTextExtractor) -> None:
        """Triple nested function (3 levels deep) should work."""
        result = extractor.extract(5)  # level3
        assert result.error is None, f"Error: {result.error}"
        assert result.code_chunk is not None
        assert "z = 3" in result.code_chunk
        assert (
            result.qualified_name
            == "test.nested.outer_function.deeply_nested.level3"
        )

    def test_extract_outer_function(self, extractor: NodeTextExtractor) -> None:
        """Outer function containing nested functions should work."""
        result = extractor.extract(2)  # outer_function
        assert result.error is None
        assert result.code_chunk is not None
        assert "def outer_function" in result.code_chunk
        assert "def inner_function" in result.code_chunk

    def test_extract_method(self, extractor: NodeTextExtractor) -> None:
        """Regular method in a class should still work."""
        result = extractor.extract(7)  # OuterClass.method
        assert result.error is None, f"Error: {result.error}"
        assert result.code_chunk is not None
        assert "def method" in result.code_chunk

    def test_extract_class(self, extractor: NodeTextExtractor) -> None:
        """Class should be extractable."""
        result = extractor.extract(6)  # OuterClass
        assert result.error is None
        assert result.code_chunk is not None
        assert "class OuterClass" in result.code_chunk


class TestModuleNodeExtraction:
    """Tests for Module/File node extraction."""

    def test_extract_module(self, extractor: NodeTextExtractor) -> None:
        """Module node should return full file content."""
        result = extractor.extract(1)  # Module
        assert result.error is None
        assert result.code_chunk is not None
        # Module should contain the entire file
        assert "def outer_function" in result.code_chunk
        assert "def regular_function" in result.code_chunk
        assert "class OuterClass" in result.code_chunk


class TestErrorCases:
    """Tests for error handling."""

    def test_node_not_found(self, extractor: NodeTextExtractor) -> None:
        """Non-existent node should return error."""
        result = extractor.extract(999)
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_structural_node(self, graph_file: str, temp_repo: Path) -> None:
        """Structural nodes (Package, Folder) should return appropriate error."""
        # Create a graph with a Package node
        graph_data = {
            "nodes": [
                {
                    "node_id": 1,
                    "labels": ["Package"],
                    "properties": {"name": "mypackage"},
                },
            ],
            "relationships": [],
            "metadata": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(graph_data, f)
            f.flush()
            extractor = NodeTextExtractor(f.name, temp_repo)
            result = extractor.extract(1)
        Path(f.name).unlink()

        assert result.error is not None
        assert "structural" in result.error.lower() or "no extractable" in result.error.lower()
