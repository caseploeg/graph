#!/usr/bin/env python
"""
Preview the hydrated meta-prompt for question generation.

Outputs the full prompt that would be sent to the LLM, so you can test it manually.

Usage:
    python batch/preview_prompt.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click

    # Save to file
    python batch/preview_prompt.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --output prompt.txt

    # Control context size
    python batch/preview_prompt.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --max-hops 3 \
        --max-nodes 30 \
        --max-tokens 6000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from question_generator import (
    CHARS_PER_TOKEN_ESTIMATE,
    EXPANSION_STRATEGIES,
    META_PROMPT,
    build_context,
    expand_context,
    sample_seed_node,
)

from codebase_rag.graph_loader import GraphLoader
from codebase_rag.node_text_extractor import NodeTextExtractor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview the hydrated meta-prompt for question generation"
    )
    parser.add_argument(
        "--graph", type=Path, required=True, help="Path to exported graph JSON file"
    )
    parser.add_argument(
        "--repo", type=Path, required=True, help="Path to the repository"
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
        "--seed-node",
        type=int,
        default=None,
        help="Specific node ID to use as seed (default: random)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file (default: stdout)",
    )

    args = parser.parse_args()

    if not args.graph.exists():
        print(f"Error: Graph file not found: {args.graph}", file=sys.stderr)
        sys.exit(1)

    if not args.repo.exists():
        print(f"Error: Repository not found: {args.repo}", file=sys.stderr)
        sys.exit(1)

    print("Loading graph...", file=sys.stderr)
    graph = GraphLoader(str(args.graph))
    graph.load()

    extractor = NodeTextExtractor(args.graph, args.repo)

    if args.seed_node is not None:
        seed = graph.get_node_by_id(args.seed_node)
        if seed is None:
            print(f"Error: Node ID {args.seed_node} not found", file=sys.stderr)
            sys.exit(1)
        print(f"Using specified seed node: {args.seed_node}", file=sys.stderr)
    else:
        seed = sample_seed_node(graph)
        if seed is None:
            print("Error: Could not find a suitable seed node", file=sys.stderr)
            sys.exit(1)
        print(f"Sampled seed node: {seed.node_id}", file=sys.stderr)

    seed_name = seed.properties.get("qualified_name", seed.properties.get("name", "?"))
    print(f"Seed: {seed_name}", file=sys.stderr)

    print(f"Expanding context (strategy={args.strategy}, max_hops={args.max_hops}, max_nodes={args.max_nodes})...", file=sys.stderr)

    expand_fn = EXPANSION_STRATEGIES.get(args.strategy, expand_context)
    if args.strategy == "bfs":
        context_nodes = expand_fn(graph, seed.node_id, args.max_hops, args.max_nodes)
    elif args.strategy == "chain":
        context_nodes = expand_fn(graph, seed.node_id)
    elif args.strategy in ("callers", "callees"):
        context_nodes = expand_fn(graph, seed.node_id, max_nodes=args.max_nodes)
    elif args.strategy == "file":
        context_nodes = expand_fn(graph, seed.node_id)
    else:
        context_nodes = expand_fn(graph, seed.node_id, args.max_hops, args.max_nodes)

    print(f"Context includes {len(context_nodes)} nodes", file=sys.stderr)

    print(f"Building context (max_tokens={args.max_tokens})...", file=sys.stderr)
    graph_context, source_context = build_context(graph, extractor, context_nodes, seed.node_id, args.max_tokens)

    prompt = META_PROMPT.format(graph_context=graph_context, source_context=source_context)

    estimated_tokens = len(prompt) // CHARS_PER_TOKEN_ESTIMATE

    if args.output:
        args.output.write_text(prompt, encoding="utf-8")
        print(f"\nWrote prompt to: {args.output}", file=sys.stderr)
        print(f"Prompt length: {len(prompt)} chars (~{estimated_tokens} tokens)", file=sys.stderr)
    else:
        print("\n" + "=" * 80, file=sys.stderr)
        print(f"Estimated tokens: ~{estimated_tokens}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print(prompt)


if __name__ == "__main__":
    main()
