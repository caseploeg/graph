"""
Batch Question Generator for Code Graph RAG

Generate diverse questions for multiple repos after graph generation.
Supports parallel processing across repos.

Usage:
    # Standalone usage with parallelism
    uv run python batch/batch_question_generator.py \
        --graphs-dir ./output \
        --clones-dir ./clones \
        --questions-dir ./questions \
        --target-per-repo 10000 \
        --workers 8

    # Or called from large_scale_processor.py with --generate-questions
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Callable

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from codebase_rag.graph_loader import GraphLoader

from generate_diverse_questions import (
    DEFAULT_PROMPT_TIMEOUT,
    DiversePromptRecord,
    generate_diverse_prompts,
)
from question_generator import get_all_candidate_seeds


def count_candidate_seeds(graph_path: Path, min_connections: int = 2) -> int:
    """Count candidate seed nodes in a graph without full generation."""
    try:
        graph = GraphLoader(str(graph_path))
        graph.load()
        candidates = get_all_candidate_seeds(graph, min_connections=min_connections)
        return len(candidates)
    except Exception as e:
        print(f"  Warning: Could not count seeds for {graph_path.name}: {e}")
        return 0


def compute_max_questions(
    num_candidates: int,
    target_per_repo: int = 10000,
    min_questions: int = 10,
) -> int:
    """
    Compute max questions for a repo based on candidate count.

    Heuristic:
    - Each question uses a unique seed node
    - Max questions = number of candidate seeds
    - Target is capped by available seeds
    - Minimum threshold to avoid tiny outputs
    """
    if num_candidates < min_questions:
        return 0  # Skip repos that are too small

    return min(target_per_repo, num_candidates)


def get_repo_path_for_graph(graph_path: Path, clones_dir: Path) -> Path | None:
    """
    Find the cloned repo path for a graph file.

    Graph files are named: {repo_name}.json
    Clone structure: {clones_dir}/{owner}/{repo_name}/
    """
    repo_name = graph_path.stem

    # Search for matching repo in clones directory
    for owner_dir in clones_dir.iterdir():
        if not owner_dir.is_dir():
            continue
        repo_path = owner_dir / repo_name
        if repo_path.exists() and (repo_path / ".git").exists():
            return repo_path

    return None


def generate_questions_for_repo(
    graph_path: Path,
    repo_path: Path,
    output_path: Path,
    target_questions: int = 10000,
    min_questions: int = 10,
    random_seed: int | None = None,
    quiet: bool = False,
    prompt_timeout: int = DEFAULT_PROMPT_TIMEOUT,
) -> dict:
    """
    Generate questions for a single repo.

    Args:
        graph_path: Path to the graph JSON file
        repo_path: Path to the cloned repository
        output_path: Path for output JSONL file
        target_questions: Target number of questions
        min_questions: Minimum candidates required
        random_seed: Optional random seed for reproducibility
        quiet: Suppress verbose output (for parallel workers)
        prompt_timeout: Timeout in seconds per prompt generation

    Returns summary dict with stats.
    """
    repo_name = repo_path.name

    # Count candidates first
    num_candidates = count_candidate_seeds(graph_path)
    max_questions = compute_max_questions(num_candidates, target_questions, min_questions)

    if max_questions == 0:
        return {
            "repo": repo_name,
            "graph": str(graph_path),
            "candidates": num_candidates,
            "generated": 0,
            "skipped": True,
            "reason": f"Too few candidates ({num_candidates} < {min_questions})",
        }

    if not quiet:
        print(f"  Candidates: {num_candidates}, generating up to {max_questions} questions")

    try:
        prompts = generate_diverse_prompts(
            graph_path=graph_path,
            repo_path=repo_path,
            num_prompts=max_questions,
            repo_name=repo_name,
            random_seed=random_seed,
            quiet=quiet,
            prompt_timeout=prompt_timeout,
        )

        # Write to JSONL
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for record in prompts:
                f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

        return {
            "repo": repo_name,
            "graph": str(graph_path),
            "output": str(output_path),
            "candidates": num_candidates,
            "generated": len(prompts),
            "skipped": False,
        }

    except Exception as e:
        return {
            "repo": repo_name,
            "graph": str(graph_path),
            "candidates": num_candidates,
            "generated": 0,
            "skipped": True,
            "reason": str(e),
        }


def generate_questions_worker(args: tuple) -> dict:
    """Worker function for parallel question generation."""
    # Suppress logging in subprocess to avoid interleaved output
    import logging
    from loguru import logger
    logger.remove()
    logger.add(lambda msg: None, level="ERROR")
    logging.getLogger().setLevel(logging.ERROR)

    graph_path, repo_path, output_path, target_questions, min_questions, prompt_timeout = args
    return generate_questions_for_repo(
        graph_path=graph_path,
        repo_path=repo_path,
        output_path=output_path,
        target_questions=target_questions,
        min_questions=min_questions,
        quiet=True,  # Suppress output in worker processes
        prompt_timeout=prompt_timeout,
    )


QuestionCallback = Callable[[dict], None] | None


def get_optimal_workers() -> int:
    """Get optimal worker count (cpu_count - 2 for headroom)."""
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count - 2)


def batch_generate_questions(
    graphs_dir: Path,
    clones_dir: Path,
    questions_dir: Path,
    target_per_repo: int = 10000,
    min_questions: int = 10,
    limit: int | None = None,
    workers: int | None = None,
    on_complete: QuestionCallback = None,
    prompt_timeout: int = DEFAULT_PROMPT_TIMEOUT,
) -> list[dict]:
    """
    Generate questions for all graphs in a directory using parallel processing.

    Args:
        graphs_dir: Directory containing graph JSON files
        clones_dir: Directory containing cloned repos
        questions_dir: Output directory for question JSONL files
        target_per_repo: Target questions per repo (capped by candidates)
        min_questions: Minimum candidates required to generate
        limit: Process only first N graphs
        workers: Number of parallel workers (default: cpu_count - 2)
        on_complete: Callback for each completed repo
        prompt_timeout: Timeout in seconds per prompt generation

    Returns:
        List of result dicts with stats per repo
    """
    # Find all graph files (exclude summary)
    graph_files = sorted([
        f for f in graphs_dir.glob("*.json")
        if f.name != "_batch_summary.json"
    ])

    if limit:
        graph_files = graph_files[:limit]

    workers = workers or get_optimal_workers()

    print(f"Found {len(graph_files)} graphs to process")
    print(f"Target questions per repo: {target_per_repo}")
    print(f"Minimum candidates required: {min_questions}")
    print(f"Workers: {workers}")
    print("-" * 60)

    questions_dir.mkdir(parents=True, exist_ok=True)

    # Build args list for all repos, tracking skipped ones
    args_list = []
    skipped_results: list[dict] = []

    for graph_path in graph_files:
        repo_name = graph_path.stem
        repo_path = get_repo_path_for_graph(graph_path, clones_dir)

        if repo_path is None:
            skipped_results.append({
                "repo": repo_name,
                "graph": str(graph_path),
                "generated": 0,
                "skipped": True,
                "reason": "Repo not found in clones directory",
            })
            continue

        output_path = questions_dir / f"{repo_name}_questions.jsonl"
        args_list.append((graph_path, repo_path, output_path, target_per_repo, min_questions, prompt_timeout))

    print(f"Processing {len(args_list)} repos ({len(skipped_results)} skipped - repo not found)")

    # Process in parallel
    results: list[dict] = list(skipped_results)  # Start with skipped results
    total_generated = 0
    completed = 0
    total_to_process = len(args_list)

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(generate_questions_worker, args): args for args in args_list}

        for future in as_completed(futures):
            args = futures[future]
            repo_name = args[0].stem
            completed += 1

            try:
                result = future.result()
            except Exception as e:
                result = {
                    "repo": repo_name,
                    "graph": str(args[0]),
                    "generated": 0,
                    "skipped": True,
                    "reason": str(e),
                }

            results.append(result)
            total_generated += result.get("generated", 0)

            # Progress output
            status = "OK" if not result.get("skipped") else "SKIP"
            gen_count = result.get("generated", 0)
            print(f"[{completed}/{total_to_process}] {status}: {repo_name} ({gen_count:,} questions)")

            if on_complete:
                on_complete(result)

    print("-" * 60)
    print("SUMMARY")
    print("-" * 60)
    successful = [r for r in results if not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    print(f"Total repos:      {len(results)}")
    print(f"Successful:       {len(successful)}")
    print(f"Skipped:          {len(skipped)}")
    print(f"Total questions:  {total_generated:,}")
    if successful:
        avg = total_generated / len(successful)
        print(f"Avg per repo:     {avg:,.0f}")

    # Write summary
    summary_path = questions_dir / "_questions_summary.json"
    summary = {
        "total_repos": len(results),
        "successful": len(successful),
        "skipped": len(skipped),
        "total_questions": total_generated,
        "target_per_repo": target_per_repo,
        "workers": workers,
        "results": results,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary written to: {summary_path}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate questions for multiple repos in parallel"
    )
    parser.add_argument(
        "--graphs-dir",
        type=Path,
        required=True,
        help="Directory containing graph JSON files",
    )
    parser.add_argument(
        "--clones-dir",
        type=Path,
        required=True,
        help="Directory containing cloned repos",
    )
    parser.add_argument(
        "--questions-dir",
        type=Path,
        required=True,
        help="Output directory for question JSONL files",
    )
    parser.add_argument(
        "--target-per-repo",
        type=int,
        default=10000,
        help="Target questions per repo (default: 10000)",
    )
    parser.add_argument(
        "--min-questions",
        type=int,
        default=10,
        help="Minimum candidates to generate questions (default: 10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N repos",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Number of parallel workers (default: cpu_count - 2 = {get_optimal_workers()})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_PROMPT_TIMEOUT,
        help=f"Timeout in seconds per prompt generation (default: {DEFAULT_PROMPT_TIMEOUT})",
    )

    args = parser.parse_args()

    if not args.graphs_dir.exists():
        print(f"Error: Graphs directory not found: {args.graphs_dir}")
        sys.exit(1)

    if not args.clones_dir.exists():
        print(f"Error: Clones directory not found: {args.clones_dir}")
        sys.exit(1)

    batch_generate_questions(
        graphs_dir=args.graphs_dir,
        clones_dir=args.clones_dir,
        questions_dir=args.questions_dir,
        target_per_repo=args.target_per_repo,
        min_questions=args.min_questions,
        limit=args.limit,
        workers=args.workers,
        prompt_timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
