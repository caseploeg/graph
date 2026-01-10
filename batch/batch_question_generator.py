"""
Batch Question Generator for Code Graph RAG

Generate diverse questions for multiple repos after graph generation.

Usage:
    # Standalone usage
    uv run python batch/batch_question_generator.py \
        --graphs-dir ./output \
        --clones-dir ./clones \
        --questions-dir ./questions \
        --target-per-repo 10000

    # Or called from large_scale_processor.py with --generate-questions
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from codebase_rag.graph_loader import GraphLoader

from generate_diverse_questions import (
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
) -> dict:
    """
    Generate questions for a single repo.

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

    print(f"  Candidates: {num_candidates}, generating up to {max_questions} questions")

    try:
        prompts = generate_diverse_prompts(
            graph_path=graph_path,
            repo_path=repo_path,
            num_prompts=max_questions,
            repo_name=repo_name,
            random_seed=random_seed,
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


QuestionCallback = Callable[[dict], None] | None


def batch_generate_questions(
    graphs_dir: Path,
    clones_dir: Path,
    questions_dir: Path,
    target_per_repo: int = 10000,
    min_questions: int = 10,
    limit: int | None = None,
    on_complete: QuestionCallback = None,
) -> list[dict]:
    """
    Generate questions for all graphs in a directory.

    Args:
        graphs_dir: Directory containing graph JSON files
        clones_dir: Directory containing cloned repos
        questions_dir: Output directory for question JSONL files
        target_per_repo: Target questions per repo (capped by candidates)
        min_questions: Minimum candidates required to generate
        limit: Process only first N graphs
        on_complete: Callback for each completed repo

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

    print(f"Found {len(graph_files)} graphs to process")
    print(f"Target questions per repo: {target_per_repo}")
    print(f"Minimum candidates required: {min_questions}")
    print("-" * 60)

    questions_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    total_generated = 0

    for i, graph_path in enumerate(graph_files, 1):
        repo_name = graph_path.stem
        print(f"[{i}/{len(graph_files)}] {repo_name}")

        # Find corresponding repo
        repo_path = get_repo_path_for_graph(graph_path, clones_dir)
        if repo_path is None:
            result = {
                "repo": repo_name,
                "graph": str(graph_path),
                "generated": 0,
                "skipped": True,
                "reason": "Repo not found in clones directory",
            }
            results.append(result)
            print(f"  Skipped: repo not found")
            continue

        output_path = questions_dir / f"{repo_name}_questions.jsonl"

        result = generate_questions_for_repo(
            graph_path=graph_path,
            repo_path=repo_path,
            output_path=output_path,
            target_questions=target_per_repo,
            min_questions=min_questions,
        )

        results.append(result)
        total_generated += result.get("generated", 0)

        if result.get("skipped"):
            print(f"  Skipped: {result.get('reason', 'unknown')}")
        else:
            print(f"  Generated: {result['generated']} questions")

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
        "results": results,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary written to: {summary_path}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate questions for multiple repos"
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
    )


if __name__ == "__main__":
    main()
