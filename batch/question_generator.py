"""
Question Generator for Code Search Agent Evaluation

Generates hard test questions by sampling graph subsections and prompting an LLM
to create questions that exploit the asymmetry between graph visibility and
text-based search capabilities.

Usage:
    python batch/question_generator.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-questions 10 \
        --output batch/test_output/click_questions.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from codebase_rag.graph_loader import GraphLoader
from codebase_rag.models import GraphNode
from codebase_rag.node_text_extractor import NodeTextExtractor, NodeTextResult

from question_debug_stats import QuestionDebugStats


@dataclass
class GeneratedQuestion:
    repo: str
    question: str
    difficulty: str
    reasoning: str
    expected_search_strategy: str
    seed_node: int
    context_nodes: list[int]
    context_quality: str = "unknown"
    context_quality_reasoning: str = ""


RELATIONSHIP_TYPES_TO_FOLLOW = frozenset(
    {"CALLS", "INHERITS", "DEFINES", "DEFINES_METHOD", "IMPORTS"}
)

CHARS_PER_TOKEN_ESTIMATE = 4


def get_all_candidate_seeds(
    graph: GraphLoader,
    min_connections: int = 3,
    debug_stats: QuestionDebugStats | None = None,
) -> list[tuple[GraphNode, int]]:
    """Get all valid seed candidates, sorted by connection count.

    Args:
        graph: The loaded graph
        min_connections: Minimum CALLS relationships required (in + out)
        debug_stats: Optional stats collector for debugging
    """
    candidates: list[tuple[GraphNode, int]] = []

    for label in ["Function", "Method"]:
        nodes = graph.find_nodes_by_label(label)

        # Track counts for debug
        if debug_stats:
            if label == "Function":
                debug_stats.total_functions = len(nodes)
            else:
                debug_stats.total_methods = len(nodes)

        for node in nodes:
            outgoing = graph.get_outgoing_relationships(node.node_id)
            incoming = graph.get_incoming_relationships(node.node_id)

            calls_out = len([r for r in outgoing if r.type == "CALLS"])
            calls_in = len([r for r in incoming if r.type == "CALLS"])

            connection_count = calls_out + calls_in
            if connection_count >= min_connections:
                candidates.append((node, connection_count))
                if debug_stats:
                    debug_stats.candidates_accepted += 1
            elif debug_stats:
                node_name = node.properties.get("name", f"node_{node.node_id}")
                reason = f"{connection_count} CALLS (need {min_connections}+)"
                debug_stats.add_rejection(
                    node_id=node.node_id,
                    node_name=node_name,
                    node_type=label,
                    reason=reason,
                    connection_count=connection_count,
                )

    candidates.sort(key=lambda x: (-x[1], x[0].node_id))
    return candidates


def get_sparse_candidate_seeds(
    graph: GraphLoader,
    min_connections: int = 1,
    debug_stats: QuestionDebugStats | None = None,
) -> list[tuple[GraphNode, int, str]]:
    """Get candidates using all relationship types for sparse repos.

    Returns tuples of (node, connection_count, best_relationship_type).
    Includes Module and Class nodes in addition to Function/Method.
    """
    candidates: list[tuple[GraphNode, int, str]] = []

    # Function/Method candidates - count all relationships, not just CALLS
    for label in ["Function", "Method"]:
        nodes = graph.find_nodes_by_label(label)
        if debug_stats:
            if label == "Function":
                debug_stats.total_functions = len(nodes)
            else:
                debug_stats.total_methods = len(nodes)

        for node in nodes:
            outgoing = graph.get_outgoing_relationships(node.node_id)
            incoming = graph.get_incoming_relationships(node.node_id)

            # Count multiple relationship types
            calls = len([r for r in outgoing if r.type == "CALLS"]) + \
                    len([r for r in incoming if r.type == "CALLS"])
            defines = len([r for r in incoming if r.type in ("DEFINES", "DEFINES_METHOD")])

            total = calls + defines
            best_type = "CALLS" if calls >= defines else "DEFINES"

            if total >= min_connections:
                candidates.append((node, total, best_type))
                if debug_stats:
                    debug_stats.candidates_accepted += 1
            elif debug_stats:
                node_name = node.properties.get("name", f"node_{node.node_id}")
                debug_stats.add_rejection(
                    node_id=node.node_id,
                    node_name=node_name,
                    node_type=label,
                    reason=f"{total} connections (need {min_connections}+)",
                    connection_count=total,
                )

    # Class candidates - for inheritance questions
    class_nodes = graph.find_nodes_by_label("Class")
    if debug_stats:
        debug_stats.total_classes = len(class_nodes)

    for node in class_nodes:
        outgoing = graph.get_outgoing_relationships(node.node_id)
        incoming = graph.get_incoming_relationships(node.node_id)

        inherits_out = len([r for r in outgoing if r.type == "INHERITS"])
        inherits_in = len([r for r in incoming if r.type == "INHERITS"])
        methods = len([r for r in outgoing if r.type == "DEFINES_METHOD"])

        total = inherits_out + inherits_in + methods
        if total >= min_connections:
            candidates.append((node, total, "INHERITS"))
            if debug_stats:
                debug_stats.candidates_accepted += 1

    # Module candidates - for import/definition questions
    module_nodes = graph.find_nodes_by_label("Module")
    if debug_stats:
        debug_stats.total_modules = len(module_nodes)

    for node in module_nodes:
        outgoing = graph.get_outgoing_relationships(node.node_id)
        incoming = graph.get_incoming_relationships(node.node_id)

        imports_out = len([r for r in outgoing if r.type == "IMPORTS"])
        imports_in = len([r for r in incoming if r.type == "IMPORTS"])
        defines = len([r for r in outgoing if r.type == "DEFINES"])

        total = imports_out + imports_in + defines
        if total >= min_connections:
            candidates.append((node, total, "IMPORTS"))
            if debug_stats:
                debug_stats.candidates_accepted += 1

    candidates.sort(key=lambda x: (-x[1], x[0].node_id))
    return candidates


def sample_seed_node(
    graph: GraphLoader,
    exclude_ids: set[int] | None = None,
    min_connections: int = 3,
) -> GraphNode | None:
    """Sample a seed node, optionally excluding already-used nodes."""
    candidates = get_all_candidate_seeds(graph, min_connections)

    if exclude_ids:
        candidates = [(n, c) for n, c in candidates if n.node_id not in exclude_ids]

    if not candidates:
        all_funcs = graph.find_nodes_by_label("Function") + graph.find_nodes_by_label(
            "Method"
        )
        if exclude_ids:
            all_funcs = [n for n in all_funcs if n.node_id not in exclude_ids]
        if all_funcs:
            return random.choice(all_funcs)
        return None

    weights = [c for _, c in candidates]
    selected = random.choices(candidates, weights=weights, k=1)[0]
    return selected[0]


def expand_context(
    graph: GraphLoader, seed_id: int, max_hops: int = 2, max_nodes: int = 25
) -> set[int]:
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}

    for _ in range(max_hops):
        if len(visited) >= max_nodes:
            break

        next_frontier: set[int] = set()
        for node_id in frontier:
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.type in RELATIONSHIP_TYPES_TO_FOLLOW:
                    next_frontier.add(rel.to_id)

            for rel in graph.get_incoming_relationships(node_id):
                if rel.type in RELATIONSHIP_TYPES_TO_FOLLOW:
                    next_frontier.add(rel.from_id)

        new_nodes = next_frontier - visited
        if len(visited) + len(new_nodes) > max_nodes:
            available_slots = max_nodes - len(visited)
            new_nodes = set(random.sample(list(new_nodes), available_slots))

        visited |= new_nodes
        frontier = new_nodes

    return visited


def get_defining_module(graph: GraphLoader, node_id: int) -> GraphNode | None:
    """Find the module that defines this function/method."""
    incoming = graph.get_incoming_relationships(node_id)

    for rel in incoming:
        if rel.type == "DEFINES":
            return graph.get_node_by_id(rel.from_id)
        if rel.type == "DEFINES_METHOD":
            class_node = graph.get_node_by_id(rel.from_id)
            if class_node:
                class_incoming = graph.get_incoming_relationships(class_node.node_id)
                for crel in class_incoming:
                    if crel.type == "DEFINES":
                        return graph.get_node_by_id(crel.from_id)
    return None


def get_siblings(graph: GraphLoader, node_id: int) -> list[int]:
    """Get sibling functions in the same module."""
    module = get_defining_module(graph, node_id)
    if not module:
        return []

    siblings = []
    for rel in graph.get_outgoing_relationships(module.node_id):
        if rel.type == "DEFINES" and rel.to_id != node_id:
            siblings.append(rel.to_id)
    return siblings


def expand_chain_with_siblings(
    graph: GraphLoader,
    seed_id: int,
    chain_depth: int = 4,
    siblings_per_node: int = 2,
    max_callers: int = 3,
) -> set[int]:
    """Follow call chains deeply, adding siblings at each level."""
    visited: set[int] = {seed_id}

    chain = [seed_id]
    current = seed_id
    for _ in range(chain_depth):
        callees = [
            r.to_id
            for r in graph.get_outgoing_relationships(current)
            if r.type == "CALLS" and r.to_id not in visited
        ]
        if not callees:
            break
        next_node = max(
            callees, key=lambda n: len(graph.get_outgoing_relationships(n))
        )
        chain.append(next_node)
        visited.add(next_node)
        current = next_node

    for node_id in chain:
        siblings = get_siblings(graph, node_id)
        for sib in siblings[:siblings_per_node]:
            visited.add(sib)

    callers = [
        r.from_id
        for r in graph.get_incoming_relationships(seed_id)
        if r.type == "CALLS"
    ]
    for caller in callers[:max_callers]:
        visited.add(caller)

    return visited


def expand_caller_tree(
    graph: GraphLoader, seed_id: int, depth: int = 3, max_nodes: int = 30
) -> set[int]:
    """Focus on who calls this function - upstream focus."""
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}

    for _ in range(depth):
        if len(visited) >= max_nodes:
            break
        next_frontier: set[int] = set()
        for node_id in frontier:
            for rel in graph.get_incoming_relationships(node_id):
                if rel.type == "CALLS" and rel.from_id not in visited:
                    next_frontier.add(rel.from_id)
                    if len(visited) + len(next_frontier) >= max_nodes:
                        break
        visited |= next_frontier
        frontier = next_frontier

    return visited


def expand_callee_tree(
    graph: GraphLoader, seed_id: int, depth: int = 4, max_nodes: int = 30
) -> set[int]:
    """Focus on what this function calls - downstream focus."""
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}

    for _ in range(depth):
        if len(visited) >= max_nodes:
            break
        next_frontier: set[int] = set()
        for node_id in frontier:
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.type == "CALLS" and rel.to_id not in visited:
                    next_frontier.add(rel.to_id)
                    if len(visited) + len(next_frontier) >= max_nodes:
                        break
        visited |= next_frontier
        frontier = next_frontier

    return visited


def expand_file_centric(
    graph: GraphLoader, seed_id: int, max_external: int = 5
) -> set[int]:
    """Get all functions in same file + external dependencies."""
    visited: set[int] = {seed_id}

    module = get_defining_module(graph, seed_id)
    if module:
        for rel in graph.get_outgoing_relationships(module.node_id):
            if rel.type in {"DEFINES", "DEFINES_METHOD"}:
                visited.add(rel.to_id)

    external_calls = []
    for node_id in list(visited):
        for rel in graph.get_outgoing_relationships(node_id):
            if rel.type == "CALLS" and rel.to_id not in visited:
                external_calls.append(rel.to_id)

    for ext in external_calls[:max_external]:
        visited.add(ext)

    return visited


def expand_import_tree(
    graph: GraphLoader, seed_id: int, depth: int = 3, max_nodes: int = 30
) -> set[int]:
    """Expand context following IMPORTS relationships for module-focused questions."""
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}

    for _ in range(depth):
        if len(visited) >= max_nodes:
            break
        next_frontier: set[int] = set()
        for node_id in frontier:
            # Follow both outgoing and incoming IMPORTS
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.type == "IMPORTS" and rel.to_id not in visited:
                    next_frontier.add(rel.to_id)
            for rel in graph.get_incoming_relationships(node_id):
                if rel.type == "IMPORTS" and rel.from_id not in visited:
                    next_frontier.add(rel.from_id)
            # Also include definitions from these modules
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.type == "DEFINES" and rel.to_id not in visited:
                    if len(visited) + len(next_frontier) < max_nodes:
                        next_frontier.add(rel.to_id)

        visited |= next_frontier
        frontier = next_frontier

    return visited


def expand_inheritance_tree(
    graph: GraphLoader, seed_id: int, max_nodes: int = 30
) -> set[int]:
    """Expand context following INHERITS relationships + class methods."""
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}

    # First, traverse inheritance chain (up and down)
    for _ in range(5):  # Max depth
        if len(visited) >= max_nodes // 2:
            break
        next_frontier: set[int] = set()
        for node_id in frontier:
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.type == "INHERITS" and rel.to_id not in visited:
                    next_frontier.add(rel.to_id)
            for rel in graph.get_incoming_relationships(node_id):
                if rel.type == "INHERITS" and rel.from_id not in visited:
                    next_frontier.add(rel.from_id)
        visited |= next_frontier
        frontier = next_frontier
        if not next_frontier:
            break

    # Add methods defined by these classes
    for class_id in list(visited):
        if len(visited) >= max_nodes:
            break
        for rel in graph.get_outgoing_relationships(class_id):
            if rel.type == "DEFINES_METHOD" and len(visited) < max_nodes:
                visited.add(rel.to_id)

    return visited


def expand_definitions(
    graph: GraphLoader, seed_id: int, max_nodes: int = 30
) -> set[int]:
    """Expand to all definitions from a module."""
    visited: set[int] = {seed_id}

    # Get all definitions from this module
    for rel in graph.get_outgoing_relationships(seed_id):
        if rel.type in {"DEFINES", "DEFINES_METHOD"} and len(visited) < max_nodes:
            visited.add(rel.to_id)

    # For each function/class defined, get their calls/methods
    for node_id in list(visited):
        if len(visited) >= max_nodes:
            break
        for rel in graph.get_outgoing_relationships(node_id):
            if rel.type in {"CALLS", "DEFINES_METHOD"} and len(visited) < max_nodes:
                visited.add(rel.to_id)

    return visited


EXPANSION_STRATEGIES = {
    "bfs": expand_context,
    "chain": expand_chain_with_siblings,
    "callers": expand_caller_tree,
    "callees": expand_callee_tree,
    "file": expand_file_centric,
    # Sparse repo strategies
    "imports": expand_import_tree,
    "inheritance": expand_inheritance_tree,
    "definitions": expand_definitions,
}


def format_code_chunk(
    node: GraphNode,
    result: NodeTextResult,
    graph: GraphLoader,
    rel_cache: dict[int, dict] | None = None,
) -> str:
    """Format a code chunk with relationship context.

    Args:
        node: The graph node
        result: Text extraction result
        graph: Graph loader
        rel_cache: Optional pre-computed relationship cache {node_id: {'out': [...], 'in': [...]}}
    """
    # Use cached relationships if available, otherwise fetch
    if rel_cache and node.node_id in rel_cache:
        outgoing = rel_cache[node.node_id]['out']
        incoming = rel_cache[node.node_id]['in']
    else:
        outgoing = graph.get_outgoing_relationships(node.node_id)
        incoming = graph.get_incoming_relationships(node.node_id)

    calls = [r for r in outgoing if r.type == "CALLS"]
    called_by = [r for r in incoming if r.type == "CALLS"]
    inherits = [r for r in outgoing if r.type == "INHERITS"]
    defines = [r for r in incoming if r.type in {"DEFINES", "DEFINES_METHOD"}]

    lines = [
        "<code_chunk>",
        f"  <node_id>{node.node_id}</node_id>",
        f"  <type>{node.labels[0]}</type>",
        f"  <qualified_name>{result.qualified_name}</qualified_name>",
        f"  <file>{result.file_path}</file>",
        f"  <lines>{result.start_line}-{result.end_line}</lines>",
    ]

    if calls:
        call_names = []
        for r in calls:
            target = graph.get_node_by_id(r.to_id)
            if target:
                call_names.append(target.properties.get("qualified_name", target.properties.get("name", "?")))
        if call_names:
            lines.append(f"  <calls>{', '.join(call_names[:5])}</calls>")

    if called_by:
        caller_names = []
        for r in called_by:
            caller = graph.get_node_by_id(r.from_id)
            if caller:
                caller_names.append(caller.properties.get("qualified_name", caller.properties.get("name", "?")))
        if caller_names:
            lines.append(f"  <called_by>{', '.join(caller_names[:5])}</called_by>")

    if inherits:
        parent_names = []
        for r in inherits:
            parent = graph.get_node_by_id(r.to_id)
            if parent:
                parent_names.append(parent.properties.get("name", "?"))
        if parent_names:
            lines.append(f"  <inherits>{', '.join(parent_names)}</inherits>")

    if defines:
        definer = graph.get_node_by_id(defines[0].from_id)
        if definer:
            lines.append(
                f"  <defined_in>{definer.properties.get('qualified_name', definer.properties.get('name', '?'))}</defined_in>"
            )

    decorators = node.properties.get("decorators", [])
    if decorators:
        lines.append(f"  <decorators>{', '.join(decorators)}</decorators>")

    lines.append("  <source>")
    lines.append(result.code_chunk or "")
    lines.append("  </source>")
    lines.append("</code_chunk>")

    return "\n".join(lines)


def build_graph_context(graph: GraphLoader, node_ids: set[int]) -> str:
    """Build a clean, scannable graph structure view."""
    lines = []

    nodes_by_file: dict[str, list[GraphNode]] = {}
    for node_id in node_ids:
        node = graph.get_node_by_id(node_id)
        if node is None:
            continue

        incoming = graph.get_incoming_relationships(node_id)
        defines_rel = [r for r in incoming if r.type in {"DEFINES", "DEFINES_METHOD"}]
        if defines_rel:
            parent = graph.get_node_by_id(defines_rel[0].from_id)
            if parent:
                file_key = parent.properties.get("path", parent.properties.get("qualified_name", "unknown"))
            else:
                file_key = "unknown"
        else:
            file_key = node.properties.get("path", "unknown")

        if file_key not in nodes_by_file:
            nodes_by_file[file_key] = []
        nodes_by_file[file_key].append(node)

    lines.append("## File Structure")
    for file_path, nodes in sorted(nodes_by_file.items()):
        lines.append(f"\n### {file_path}")
        for node in nodes:
            node_type = node.labels[0] if node.labels else "?"
            name = node.properties.get("name", "?")
            qn = node.properties.get("qualified_name", "")
            lines.append(f"  - [{node_type}] {name}")

    lines.append("\n## Call Graph")
    for node_id in sorted(node_ids):
        node = graph.get_node_by_id(node_id)
        if node is None:
            continue
        if node.labels[0] not in {"Function", "Method"}:
            continue

        outgoing = graph.get_outgoing_relationships(node_id)
        calls = [r for r in outgoing if r.type == "CALLS"]
        if not calls:
            continue

        caller_name = node.properties.get("name", "?")
        callee_names = []
        for r in calls:
            callee = graph.get_node_by_id(r.to_id)
            if callee and callee.node_id in node_ids:
                callee_names.append(callee.properties.get("name", "?"))

        if callee_names:
            lines.append(f"  {caller_name} -> {', '.join(callee_names)}")

    lines.append("\n## Inheritance")
    for node_id in sorted(node_ids):
        node = graph.get_node_by_id(node_id)
        if node is None:
            continue
        if node.labels[0] != "Class":
            continue

        outgoing = graph.get_outgoing_relationships(node_id)
        inherits = [r for r in outgoing if r.type == "INHERITS"]
        if not inherits:
            continue

        class_name = node.properties.get("name", "?")
        parent_names = []
        for r in inherits:
            parent = graph.get_node_by_id(r.to_id)
            if parent:
                parent_names.append(parent.properties.get("name", "?"))

        if parent_names:
            lines.append(f"  {class_name} extends {', '.join(parent_names)}")

    return "\n".join(lines)


def get_call_tree(
    graph: GraphLoader,
    node_id: int,
    context_nodes: set[int],
    direction: str,
    depth: int,
    visited: set[int] | None = None,
) -> dict[str, dict]:
    """Build nested dict of call relationships for ASCII tree."""
    if visited is None:
        visited = set()
    if node_id in visited or depth <= 0:
        return {}

    visited.add(node_id)
    tree: dict[str, dict] = {}

    if direction == "out":
        rels = [r for r in graph.get_outgoing_relationships(node_id) if r.type == "CALLS"]
        children = [r.to_id for r in rels if r.to_id in context_nodes]
    else:
        rels = [r for r in graph.get_incoming_relationships(node_id) if r.type == "CALLS"]
        children = [r.from_id for r in rels if r.from_id in context_nodes]

    for child_id in children[:4]:
        child = graph.get_node_by_id(child_id)
        if child:
            name = child.properties.get("name", "?")
            tree[name] = get_call_tree(
                graph, child_id, context_nodes, direction, depth - 1, visited.copy()
            )

    return tree


def format_tree(tree: dict[str, dict], prefix: str = "") -> list[str]:
    """Format nested dict as ASCII tree lines."""
    lines = []
    items = list(tree.items())
    for i, (name, children) in enumerate(items):
        is_last_item = i == len(items) - 1
        connector = "└── " if is_last_item else "├── "
        lines.append(f"{prefix}{connector}{name}")

        if children:
            child_prefix = prefix + ("    " if is_last_item else "│   ")
            lines.extend(format_tree(children, child_prefix))

    return lines


def collect_files_from_nodes(graph: GraphLoader, node_ids: set[int]) -> set[str]:
    """Collect unique file paths from context nodes."""
    files: set[str] = set()
    for node_id in node_ids:
        node = graph.get_node_by_id(node_id)
        if node is None:
            continue

        incoming = graph.get_incoming_relationships(node_id)
        for rel in incoming:
            if rel.type in {"DEFINES", "DEFINES_METHOD"}:
                parent = graph.get_node_by_id(rel.from_id)
                if parent:
                    path = parent.properties.get("path")
                    if path:
                        files.add(str(path))
                    if rel.type == "DEFINES_METHOD":
                        class_incoming = graph.get_incoming_relationships(parent.node_id)
                        for crel in class_incoming:
                            if crel.type == "DEFINES":
                                module = graph.get_node_by_id(crel.from_id)
                                if module:
                                    mod_path = module.properties.get("path")
                                    if mod_path:
                                        files.add(str(mod_path))

    return files


def build_ascii_graph(
    graph: GraphLoader,
    seed_id: int,
    context_nodes: set[int],
) -> str:
    """Build ASCII tree visualization of the graph structure."""
    lines = ["=== GRAPH STRUCTURE ===", ""]

    seed = graph.get_node_by_id(seed_id)
    if seed is None:
        return ""

    seed_name = seed.properties.get("name", "?")
    seed_type = seed.labels[0] if seed.labels else "?"
    lines.append(f"Seed: {seed_name} ({seed_type})")
    lines.append("│")

    callees = get_call_tree(graph, seed_id, context_nodes, direction="out", depth=3)
    if callees:
        lines.append("├─► CALLS (downstream)")
        lines.extend(format_tree(callees, prefix="│   "))
        lines.append("│")

    callers = get_call_tree(graph, seed_id, context_nodes, direction="in", depth=2)
    if callers:
        lines.append("├─◄ CALLED BY (upstream)")
        lines.extend(format_tree(callers, prefix="│   "))
        lines.append("│")

    siblings = get_siblings(graph, seed_id)
    sibling_nodes = [s for s in siblings if s in context_nodes]
    if sibling_nodes:
        lines.append("└── SIBLINGS (same file)")
        for i, sib_id in enumerate(sibling_nodes[:5]):
            sib = graph.get_node_by_id(sib_id)
            if sib:
                is_last = i == min(len(sibling_nodes), 5) - 1
                prefix = "    └── " if is_last else "    ├── "
                lines.append(f"{prefix}{sib.properties.get('name', '?')}")

    files = collect_files_from_nodes(graph, context_nodes)
    if files:
        lines.append("")
        lines.append("Files involved:")
        for f in sorted(files)[:8]:
            lines.append(f"  - {f}")
        if len(files) > 8:
            lines.append(f"  ... and {len(files) - 8} more")

    return "\n".join(lines)


def compute_distances_from_seed(
    graph: GraphLoader, seed_id: int, node_ids: set[int]
) -> dict[int, int]:
    """BFS to compute hop distance from seed node."""
    distances = {seed_id: 0}
    frontier = {seed_id}
    distance = 0

    while frontier and distance < 10:
        distance += 1
        next_frontier: set[int] = set()
        for node_id in frontier:
            for rel in graph.get_outgoing_relationships(node_id):
                if rel.to_id in node_ids and rel.to_id not in distances:
                    distances[rel.to_id] = distance
                    next_frontier.add(rel.to_id)
            for rel in graph.get_incoming_relationships(node_id):
                if rel.from_id in node_ids and rel.from_id not in distances:
                    distances[rel.from_id] = distance
                    next_frontier.add(rel.from_id)
        frontier = next_frontier

    return distances


def build_source_context(
    graph: GraphLoader,
    extractor: NodeTextExtractor,
    node_ids: set[int],
    seed_id: int,
    max_chars: int,
) -> str:
    """Build source code chunks with priority ordering by distance from seed."""
    distances = compute_distances_from_seed(graph, seed_id, node_ids)

    prioritized = sorted(node_ids, key=lambda nid: (distances.get(nid, 999), nid))

    # Pre-compute relationship cache for all context nodes to avoid redundant fetches
    rel_cache: dict[int, dict] = {}
    for node_id in node_ids:
        rel_cache[node_id] = {
            'out': graph.get_outgoing_relationships(node_id),
            'in': graph.get_incoming_relationships(node_id),
        }

    chunks = []
    total_chars = 0
    skipped_nodes: list[str] = []

    for node_id in prioritized:
        node = graph.get_node_by_id(node_id)
        if node is None:
            continue

        result = extractor.extract(node_id)
        if result.error or not result.code_chunk:
            continue

        chunk = format_code_chunk(node, result, graph, rel_cache=rel_cache)
        chunk_len = len(chunk)

        if total_chars + chunk_len > max_chars:
            skipped_nodes.append(node.properties.get("name", f"node_{node_id}"))
            continue

        chunks.append(chunk)
        total_chars += chunk_len

    if skipped_nodes:
        omitted_list = ", ".join(skipped_nodes[:5])
        suffix = "..." if len(skipped_nodes) > 5 else ""
        chunks.append(f"<!-- {len(skipped_nodes)} nodes omitted: {omitted_list}{suffix} -->")

    return "\n\n".join(chunks)


def build_context(
    graph: GraphLoader,
    extractor: NodeTextExtractor,
    node_ids: set[int],
    seed_id: int,
    max_tokens: int = 8000,
) -> tuple[str, str]:
    """Build both graph context and source context."""
    ascii_graph = build_ascii_graph(graph, seed_id, node_ids)
    struct_graph = build_graph_context(graph, node_ids)

    graph_ctx = ascii_graph + "\n\n" + struct_graph

    graph_tokens = len(graph_ctx) // CHARS_PER_TOKEN_ESTIMATE
    remaining_tokens = max_tokens - graph_tokens - 500
    max_source_chars = remaining_tokens * CHARS_PER_TOKEN_ESTIMATE

    source_ctx = build_source_context(graph, extractor, node_ids, seed_id, max_source_chars)

    return graph_ctx, source_ctx


META_PROMPT = """You are a test question writer for evaluating code search expert agents.

You are shown a subsection of a larger codebase. First, review the graph structure to understand how the code is organized and connected. Then examine the source code for implementation details.

<graph_context>
{graph_context}
</graph_context>

<source_code>
{source_context}
</source_code>

## STEP 1: Assess Context Quality

Before writing a question, evaluate the quality of the context you've been given.

**EXCELLENT context has:**
- Multiple source files involved (3+)
- Deep call chains visible (seed → A → B → C)
- Both upstream (callers) and downstream (callees) relationships
- Substantial implementation code (not just test assertions)
- Error handling, edge cases, or complex logic visible

**GOOD context has:**
- At least 2 files involved
- Some relationship depth (at least 2 levels)
- Mix of implementation and usage

**POOR context has:**
- Only 1-2 small code chunks
- Seed function is trivial (< 5 lines)
- Mostly test code without implementation details
- No meaningful relationships visible
- Only shows leaf functions with no callees

**Calibration examples:**
- A 100-line function that calls 5+ other functions across 3 files = EXCELLENT
- A 3-line method that just raises an exception = POOR
- Test functions showing assertions but not the code being tested = POOR
- Core processing function with type conversion, validation, error handling = EXCELLENT

## STEP 2: Generate Question (or Skip)

If context is POOR, you should indicate this rather than forcing a low-quality question.

IMPORTANT CONSTRAINTS:
- The agent being tested starts with ONLY access to the root directory
- They cannot see the graph structure or this specific subsection you're viewing
- They must discover the answer using: glob patterns, grep searches, and file reading
- The agent CANNOT run the code or use a debugger

QUESTION DIFFICULTY GUIDELINES:

Generate questions that are HARD because they require:
1. **Multi-file navigation**: Answer spans multiple files that aren't obviously related by name
2. **Relationship discovery**: Understanding call chains, inheritance, or imports
3. **Pattern recognition**: Finding implementations of a concept without obvious keywords
4. **Semantic understanding**: Understanding what code does, not just where it is

AVOID easy questions that can be solved by:
- Simple grep for a function name mentioned in the question
- Looking in an obviously-named file
- Reading a single file

QUESTION FORMAT:
Write a natural question a developer would ask, as if they're trying to understand or modify this part of the codebase. Don't reveal graph relationships directly in the question.

BAD EXAMPLES (too easy):
- "What functions are called by process_data()?" (reveals call graph structure)
- "What file contains the AuthService class?" (simple grep)
- "What does the validate() function do?" (just read one function)

GOOD EXAMPLES (require investigation):
- "How does the data processing pipeline handle malformed input?"
- "What validation occurs before a user can access protected routes?"
- "How are errors propagated from the database layer to the API response?"

OUTPUT FORMAT (respond with valid JSON only):
{{
  "context_quality": "excellent|good|poor",
  "context_quality_reasoning": "<brief explanation of why you rated the context this way>",
  "question": "<the question a developer would naturally ask, or null if context is poor>",
  "difficulty": "hard",
  "reasoning": "<why this requires multi-step search - what files/concepts must be connected>",
  "expected_search_strategy": "<step-by-step how an expert would find the answer using grep/glob/read>"
}}"""


def call_llm(prompt: str) -> str:
    from openai import OpenAI

    base_url = os.getenv("QUESTION_GEN_BASE_URL")
    api_key = os.getenv("QUESTION_GEN_API_KEY")
    model = os.getenv("QUESTION_GEN_MODEL", "gpt-4o")

    if not base_url or not api_key:
        raise ValueError(
            "QUESTION_GEN_BASE_URL and QUESTION_GEN_API_KEY environment variables required"
        )

    client = OpenAI(base_url=base_url, api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    return response.choices[0].message.content or ""


def parse_llm_response(response: str) -> dict:
    response = response.strip()
    if response.startswith("```json"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]

    return json.loads(response.strip())


def generate_question(
    graph: GraphLoader,
    extractor: NodeTextExtractor,
    repo_name: str,
    max_hops: int = 2,
    max_nodes: int = 25,
    max_tokens: int = 8000,
    strategy: str = "bfs",
    exclude_ids: set[int] | None = None,
) -> tuple[GeneratedQuestion | None, int | None]:
    """Generate a question. Returns (question, seed_node_id) tuple."""
    seed = sample_seed_node(graph, exclude_ids=exclude_ids)
    if seed is None:
        return None, None

    expand_fn = EXPANSION_STRATEGIES.get(strategy, expand_context)
    if strategy == "bfs":
        context_nodes = expand_fn(graph, seed.node_id, max_hops, max_nodes)
    elif strategy == "chain":
        context_nodes = expand_fn(graph, seed.node_id)
    elif strategy in ("callers", "callees"):
        context_nodes = expand_fn(graph, seed.node_id, max_nodes=max_nodes)
    elif strategy == "file":
        context_nodes = expand_fn(graph, seed.node_id)
    else:
        context_nodes = expand_fn(graph, seed.node_id, max_hops, max_nodes)

    graph_context, source_context = build_context(graph, extractor, context_nodes, seed.node_id, max_tokens)

    if not source_context.strip():
        return None, seed.node_id

    prompt = META_PROMPT.format(graph_context=graph_context, source_context=source_context)
    response = call_llm(prompt)

    try:
        parsed = parse_llm_response(response)
    except json.JSONDecodeError:
        print(f"Failed to parse LLM response: {response[:200]}...")
        return None, seed.node_id

    question = GeneratedQuestion(
        repo=repo_name,
        question=parsed.get("question") or "",
        difficulty=parsed.get("difficulty", "hard"),
        reasoning=parsed.get("reasoning", ""),
        expected_search_strategy=parsed.get("expected_search_strategy", ""),
        seed_node=seed.node_id,
        context_nodes=list(context_nodes),
        context_quality=parsed.get("context_quality", "unknown"),
        context_quality_reasoning=parsed.get("context_quality_reasoning", ""),
    )
    return question, seed.node_id


def generate_questions(
    graph_path: Path,
    repo_path: Path,
    num_questions: int = 10,
    max_hops: int = 2,
    max_nodes: int = 25,
    max_tokens: int = 8000,
    strategy: str = "bfs",
    unique_seeds: bool = False,
    random_seed: int | None = None,
) -> list[GeneratedQuestion]:
    if random_seed is not None:
        random.seed(random_seed)
        print(f"Using random seed: {random_seed}")

    graph = GraphLoader(str(graph_path))
    graph.load()

    extractor = NodeTextExtractor(graph_path, repo_path)
    repo_name = repo_path.name

    all_candidates = get_all_candidate_seeds(graph)
    print(f"Found {len(all_candidates)} candidate seed nodes")

    questions: list[GeneratedQuestion] = []
    used_seeds: set[int] = set()
    attempts = 0
    max_attempts = num_questions * 3

    while len(questions) < num_questions and attempts < max_attempts:
        attempts += 1
        print(f"Generating question {len(questions) + 1}/{num_questions} (attempt {attempts})...")

        exclude = used_seeds if unique_seeds else None

        try:
            question, seed_id = generate_question(
                graph, extractor, repo_name, max_hops, max_nodes, max_tokens,
                strategy=strategy, exclude_ids=exclude,
            )

            if seed_id is not None:
                used_seeds.add(seed_id)

            if question and question.question:
                questions.append(question)
                print(f"  Generated: {question.question[:80]}...")
            elif seed_id is None:
                print("  No more unique seed candidates available")
                break
        except Exception as e:
            print(f"  Error: {e}")

    if unique_seeds:
        print(f"Used {len(used_seeds)} unique seeds")

    return questions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate test questions for code search agent evaluation"
    )
    parser.add_argument(
        "--graph", type=Path, required=True, help="Path to exported graph JSON file"
    )
    parser.add_argument(
        "--repo", type=Path, required=True, help="Path to the repository"
    )
    parser.add_argument(
        "--num-questions",
        type=int,
        default=10,
        help="Number of questions to generate (default: 10)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=2,
        help="Maximum hops for context expansion (default: 2)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=25,
        help="Maximum nodes in context (default: 25)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Maximum tokens for context (default: 8000)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="bfs",
        choices=["bfs", "chain", "callers", "callees", "file"],
        help="Graph expansion strategy (default: bfs)",
    )
    parser.add_argument(
        "--unique-seeds",
        action="store_true",
        help="Ensure each question uses a unique seed node",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL file (default: stdout)",
    )

    args = parser.parse_args()

    if not args.graph.exists():
        print(f"Error: Graph file not found: {args.graph}")
        sys.exit(1)

    if not args.repo.exists():
        print(f"Error: Repository not found: {args.repo}")
        sys.exit(1)

    questions = generate_questions(
        args.graph,
        args.repo,
        args.num_questions,
        args.max_hops,
        args.max_nodes,
        args.max_tokens,
        strategy=args.strategy,
        unique_seeds=args.unique_seeds,
        random_seed=args.random_seed,
    )

    output_lines = []
    for q in questions:
        output_lines.append(
            json.dumps(
                {
                    "repo": q.repo,
                    "question": q.question,
                    "difficulty": q.difficulty,
                    "reasoning": q.reasoning,
                    "expected_search_strategy": q.expected_search_strategy,
                    "seed_node": q.seed_node,
                    "context_nodes": q.context_nodes,
                },
                ensure_ascii=False,
            )
        )

    output_content = "\n".join(output_lines)

    if args.output:
        args.output.write_text(output_content, encoding="utf-8")
        print(f"\nWrote {len(questions)} questions to: {args.output}")
    else:
        print("\n" + "=" * 60)
        print("GENERATED QUESTIONS")
        print("=" * 60)
        print(output_content)


if __name__ == "__main__":
    main()
