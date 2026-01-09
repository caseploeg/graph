#!/usr/bin/env python
"""
Generate diverse meta-prompts using multiple strategies.

This showcase script generates diverse prompts by:
1. Rotating through expansion strategies with configurable weights
2. Using unique seeds to avoid repetition
3. Outputting prompts for manual review or batch processing

Usage:
    # Generate 10 diverse prompts to stdout
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 10

    # Save prompts to a directory
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 20 \
        --output-dir prompts/

    # Customize strategy weights
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 50 \
        --weights "callees:4,chain:3,file:2,callers:1,bfs:1"
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from codebase_rag.graph_loader import GraphLoader
from codebase_rag.node_text_extractor import NodeTextExtractor

from question_generator import (
    EXPANSION_STRATEGIES,
    META_PROMPT,
    build_context,
    expand_callee_tree,
    expand_caller_tree,
    expand_chain_with_siblings,
    expand_context,
    expand_file_centric,
    get_all_candidate_seeds,
    sample_seed_node,
)


STRATEGY_WEIGHTS = {
    "callees": 3,
    "chain": 2,
    "file": 2,
    "callers": 2,
    "bfs": 1,
}


def parse_weights(weights_str: str) -> dict[str, int]:
    """Parse weights string like 'callees:4,chain:3,file:2'."""
    weights = {}
    for part in weights_str.split(","):
        if ":" not in part:
            continue
        name, weight = part.strip().split(":")
        name = name.strip()
        if name in EXPANSION_STRATEGIES:
            weights[name] = int(weight.strip())
    return weights


def build_strategy_queue(weights: dict[str, int]) -> list[str]:
    """Build a shuffled queue of strategies based on weights."""
    queue = []
    for strategy, weight in weights.items():
        queue.extend([strategy] * weight)
    random.shuffle(queue)
    return queue


def expand_with_strategy(
    graph: GraphLoader, seed_id: int, strategy: str, max_nodes: int = 30
) -> set[int]:
    """Expand context using the specified strategy."""
    if strategy == "bfs":
        return expand_context(graph, seed_id, max_hops=2, max_nodes=max_nodes)
    elif strategy == "chain":
        return expand_chain_with_siblings(graph, seed_id)
    elif strategy == "callers":
        return expand_caller_tree(graph, seed_id, max_nodes=max_nodes)
    elif strategy == "callees":
        return expand_callee_tree(graph, seed_id, max_nodes=max_nodes)
    elif strategy == "file":
        return expand_file_centric(graph, seed_id)
    else:
        return expand_context(graph, seed_id, max_hops=2, max_nodes=max_nodes)


def generate_diverse_prompts(
    graph_path: Path,
    repo_path: Path,
    num_prompts: int,
    output_dir: Path | None = None,
    weights: dict[str, int] | None = None,
    max_tokens: int = 8000,
    random_seed: int | None = None,
) -> list[tuple[str, str, int, str]]:
    """Generate prompts with strategy rotation for diversity.

    Returns list of (prompt, strategy, seed_id, seed_name) tuples.
    """
    if random_seed is not None:
        random.seed(random_seed)
        print(f"Using random seed: {random_seed}", file=sys.stderr)

    if weights is None:
        weights = STRATEGY_WEIGHTS.copy()

    strategy_queue = build_strategy_queue(weights)
    print(f"Strategy queue (first 20): {strategy_queue[:20]}", file=sys.stderr)
    print(f"Total weight sum: {sum(weights.values())}", file=sys.stderr)
    print(file=sys.stderr)

    graph = GraphLoader(str(graph_path))
    graph.load()

    all_candidates = get_all_candidate_seeds(graph)
    print(f"Found {len(all_candidates)} candidate seed nodes", file=sys.stderr)

    if len(all_candidates) < num_prompts:
        print(
            f"Warning: Only {len(all_candidates)} candidates for {num_prompts} prompts",
            file=sys.stderr,
        )

    extractor = NodeTextExtractor(graph_path, repo_path)

    prompts: list[tuple[str, str, int, str]] = []
    used_seeds: set[int] = set()
    strategy_idx = 0
    strategy_counts: Counter[str] = Counter()

    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("GENERATING PROMPTS", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    while len(prompts) < num_prompts:
        if not strategy_queue:
            strategy_queue = build_strategy_queue(weights)

        strategy = strategy_queue[strategy_idx % len(strategy_queue)]
        strategy_idx += 1

        seed = sample_seed_node(graph, exclude_ids=used_seeds)
        if seed is None:
            print(f"\nExhausted seed pool at prompt {len(prompts)}", file=sys.stderr)
            break

        used_seeds.add(seed.node_id)
        seed_name = seed.properties.get("name", f"node_{seed.node_id}")

        print(
            f"[{len(prompts) + 1:3d}/{num_prompts}] "
            f"Strategy: {strategy:8s} | Seed: {seed_name}",
            file=sys.stderr,
        )

        context_nodes = expand_with_strategy(graph, seed.node_id, strategy)
        graph_context, source_context = build_context(
            graph, extractor, context_nodes, seed.node_id, max_tokens
        )

        if not source_context.strip():
            print("  -> Skipped (no source)", file=sys.stderr)
            continue

        prompt = META_PROMPT.format(
            graph_context=graph_context, source_context=source_context
        )
        prompts.append((prompt, strategy, seed.node_id, seed_name))
        strategy_counts[strategy] += 1

    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("GENERATION COMPLETE", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Total prompts: {len(prompts)}", file=sys.stderr)
    print(f"Unique seeds used: {len(used_seeds)}", file=sys.stderr)
    print(file=sys.stderr)
    print("By strategy:", file=sys.stderr)
    total = sum(strategy_counts.values())
    for strategy in sorted(weights.keys()):
        count = strategy_counts[strategy]
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {strategy:10s}: {count:3d} ({pct:5.1f}%)", file=sys.stderr)
    print(file=sys.stderr)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        for i, (prompt, strategy, seed_id, seed_name) in enumerate(prompts):
            filename = f"{i+1:03d}_{strategy}_{seed_name}.txt"
            filepath = output_dir / filename
            filepath.write_text(prompt, encoding="utf-8")
        print(f"Wrote {len(prompts)} prompts to: {output_dir}/", file=sys.stderr)

    return prompts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate diverse meta-prompts using multiple expansion strategies"
    )
    parser.add_argument(
        "--graph", type=Path, required=True, help="Path to exported graph JSON file"
    )
    parser.add_argument(
        "--repo", type=Path, required=True, help="Path to the repository"
    )
    parser.add_argument(
        "--num-prompts",
        type=int,
        default=10,
        help="Number of prompts to generate (default: 10)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Maximum tokens for context (default: 8000)",
    )
    parser.add_argument(
        "--weights",
        type=str,
        default=None,
        help="Strategy weights like 'callees:4,chain:3,file:2,callers:1,bfs:1'",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for prompt files (default: print to stdout)",
    )

    args = parser.parse_args()

    if not args.graph.exists():
        print(f"Error: Graph file not found: {args.graph}", file=sys.stderr)
        sys.exit(1)

    if not args.repo.exists():
        print(f"Error: Repository not found: {args.repo}", file=sys.stderr)
        sys.exit(1)

    weights = None
    if args.weights:
        weights = parse_weights(args.weights)
        if not weights:
            print(f"Warning: Could not parse weights '{args.weights}', using defaults", file=sys.stderr)
            weights = None

    prompts = generate_diverse_prompts(
        graph_path=args.graph,
        repo_path=args.repo,
        num_prompts=args.num_prompts,
        output_dir=args.output_dir,
        weights=weights,
        max_tokens=args.max_tokens,
        random_seed=args.random_seed,
    )

    if not args.output_dir:
        for i, (prompt, strategy, seed_id, seed_name) in enumerate(prompts):
            print("=" * 80)
            print(f"PROMPT {i+1}: strategy={strategy}, seed={seed_name} (id={seed_id})")
            print("=" * 80)
            print(prompt)
            print()


if __name__ == "__main__":
    main()
