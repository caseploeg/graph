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
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from generate_diverse_questions import (
    DEFAULT_MAX_ATTEMPTS_MULTIPLIER,
    DEFAULT_PROMPT_TIMEOUT,
    generate_diverse_prompts,
)
from question_debug_stats import QuestionDebugStats
from question_generator import get_all_candidate_seeds, get_sparse_candidate_seeds

from codebase_rag.graph_loader import GraphLoader

if TYPE_CHECKING:
    from batch.questions_rich_ui import QuestionsProgressUI


def count_candidate_seeds(
    graph_path: Path,
    min_connections: int = 1,
    debug: bool = False,
    sparse_mode: bool = False,
) -> int | tuple[int, QuestionDebugStats | None]:
    """Count candidate seed nodes in a graph without full generation.

    Args:
        graph_path: Path to the graph JSON file
        min_connections: Minimum connections required
        debug: If True, return (count, debug_stats) tuple
        sparse_mode: If True, use sparse candidate selection (more relationship types)

    Returns:
        int if debug=False, tuple[int, QuestionDebugStats] if debug=True
    """
    try:
        graph = GraphLoader(str(graph_path))
        graph.load()

        debug_stats = None
        if debug:
            debug_stats = QuestionDebugStats()
            # Populate graph stats from summary
            summary = graph.summary()
            debug_stats.node_counts = dict(summary.node_labels)
            debug_stats.relationship_counts = dict(summary.relationship_types)

        if sparse_mode:
            candidates = get_sparse_candidate_seeds(
                graph, min_connections=min_connections, debug_stats=debug_stats
            )
        else:
            candidates = get_all_candidate_seeds(
                graph, min_connections=min_connections, debug_stats=debug_stats
            )

        if debug:
            return len(candidates), debug_stats
        return len(candidates)
    except Exception as e:
        print(f"  Warning: Could not count seeds for {graph_path.name}: {e}")
        if debug:
            return 0, None
        return 0


def compute_max_questions(
    num_candidates: int,
    target_per_repo: int = 10000,
    min_questions: int = 10,
) -> int:
    """
    Compute max questions for a repo based on candidate count.

    Returns target_per_repo if repo has enough candidates.
    The generation loop handles exhaustion naturally when all
    (seed, strategy) combos are used up.
    """
    if num_candidates < min_questions:
        return 0  # Skip repos that are too small
    return target_per_repo


def get_repo_path_for_graph(graph_path: Path, clones_dir: Path) -> Path | None:
    """
    Find the cloned repo path for a graph file.

    Graph files are named: {owner}__{repo_name}.json (new format)
    or {repo_name}.json (old format for backwards compatibility)

    Clone structure: {clones_dir}/{owner}/{repo_name}/
    """
    graph_stem = graph_path.stem

    # New format: owner__repo
    if "__" in graph_stem:
        owner, repo_name = graph_stem.split("__", 1)
        repo_path = clones_dir / owner / repo_name
        if repo_path.exists() and (repo_path / ".git").exists():
            return repo_path
        return None

    # Old format (backwards compatibility): search all owners for repo_name
    repo_name = graph_stem
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
    debug: bool = False,
    sparse_fallback: bool = True,
    max_attempts: int | None = None,
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
        debug: Show detailed debug statistics
        sparse_fallback: Try sparse mode if regular mode has too few candidates
        max_attempts: Maximum attempts before giving up (default: 5x target)

    Returns summary dict with stats.
    """
    # Use owner/repo format for unique repo identification
    owner = repo_path.parent.name
    repo_name = f"{owner}/{repo_path.name}"
    sparse_mode_used = False

    # Count candidates first (with debug stats if requested)
    if debug:
        num_candidates, debug_stats = count_candidate_seeds(graph_path, debug=True)
        if debug_stats and not quiet:
            print(f"\n=== DEBUG: {repo_name} ===")
            print(debug_stats.format_summary(verbose=False))
    else:
        num_candidates = count_candidate_seeds(graph_path)

    max_questions = compute_max_questions(num_candidates, target_questions, min_questions)

    # Try sparse mode if regular mode produces too few candidates
    if max_questions == 0 and sparse_fallback:
        if debug:
            sparse_candidates, sparse_stats = count_candidate_seeds(
                graph_path, min_connections=1, debug=True, sparse_mode=True
            )
            if sparse_stats and not quiet:
                print(f"\n=== DEBUG (sparse mode): {repo_name} ===")
                print(sparse_stats.format_summary(verbose=False))
        else:
            sparse_candidates = count_candidate_seeds(
                graph_path, min_connections=1, sparse_mode=True
            )

        sparse_max = compute_max_questions(sparse_candidates, target_questions, min_questions)
        if sparse_max > 0:
            num_candidates = sparse_candidates
            max_questions = sparse_max
            sparse_mode_used = True
            if not quiet:
                print(f"  Using sparse mode: {num_candidates} candidates")

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
        prompts, gen_stats = generate_diverse_prompts(
            graph_path=graph_path,
            repo_path=repo_path,
            num_prompts=max_questions,
            repo_name=repo_name,
            random_seed=random_seed,
            quiet=quiet,
            prompt_timeout=prompt_timeout,
            max_attempts=max_attempts,
        )

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
            "sparse_mode": sparse_mode_used,
            "gen_stats": gen_stats.to_dict(),
        }

    except Exception as e:
        return {
            "repo": repo_name,
            "graph": str(graph_path),
            "candidates": num_candidates,
            "generated": 0,
            "skipped": True,
            "reason": str(e),
            "sparse_mode": sparse_mode_used,
        }


def generate_questions_worker(args: tuple) -> dict:
    """Worker function for parallel question generation."""
    # Suppress logging in subprocess to avoid interleaved output
    import logging

    from loguru import logger
    logger.remove()
    logger.add(lambda msg: None, level="ERROR")
    logging.getLogger().setLevel(logging.ERROR)

    graph_path, repo_path, output_path, target_questions, min_questions, prompt_timeout, sparse_fallback, max_attempts = args
    return generate_questions_for_repo(
        graph_path=graph_path,
        repo_path=repo_path,
        output_path=output_path,
        target_questions=target_questions,
        min_questions=min_questions,
        quiet=True,  # Suppress output in worker processes
        prompt_timeout=prompt_timeout,
        sparse_fallback=sparse_fallback,
        max_attempts=max_attempts,
    )


QuestionCallback = Callable[[dict], None] | None


def get_optimal_workers() -> int:
    """Get optimal worker count (cpu_count - 2 for headroom)."""
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count - 2)


DEFAULT_REPO_TIMEOUT = 600


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
    debug: bool = False,
    sparse_fallback: bool = True,
    verbose: bool = False,
    max_attempts: int | None = None,
    repo_timeout: int = DEFAULT_REPO_TIMEOUT,
    ui: QuestionsProgressUI | None = None,
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
        debug: Show detailed debug statistics per repo
        sparse_fallback: Try sparse mode if regular mode has too few candidates
        verbose: Run sequentially with full output (disables parallel processing)
        max_attempts: Maximum attempts per repo before giving up (default: 5x target)
        repo_timeout: Hard time limit per repo in seconds (default: 600)
        ui: Optional Rich UI for progress display (None for text output)

    Returns:
        List of result dicts with stats per repo
    """
    from batch.questions_rich_ui import GenerationStats

    graph_files = sorted([
        f for f in graphs_dir.glob("*.json")
        if f.name != "_batch_summary.json"
    ])

    if limit:
        graph_files = graph_files[:limit]

    if verbose:
        workers = 1
    else:
        workers = workers or get_optimal_workers()

    if not ui:
        effective_max_attempts = max_attempts if max_attempts else f"{target_per_repo * DEFAULT_MAX_ATTEMPTS_MULTIPLIER} (5x target)"
        print(f"Found {len(graph_files)} graphs to process")
        print(f"Target questions per repo: {target_per_repo}")
        print(f"Minimum candidates required: {min_questions}")
        print(f"Max attempts per repo: {effective_max_attempts}")
        print(f"Repo timeout: {repo_timeout}s")
        print(f"Sparse fallback: {sparse_fallback}")
        print(f"Debug mode: {debug}")
        print(f"Verbose mode: {verbose}")
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
        args_list.append((graph_path, repo_path, output_path, target_per_repo, min_questions, prompt_timeout, sparse_fallback, max_attempts))

    if not ui:
        print(f"Processing {len(args_list)} repos ({len(skipped_results)} skipped - repo not found)")

    results: list[dict] = list(skipped_results)  # Start with skipped results
    total_generated = 0
    completed = 0
    total_to_process = len(args_list)

    # Create/truncate the combined questions file for incremental writes
    all_questions_path = questions_dir / "all_questions.jsonl"
    all_questions_file = open(all_questions_path, "w")
    combined_count = 0

    def append_to_combined(result: dict) -> int:
        """Append a repo's questions to the combined file. Returns count added."""
        nonlocal combined_count
        output_file = result.get("output")
        if output_file and Path(output_file).exists():
            with open(output_file) as in_f:
                for line in in_f:
                    all_questions_file.write(line)
                    combined_count += 1
            all_questions_file.flush()
        return combined_count

    try:
        if verbose:
            # Sequential processing with full output
            for args in args_list:
                graph_path, repo_path, output_path, target_q, min_q, timeout, sparse_fb, max_att = args
                repo_name = graph_path.stem
                completed += 1

                print(f"\n{'='*60}")
                print(f"[{completed}/{total_to_process}] Processing: {repo_name}")
                print(f"{'='*60}")

                try:
                    result = generate_questions_for_repo(
                        graph_path=graph_path,
                        repo_path=repo_path,
                        output_path=output_path,
                        target_questions=target_q,
                        min_questions=min_q,
                        quiet=False,  # Full output in verbose mode
                        prompt_timeout=timeout,
                        debug=debug,
                        sparse_fallback=sparse_fb,
                        max_attempts=max_att,
                    )
                except Exception as e:
                    result = {
                        "repo": repo_name,
                        "graph": str(graph_path),
                        "generated": 0,
                        "skipped": True,
                        "reason": str(e),
                    }

                results.append(result)
                total_generated += result.get("generated", 0)

                if not result.get("skipped"):
                    append_to_combined(result)

                if on_complete:
                    on_complete(result)
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for i, args in enumerate(args_list):
                    future = executor.submit(generate_questions_worker, args)
                    futures[future] = args
                    if ui and i < workers:
                        repo_name = args[0].stem
                        if "__" in repo_name:
                            owner, name = repo_name.split("__", 1)
                            repo_name = f"{owner}/{name}"
                        ui.mark_repo_started(repo_name)

                for future in as_completed(futures):
                    args = futures[future]
                    repo_name = args[0].stem
                    completed += 1

                    try:
                        result = future.result(timeout=repo_timeout)
                    except FuturesTimeoutError:
                        result = {
                            "repo": repo_name,
                            "graph": str(args[0]),
                            "generated": 0,
                            "skipped": True,
                            "reason": f"Timeout after {repo_timeout}s",
                        }
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

                    if not result.get("skipped"):
                        append_to_combined(result)

                    gen_stats = None
                    gen_stats_dict = result.get("gen_stats")
                    if gen_stats_dict:
                        gen_stats = GenerationStats.from_dict(gen_stats_dict)

                    if ui:
                        ui.update_repo_complete(result, gen_stats)
                        completed_paths = {r.get("graph") for r in results}
                        in_progress_names = set(ui.stats.in_progress_repos)
                        for pending_args in args_list:
                            if str(pending_args[0]) not in completed_paths:
                                pending_name = pending_args[0].stem
                                if "__" in pending_name:
                                    owner, name = pending_name.split("__", 1)
                                    pending_name = f"{owner}/{name}"
                                if pending_name not in in_progress_names:
                                    ui.mark_repo_started(pending_name)
                                    break
                    else:
                        status = "OK" if not result.get("skipped") else "SKIP"
                        gen_count = result.get("generated", 0)
                        sparse_tag = " [sparse]" if result.get("sparse_mode") else ""
                        timeout_tag = " [TIMEOUT]" if "Timeout" in result.get("reason", "") else ""
                        print(f"[{completed}/{total_to_process}] {status}: {repo_name} ({gen_count:,} questions){sparse_tag}{timeout_tag}")

                    if on_complete:
                        on_complete(result)
    finally:
        all_questions_file.close()

    successful = [r for r in results if not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    sparse_used = [r for r in successful if r.get("sparse_mode")]

    if not ui:
        print("-" * 60)
        print("SUMMARY")
        print("-" * 60)
        print(f"Total repos:      {len(results)}")
        print(f"Successful:       {len(successful)}")
        print(f"  - Regular mode: {len(successful) - len(sparse_used)}")
        print(f"  - Sparse mode:  {len(sparse_used)}")
        print(f"Skipped:          {len(skipped)}")
        print(f"Total questions:  {total_generated:,}")
        if successful:
            avg = total_generated / len(successful)
            print(f"Avg per repo:     {avg:,.0f}")
        print(f"\nCombined file:    {all_questions_path} ({combined_count:,} questions)")

    summary_path = questions_dir / "_questions_summary.json"
    summary = {
        "total_repos": len(results),
        "successful": len(successful),
        "sparse_mode_used": len(sparse_used),
        "skipped": len(skipped),
        "total_questions": total_generated,
        "combined_file": str(all_questions_path),
        "combined_count": combined_count,
        "target_per_repo": target_per_repo,
        "sparse_fallback": sparse_fallback,
        "workers": workers,
        "results": results,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    if not ui:
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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show detailed debug stats (node counts, relationship counts, rejection reasons)",
    )
    parser.add_argument(
        "--no-sparse-fallback",
        action="store_true",
        help="Disable sparse mode fallback for repos with few CALLS connections",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Run sequentially with full output (disables parallel processing)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help=f"Max attempts per repo before giving up (default: {DEFAULT_MAX_ATTEMPTS_MULTIPLIER}x target)",
    )
    parser.add_argument(
        "--repo-timeout",
        type=int,
        default=DEFAULT_REPO_TIMEOUT,
        help=f"Hard time limit per repo in seconds (default: {DEFAULT_REPO_TIMEOUT})",
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
        debug=args.debug,
        sparse_fallback=not args.no_sparse_fallback,
        verbose=args.verbose,
        max_attempts=args.max_attempts,
        repo_timeout=args.repo_timeout,
    )


if __name__ == "__main__":
    main()
