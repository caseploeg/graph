#!/usr/bin/env python
"""
Generate diverse meta-prompts using multiple strategies.

This script generates diverse prompts by:
1. Rotating through expansion strategies with configurable weights
2. Using unique seeds to avoid repetition
3. Outputting prompts as JSONL with rich metadata

Usage:
    # Generate 10 diverse prompts to stdout (JSONL)
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 10

    # Save prompts to a JSONL file
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 1000 \
        --output prompts.jsonl

    # Customize strategy weights
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --num-prompts 50 \
        --weights "callees:4,chain:3,file:2,callers:1,bfs:1"

    # Override repo name
    uv run python batch/generate_diverse_questions.py \
        --graph batch/test_output/click.json \
        --repo batch/test_repos/click \
        --repo-name "pallets-click" \
        --num-prompts 100 \
        --output prompts.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from batch.questions_rich_ui import GenerationStats

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from codebase_rag.graph_loader import GraphLoader
from codebase_rag.node_text_extractor import NodeTextExtractor

from evaluation_prompt_builder import detect_primary_language
from question_generator import (
    EXPANSION_STRATEGIES,
    META_PROMPT,
    build_context,
    collect_files_from_nodes,
    expand_callee_tree,
    expand_caller_tree,
    expand_chain_with_siblings,
    expand_context,
    expand_file_centric,
    get_all_candidate_seeds,
    sample_seed_node,
)


@dataclass
class DiversePromptRecord:
    """A generated prompt with rich metadata for downstream processing."""

    prompt_id: str
    repo_name: str
    primary_language: str
    expansion_strategy: str
    seed_node_id: int
    seed_node_name: str
    seed_node_qualified_name: str
    context_node_ids: list[int]
    file_paths: list[str]
    prompt_text: str


class GenerationTimeout(Exception):
    """Raised when a single prompt generation exceeds the timeout."""

    pass


@contextmanager
def timeout_context(seconds: int):
    """Context manager for timing out slow operations.

    Uses signal.alarm on Unix systems. On Windows or when signals
    aren't available, the timeout is a no-op.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def handler(signum, frame):
        raise GenerationTimeout(f"Operation timed out after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# Default timeout per prompt generation (seconds)
DEFAULT_PROMPT_TIMEOUT = 30


STRATEGY_WEIGHTS = {
    "callees": 3,
    "chain": 2,
    "file": 2,
    "callers": 2,
    "bfs": 1,
}

# Max times a (seed, strategy) combo can be used (with different random expansions)
MAX_REPEATS_PER_COMBO = 5


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


DEFAULT_MAX_ATTEMPTS_MULTIPLIER = 5


def generate_diverse_prompts(
    graph_path: Path,
    repo_path: Path,
    num_prompts: int,
    repo_name: str | None = None,
    weights: dict[str, int] | None = None,
    max_tokens: int = 8000,
    random_seed: int | None = None,
    quiet: bool = False,
    prompt_timeout: int = DEFAULT_PROMPT_TIMEOUT,
    max_attempts: int | None = None,
) -> tuple[list[DiversePromptRecord], GenerationStats]:
    """Generate prompts with strategy rotation for diversity.

    Args:
        graph_path: Path to the graph JSON file
        repo_path: Path to the repository
        num_prompts: Number of prompts to generate
        repo_name: Optional repo name override
        weights: Strategy weights (default: STRATEGY_WEIGHTS)
        max_tokens: Maximum tokens for context
        random_seed: Optional random seed for reproducibility
        quiet: Suppress verbose output (for parallel workers)
        prompt_timeout: Timeout in seconds per prompt generation (default: 30)
        max_attempts: Maximum total attempts before giving up (default: 5x num_prompts)

    Returns tuple of (prompts, generation_stats) where prompts is list of
    DiversePromptRecord objects and generation_stats contains aggregate metrics.
    """
    from batch.questions_rich_ui import GenerationStats

    start_time = time.time()
    if max_attempts is None:
        max_attempts = num_prompts * DEFAULT_MAX_ATTEMPTS_MULTIPLIER
    if random_seed is not None:
        random.seed(random_seed)
        if not quiet:
            print(f"Using random seed: {random_seed}", file=sys.stderr)

    if weights is None:
        weights = STRATEGY_WEIGHTS.copy()

    # Derive repo_name and detect language once at start
    if repo_name is None:
        # Try to extract owner from repo_path structure (clone_dir/owner/repo)
        # Use owner/repo format for unique prompt IDs
        owner = repo_path.parent.name
        if owner and owner not in (".", "", "test_repos", "clones"):
            repo_name = f"{owner}/{repo_path.name}"
        else:
            repo_name = repo_path.name
    primary_language = detect_primary_language(repo_path)

    if not quiet:
        print(f"Repository: {repo_name}", file=sys.stderr)
        print(f"Primary language: {primary_language}", file=sys.stderr)

    strategy_queue = build_strategy_queue(weights)

    if not quiet:
        print(f"Strategy queue (first 20): {strategy_queue[:20]}", file=sys.stderr)
        print(f"Total weight sum: {sum(weights.values())}", file=sys.stderr)
        print(file=sys.stderr)

    graph = GraphLoader(str(graph_path))
    graph.load()

    all_candidates = get_all_candidate_seeds(graph)
    # Build a dict for O(1) lookup of available candidates
    candidate_dict = {node.node_id: (node, count) for node, count in all_candidates}

    if not quiet:
        print(f"Found {len(all_candidates)} candidate seed nodes", file=sys.stderr)

    if len(all_candidates) < num_prompts and not quiet:
        print(
            f"Warning: Only {len(all_candidates)} candidates for {num_prompts} prompts",
            file=sys.stderr,
        )

    extractor = NodeTextExtractor(graph_path, repo_path)

    prompts: list[DiversePromptRecord] = []
    # Track (seed_id, strategy) combo usage counts - allows repeats up to MAX_REPEATS_PER_COMBO
    # since expansion strategies are now non-deterministic (shuffled)
    combo_counts: dict[tuple[int, str], int] = {}
    strategy_idx = 0
    strategy_counts: Counter[str] = Counter()
    timeout_count = 0
    attempt_count = 0
    unique_strategies = set(weights.keys())
    max_total_combos = len(candidate_dict) * len(unique_strategies) * MAX_REPEATS_PER_COMBO

    if not quiet:
        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("GENERATING PROMPTS", file=sys.stderr)
        print(f"Max repeats per combo: {MAX_REPEATS_PER_COMBO}", file=sys.stderr)
        print(f"Max theoretical capacity: {max_total_combos:,}", file=sys.stderr)
        print(f"Max attempts: {max_attempts:,}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    while len(prompts) < num_prompts and attempt_count < max_attempts:
        attempt_count += 1

        if not strategy_queue:
            strategy_queue = build_strategy_queue(weights)

        strategy = strategy_queue[strategy_idx % len(strategy_queue)]
        strategy_idx += 1

        # Sample from candidates that haven't hit max repeats for this strategy
        available = [
            (n, c) for nid, (n, c) in candidate_dict.items()
            if combo_counts.get((nid, strategy), 0) < MAX_REPEATS_PER_COMBO
        ]
        if not available:
            # Check if we've exhausted all combinations at max repeats
            total_used = sum(combo_counts.values())
            if total_used >= max_total_combos:
                if not quiet:
                    print(f"\nExhausted all combinations at max repeats ({len(prompts)} prompts)", file=sys.stderr)
                break
            # Otherwise, rebuild queue and continue (may find available combos with other strategies)
            strategy_queue = build_strategy_queue(weights)
            continue

        # Weighted random selection
        weights_list = [c for _, c in available]
        seed = random.choices(available, weights=weights_list, k=1)[0][0]

        combo_key = (seed.node_id, strategy)
        combo_counts[combo_key] = combo_counts.get(combo_key, 0) + 1
        seed_name = seed.properties.get("name", f"node_{seed.node_id}")
        seed_qualified_name = seed.properties.get("qualified_name", "")

        if not quiet:
            print(
                f"[{len(prompts) + 1:3d}/{num_prompts}] "
                f"Strategy: {strategy:8s} | Seed: {seed_name}",
                file=sys.stderr,
            )

        # Wrap slow operations in timeout context
        try:
            with timeout_context(prompt_timeout):
                context_nodes = expand_with_strategy(graph, seed.node_id, strategy)
                graph_context, source_context = build_context(
                    graph, extractor, context_nodes, seed.node_id, max_tokens
                )

                if not source_context.strip():
                    if not quiet:
                        print("  -> Skipped (no source)", file=sys.stderr)
                    continue

                prompt_text = META_PROMPT.format(
                    graph_context=graph_context, source_context=source_context
                )

                # Collect file paths from context nodes
                file_paths = sorted(collect_files_from_nodes(graph, context_nodes))

        except GenerationTimeout:
            timeout_count += 1
            if not quiet:
                print(f"  -> Timeout ({prompt_timeout}s), skipping seed", file=sys.stderr)
            continue

        # Build the record with all metadata
        prompt_id = f"{repo_name}_{len(prompts) + 1:04d}"
        record = DiversePromptRecord(
            prompt_id=prompt_id,
            repo_name=repo_name,
            primary_language=primary_language,
            expansion_strategy=strategy,
            seed_node_id=seed.node_id,
            seed_node_name=seed_name,
            seed_node_qualified_name=seed_qualified_name,
            context_node_ids=sorted(context_nodes),
            file_paths=file_paths,
            prompt_text=prompt_text,
        )
        prompts.append(record)
        strategy_counts[strategy] += 1

    if attempt_count >= max_attempts and len(prompts) < num_prompts and not quiet:
        print(f"\nReached max attempts ({max_attempts}), stopping with {len(prompts)} prompts", file=sys.stderr)

    unique_seeds = len(set(combo[0] for combo in combo_counts.keys()))
    unique_combos = len(combo_counts)
    duration_seconds = time.time() - start_time

    if not quiet:
        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("GENERATION COMPLETE", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"Total prompts: {len(prompts)}", file=sys.stderr)
        print(f"Total attempts: {attempt_count}", file=sys.stderr)
        total_combo_uses = sum(combo_counts.values())
        avg_repeats = total_combo_uses / unique_combos if unique_combos > 0 else 0
        print(f"Unique seeds used: {unique_seeds}", file=sys.stderr)
        print(f"Unique (seed, strategy) combos: {unique_combos}", file=sys.stderr)
        print(f"Total combo uses: {total_combo_uses} (avg {avg_repeats:.1f} repeats)", file=sys.stderr)
        if timeout_count > 0:
            print(f"Timeouts: {timeout_count}", file=sys.stderr)
        print(file=sys.stderr)
        print("By strategy:", file=sys.stderr)
        total = sum(strategy_counts.values())
        for strategy in sorted(weights.keys()):
            count = strategy_counts[strategy]
            pct = (count / total * 100) if total > 0 else 0
            print(f"  {strategy:10s}: {count:4d} ({pct:5.1f}%)", file=sys.stderr)
        print(file=sys.stderr)

    gen_stats = GenerationStats(
        timeout_count=timeout_count,
        strategy_counts=dict(strategy_counts),
        attempt_count=attempt_count,
        unique_seeds_used=unique_seeds,
        unique_combos_used=unique_combos,
        duration_seconds=duration_seconds,
    )

    return prompts, gen_stats


def write_prompts_to_jsonl(prompts: list[DiversePromptRecord], output_path: Path) -> None:
    """Write prompt records to a JSONL file."""
    lines = []
    for record in prompts:
        lines.append(json.dumps(asdict(record), ensure_ascii=False))
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(prompts)} prompts to: {output_path}", file=sys.stderr)


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
        "--output",
        type=Path,
        default=None,
        help="Output JSONL file (default: print JSONL to stdout)",
    )
    parser.add_argument(
        "--repo-name",
        type=str,
        default=None,
        help="Repository name for output metadata (default: derived from --repo path)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_PROMPT_TIMEOUT,
        help=f"Timeout in seconds per prompt generation (default: {DEFAULT_PROMPT_TIMEOUT})",
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
            print(
                f"Warning: Could not parse weights '{args.weights}', using defaults",
                file=sys.stderr,
            )
            weights = None

    prompts, gen_stats = generate_diverse_prompts(
        graph_path=args.graph,
        repo_path=args.repo,
        num_prompts=args.num_prompts,
        repo_name=args.repo_name,
        weights=weights,
        max_tokens=args.max_tokens,
        random_seed=args.random_seed,
        prompt_timeout=args.timeout,
    )

    if args.output:
        write_prompts_to_jsonl(prompts, args.output)
    else:
        for record in prompts:
            print(json.dumps(asdict(record), ensure_ascii=False))


if __name__ == "__main__":
    main()
