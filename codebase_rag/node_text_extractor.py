from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from . import constants as cs
from .graph_loader import GraphLoader

if TYPE_CHECKING:
    from .models import GraphNode

# (H) Node types and their extractable content:
#
# CODE NODES (have start_line/end_line -> returns code chunk):
#   - Function: function definitions
#   - Method: class method definitions
#   - Class: class definitions (includes all methods)
#   - Interface, Enum, Type, Union: type definitions
#
# FILE NODES (have path -> returns full file content as code_chunk):
#   - Module: source file (e.g., "module.py")
#   - File: any file (e.g., "config.yaml")
#
# STRUCTURAL NODES (no extractable content -> returns None):
#   - Project: root node
#   - Package: directory with __init__.py
#   - Folder: regular directory
#   - ExternalPackage: external dependency (e.g., "loguru")
#
# RELATIONSHIPS (not nodes, no content):
#   - IMPORTS: Module -> Module
#   - CALLS: Function/Method -> Function/Method
#   - DEFINES: Module -> Function/Class
#   - DEFINES_METHOD: Class -> Method
#   - INHERITS, IMPLEMENTS, OVERRIDES, etc.

LABELS_WITH_LINE_INFO = frozenset(
    {
        cs.NodeLabel.FUNCTION,
        cs.NodeLabel.METHOD,
        cs.NodeLabel.CLASS,
        cs.NodeLabel.INTERFACE,
        cs.NodeLabel.ENUM,
        cs.NodeLabel.TYPE,
        cs.NodeLabel.UNION,
    }
)

LABELS_WITH_PATH = frozenset(
    {
        cs.NodeLabel.MODULE,
        cs.NodeLabel.FILE,
    }
)

LABELS_STRUCTURAL = frozenset(
    {
        cs.NodeLabel.PROJECT,
        cs.NodeLabel.PACKAGE,
        cs.NodeLabel.FOLDER,
        cs.NodeLabel.EXTERNAL_PACKAGE,
    }
)


@dataclass
class NodeChunk:
    file_path: Path
    code_chunk: str


@dataclass
class NodeTextResult:
    node_id: int
    qualified_name: str | None
    file_path: Path | None
    start_line: int | None
    end_line: int | None
    code_chunk: str | None
    file_content: str | None
    error: str | None = None


class NodeTextExtractor:
    def __init__(self, graph_path: str | Path, repo_base_path: str | Path):
        self.graph_loader = GraphLoader(str(graph_path))
        self.repo_base_path = Path(repo_base_path).resolve()
        # File cache to avoid reading the same file multiple times
        self._file_cache: dict[str, str | None] = {}

    def _get_node_category(self, node: GraphNode) -> str:
        labels = set(node.labels)
        if labels & LABELS_WITH_LINE_INFO:
            return "code"
        if labels & LABELS_WITH_PATH:
            return "file"
        if labels & LABELS_STRUCTURAL:
            return "structural"
        return "unknown"

    def _find_module_for_node(self, node: GraphNode) -> GraphNode | None:
        """Find the Module node that ultimately contains this node.

        Handles nested structures by recursively traversing DEFINES relationships
        until reaching a Module (which has LABELS_WITH_PATH like Module or File).
        """
        labels = set(node.labels)

        # If node itself is a file-type node (Module/File), return it
        if labels & LABELS_WITH_PATH:
            return node

        # For Methods: find Class via DEFINES_METHOD, then recurse to find Module
        if cs.NodeLabel.METHOD in labels:
            class_node = self._find_parent_via_relationship(
                node.node_id, cs.RelationshipType.DEFINES_METHOD
            )
            if class_node is None:
                return None
            # Recursively find module for the class (handles nested classes)
            return self._find_module_for_node(class_node)

        # For Functions and Classes: traverse DEFINES chain until reaching Module
        if labels & {cs.NodeLabel.FUNCTION, cs.NodeLabel.CLASS}:
            parent = self._find_parent_via_relationship(
                node.node_id, cs.RelationshipType.DEFINES
            )
            if parent is None:
                return None
            # Recursively find module (handles nested functions/classes)
            return self._find_module_for_node(parent)

        return None

    def _find_parent_via_relationship(
        self, node_id: int, rel_type: str
    ) -> GraphNode | None:
        incoming = self.graph_loader.get_incoming_relationships(node_id)
        for rel in incoming:
            if rel.type == rel_type:
                return self.graph_loader.get_node_by_id(rel.from_id)
        return None

    def _get_file_path(self, module_node: GraphNode) -> Path | None:
        rel_path = module_node.properties.get(cs.KEY_PATH)
        if rel_path is None:
            return None
        return self.repo_base_path / str(rel_path)

    def _read_file(self, file_path: Path) -> str | None:
        # Use cached content if available
        cache_key = str(file_path)
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]

        # Read and cache
        if not file_path.exists():
            logger.warning("File not found: {}", file_path)
            self._file_cache[cache_key] = None
            return None

        content = file_path.read_text(encoding=cs.ENCODING_UTF8)
        self._file_cache[cache_key] = content
        return content

    def _extract_lines(self, content: str, start_line: int, end_line: int) -> str:
        lines = content.splitlines()
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        return "\n".join(lines[start_idx:end_idx])

    def extract(self, node_id: int) -> NodeTextResult:
        self.graph_loader._ensure_loaded()

        node = self.graph_loader.get_node_by_id(node_id)
        if node is None:
            return NodeTextResult(
                node_id=node_id,
                qualified_name=None,
                file_path=None,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error=f"Node with id {node_id} not found",
            )

        qualified_name = node.properties.get(cs.KEY_QUALIFIED_NAME)
        if isinstance(qualified_name, str):
            qn_str: str | None = qualified_name
        else:
            qn_str = None

        category = self._get_node_category(node)

        if category == "structural":
            node_type = node.labels[0] if node.labels else "unknown"
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=None,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error=f"Structural node type '{node_type}' has no extractable content",
            )

        if category == "unknown":
            node_type = node.labels[0] if node.labels else "unknown"
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=None,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error=f"Unknown node type '{node_type}'",
            )

        module_node = self._find_module_for_node(node)
        if module_node is None:
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=None,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error="Could not find module/file for node",
            )

        file_path = self._get_file_path(module_node)
        if file_path is None:
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=None,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error="Module node has no path property",
            )

        file_content = self._read_file(file_path)
        if file_content is None:
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=file_path,
                start_line=None,
                end_line=None,
                code_chunk=None,
                file_content=None,
                error=f"Could not read file: {file_path}",
            )

        if category == "file":
            return NodeTextResult(
                node_id=node_id,
                qualified_name=qn_str,
                file_path=file_path,
                start_line=1,
                end_line=len(file_content.splitlines()),
                code_chunk=file_content,
                file_content=file_content,
            )

        start_line_val = node.properties.get(cs.KEY_START_LINE)
        end_line_val = node.properties.get(cs.KEY_END_LINE)

        start_line = int(start_line_val) if isinstance(start_line_val, int) else None
        end_line = int(end_line_val) if isinstance(end_line_val, int) else None

        code_chunk: str | None = None
        if start_line is not None and end_line is not None:
            code_chunk = self._extract_lines(file_content, start_line, end_line)

        return NodeTextResult(
            node_id=node_id,
            qualified_name=qn_str,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            code_chunk=code_chunk,
            file_content=file_content,
        )

    def extract_batch(self, node_ids: list[int]) -> dict[int, NodeTextResult]:
        return {node_id: self.extract(node_id) for node_id in node_ids}


def extract_node_text(
    graph_path: str | Path,
    repo_base_path: str | Path,
    node_id: int,
) -> NodeTextResult:
    extractor = NodeTextExtractor(graph_path, repo_base_path)
    return extractor.extract(node_id)


def extract_nodes_text(
    graph_path: str | Path,
    repo_base_path: str | Path,
    node_ids: list[int],
) -> dict[int, NodeTextResult]:
    extractor = NodeTextExtractor(graph_path, repo_base_path)
    return extractor.extract_batch(node_ids)


def get_node_chunk(
    graph_path: str | Path,
    repo_base_path: str | Path,
    node_id: int,
) -> NodeChunk | None:
    result = extract_node_text(graph_path, repo_base_path, node_id)
    if result.error or result.file_path is None or result.code_chunk is None:
        return None
    return NodeChunk(file_path=result.file_path, code_chunk=result.code_chunk)


def get_node_chunks(
    graph_path: str | Path,
    repo_base_path: str | Path,
    node_ids: list[int],
) -> dict[int, NodeChunk | None]:
    results = extract_nodes_text(graph_path, repo_base_path, node_ids)
    output: dict[int, NodeChunk | None] = {}
    for node_id, result in sorted(results.items()):
        if result.error or result.file_path is None or result.code_chunk is None:
            output[node_id] = None
        else:
            output[node_id] = NodeChunk(
                file_path=result.file_path, code_chunk=result.code_chunk
            )
    return output


def read_node_ids_from_file(file_path: str | Path) -> list[int]:
    path = Path(file_path)
    node_ids: list[int] = []
    for line in path.read_text(encoding=cs.ENCODING_UTF8).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        node_ids.append(int(stripped))
    return node_ids


def _result_to_jsonl_dict(result: NodeTextResult) -> dict[str, str | int | None]:
    return {
        "node_id": result.node_id,
        "text": result.code_chunk,
        "qualified_name": result.qualified_name,
        "file_path": str(result.file_path) if result.file_path else None,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "error": result.error,
    }


def extract_nodes_to_jsonl(
    graph_path: str | Path,
    repo_base_path: str | Path,
    node_ids: list[int],
    output_path: str | Path | None = None,
) -> str:
    import json

    results = extract_nodes_text(graph_path, repo_base_path, node_ids)
    lines = [
        json.dumps(_result_to_jsonl_dict(results[node_id]), ensure_ascii=False)
        for node_id in node_ids
    ]
    jsonl_content = "\n".join(lines)
    if output_path:
        Path(output_path).write_text(jsonl_content, encoding=cs.ENCODING_UTF8)
    return jsonl_content


def extract_nodes_file_to_jsonl(
    graph_path: str | Path,
    repo_base_path: str | Path,
    input_file: str | Path,
    output_path: str | Path | None = None,
) -> str:
    node_ids = read_node_ids_from_file(input_file)
    return extract_nodes_to_jsonl(graph_path, repo_base_path, node_ids, output_path)


def main() -> None:
    import argparse

    import click

    parser = argparse.ArgumentParser(
        description="Extract source code text for graph nodes"
    )
    parser.add_argument("graph_path", help="Path to exported graph JSON file")
    parser.add_argument("repo_base_path", help="Base path of the repository")
    parser.add_argument(
        "node_ids", nargs="+", type=int, help="Node IDs to extract text for"
    )
    parser.add_argument(
        "--full-file",
        action="store_true",
        help="Print full file content instead of chunk",
    )
    args = parser.parse_args()

    results = extract_nodes_text(args.graph_path, args.repo_base_path, args.node_ids)

    for node_id, result in sorted(results.items()):
        click.echo(f"=== Node {node_id} ===")
        click.echo(f"qualified_name: {result.qualified_name}")
        click.echo(f"file_path: {result.file_path}")
        click.echo(f"lines: {result.start_line}-{result.end_line}")
        if result.error:
            click.echo(f"error: {result.error}")
        else:
            content = result.file_content if args.full_file else result.code_chunk
            click.echo(f"\n{content}\n")


if __name__ == "__main__":
    main()
