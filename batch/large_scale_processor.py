"""
Large Scale Batch Processor for Code Graph RAG

Orchestrates the full pipeline: clone -> process -> upload with rich UI.

Usage:
    uv run python batch/large_scale_processor.py \
        --repo-list repos.json \
        --clone-dir ./clones \
        --output-dir ./output \
        --upload-to gs://bucket/prefix
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from batch.batch_processor import ProcessResult, process_single_repo
from batch.github_cloner import CloneResult, GitHubCloner
from batch.repo_discovery import RepoEntry, RepoList
from batch.rich_ui import BatchProgressUI


def get_optimal_workers() -> int:
    """Get optimal worker count (cpu_count - 2 for headroom)."""
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count - 2)


@dataclass
class LargeScaleConfig:
    """Configuration for large-scale batch processing."""
    repo_list_json: Path
    clone_dir: Path
    output_dir: Path
    workers: int
    max_retries: int = 3
    shallow_clone: bool = True
    upload_to: str | None = None
    resume: bool = True
    skip_clone: bool = False
    limit: int | None = None
    languages: list[str] | None = None
    # Question generation
    generate_questions: bool = False
    questions_dir: Path | None = None
    target_questions_per_repo: int = 10000
    min_questions: int = 10
    questions_only: bool = False  # Skip clone/process, only generate questions
    question_workers: int | None = None  # Workers for question generation (default: auto)
    questions_debug: bool = False  # Show debug stats during question generation
    sparse_fallback: bool = True  # Try sparse mode if regular mode has too few candidates
    questions_verbose: bool = False  # Run question gen sequentially with full output


@dataclass
class BatchSummary:
    """Summary of batch processing run."""
    timestamp: str
    total_repos: int
    cloned: int
    clone_failed: int
    processed: int
    process_failed: int
    total_nodes: int
    total_relationships: int
    total_time_seconds: float
    workers: int
    config: dict

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_repos": self.total_repos,
            "cloned": self.cloned,
            "clone_failed": self.clone_failed,
            "processed": self.processed,
            "process_failed": self.process_failed,
            "total_nodes": self.total_nodes,
            "total_relationships": self.total_relationships,
            "total_time_seconds": self.total_time_seconds,
            "workers": self.workers,
            "config": self.config,
        }


class LargeScaleBatchProcessor:
    """
    Orchestrates large-scale batch processing.

    Pipeline:
    1. Load repo list from JSON
    2. Filter by language (optional)
    3. Clone repos (with state for resume)
    4. Process repos in parallel
    5. Upload results (optional)
    """

    def __init__(self, config: LargeScaleConfig):
        self.config = config
        self.repos: list[RepoEntry] = []
        self.cloner: GitHubCloner | None = None
        self.ui: BatchProgressUI | None = None
        self.process_results: list[ProcessResult] = []

    def load_repos(self) -> list[RepoEntry]:
        """Load and filter repos from JSON."""
        print(f"Loading repos from {self.config.repo_list_json}")
        repo_list = RepoList.load(self.config.repo_list_json)

        repos = repo_list.repos

        # Filter by language
        if self.config.languages:
            langs = set(self.config.languages)
            repos = [r for r in repos if r.primary_language in langs]
            print(f"Filtered to {len(repos)} repos for languages: {', '.join(langs)}")

        # Filter to supported only
        repos = [r for r in repos if r.supported_language]
        print(f"Filtered to {len(repos)} repos with supported languages")

        # Apply limit
        if self.config.limit:
            repos = repos[:self.config.limit]
            print(f"Limited to {len(repos)} repos")

        self.repos = repos
        return repos

    def clone_phase(self) -> list[CloneResult]:
        """Clone all repos (sequential with state persistence)."""
        if not self.repos:
            return []

        self.cloner = GitHubCloner(
            clone_dir=self.config.clone_dir,
            max_retries=self.config.max_retries,
            shallow=self.config.shallow_clone,
        )

        # Load existing state for resume
        if self.config.resume:
            self.cloner.load_state()

        github_urls = [r.github_url for r in self.repos]

        # Get pending URLs
        if self.config.resume:
            pending = self.cloner.get_pending_urls(github_urls)
            print(f"Resuming: {len(github_urls) - len(pending)} already cloned, {len(pending)} remaining")
        else:
            pending = github_urls

        if not pending:
            print("All repos already cloned")
            # Return success results for all
            return [
                CloneResult(
                    github_url=url,
                    local_path=self.cloner.get_local_path(url),
                    success=True,
                    error=None,
                    duration_seconds=0,
                    retry_count=0,
                )
                for url in github_urls
                if self.cloner.is_cloned(url)
            ]

        # Clone with UI callback
        def on_clone_complete(result: CloneResult) -> None:
            if self.ui:
                self.ui.update_clone_progress(result)

        results = self.cloner.clone_repos(
            pending,
            on_complete=on_clone_complete,
            skip_completed=self.config.resume,
        )

        return results

    def process_phase(self) -> list[ProcessResult]:
        """Process all cloned repos in parallel."""
        if not self.cloner:
            self.cloner = GitHubCloner(clone_dir=self.config.clone_dir)

        # Get successfully cloned repos
        cloned_paths: list[Path] = []
        for repo in self.repos:
            local_path = self.cloner.get_local_path(repo.github_url)
            if local_path.exists() and (local_path / ".git").exists():
                cloned_paths.append(local_path)

        if not cloned_paths:
            print("No repos to process")
            return []

        print(f"Processing {len(cloned_paths)} repos with {self.config.workers} workers")

        # Prepare output directory
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Build args list for process pool
        args_list = [(repo_path, self.config.output_dir) for repo_path in cloned_paths]

        results: list[ProcessResult] = []

        with ProcessPoolExecutor(max_workers=self.config.workers) as executor:
            futures = {}
            # Submit all tasks and mark initial batch as in-progress
            for args in args_list:
                future = executor.submit(process_single_repo, args)
                futures[future] = args[0]
                # Mark first N repos as in-progress (up to worker count)
                if self.ui and len(futures) <= self.config.workers:
                    self.ui.mark_repo_started(str(args[0]))

            for future in as_completed(futures):
                repo_path = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = ProcessResult(
                        repo_path=str(repo_path),
                        output_path=None,
                        success=False,
                        error=str(e),
                        duration_seconds=0,
                        node_count=0,
                        relationship_count=0,
                    )

                results.append(result)

                # Update UI - this removes from in-progress
                if self.ui:
                    self.ui.update_process_progress(result)

                    # Find a pending repo to mark as in-progress
                    # (a worker just freed up)
                    completed_paths = {Path(r.repo_path) for r in results}
                    in_progress_names = set(self.ui.stats.in_progress_repos)
                    for args in args_list:
                        if args[0] not in completed_paths:
                            repo_name = args[0].name
                            if repo_name not in in_progress_names:
                                self.ui.mark_repo_started(str(args[0]))
                                break

        self.process_results = results
        return results

    def questions_phase(self) -> list[dict]:
        """Generate questions for all processed repos in parallel."""
        from batch.batch_question_generator import batch_generate_questions

        questions_dir = self.config.questions_dir or (self.config.output_dir.parent / "questions")

        print()
        print("=" * 60)
        print("QUESTION GENERATION")
        print("=" * 60)

        results = batch_generate_questions(
            graphs_dir=self.config.output_dir,
            clones_dir=self.config.clone_dir,
            questions_dir=questions_dir,
            target_per_repo=self.config.target_questions_per_repo,
            min_questions=self.config.min_questions,
            workers=self.config.question_workers,
            debug=self.config.questions_debug,
            sparse_fallback=self.config.sparse_fallback,
            verbose=self.config.questions_verbose,
        )

        return results

    def run_questions_only(self) -> None:
        """Run question generation only on existing graphs."""
        # Validate directories exist
        if not self.config.output_dir.exists():
            print(f"Error: Output directory not found: {self.config.output_dir}")
            sys.exit(1)
        if not self.config.clone_dir.exists():
            print(f"Error: Clone directory not found: {self.config.clone_dir}")
            sys.exit(1)

        print("=" * 60)
        print("QUESTIONS-ONLY MODE")
        print("=" * 60)
        print(f"Graphs dir:   {self.config.output_dir}")
        print(f"Clones dir:   {self.config.clone_dir}")
        print(f"Workers:      {self.config.question_workers or 'auto'}")

        self.questions_phase()

    def upload_phase(self) -> list[str]:
        """Upload results to destination."""
        if not self.config.upload_to:
            return []

        from batch.upload import is_gcs_path, upload_directory

        if self.ui:
            self.ui.set_phase("uploading")

        dest_type = "GCS" if is_gcs_path(self.config.upload_to) else "local"
        print(f"Uploading to {dest_type}: {self.config.upload_to}")

        try:
            uploaded = upload_directory(
                self.config.output_dir,
                self.config.upload_to,
                "*.json",
            )
            print(f"Uploaded {len(uploaded)} files")
            return uploaded
        except Exception as e:
            print(f"Upload error: {e}")
            return []

    def write_summary(self, start_time: float) -> BatchSummary:
        """Write batch summary to output directory."""
        successful = [r for r in self.process_results if r.success]
        failed = [r for r in self.process_results if not r.success]

        clone_results = []
        if self.cloner:
            clone_results = list(self.cloner.state.completed)

        summary = BatchSummary(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_repos=len(self.repos),
            cloned=len(clone_results),
            clone_failed=len(self.cloner.state.failed) if self.cloner else 0,
            processed=len(successful),
            process_failed=len(failed),
            total_nodes=sum(r.node_count for r in successful),
            total_relationships=sum(r.relationship_count for r in successful),
            total_time_seconds=time.time() - start_time,
            workers=self.config.workers,
            config={
                "repo_list": str(self.config.repo_list_json),
                "clone_dir": str(self.config.clone_dir),
                "output_dir": str(self.config.output_dir),
                "languages": self.config.languages,
                "limit": self.config.limit,
            },
        )

        summary_file = self.config.output_dir / "_batch_summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"Summary written to: {summary_file}")

        return summary

    def run(self) -> BatchSummary:
        """Run the full pipeline."""
        # Handle questions-only mode
        if self.config.questions_only:
            self.run_questions_only()
            # Return a minimal summary for questions-only mode
            return BatchSummary(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_repos=0,
                cloned=0,
                clone_failed=0,
                processed=0,
                process_failed=0,
                total_nodes=0,
                total_relationships=0,
                total_time_seconds=0,
                workers=self.config.question_workers or get_optimal_workers(),
                config={"questions_only": True},
            )

        start_time = time.time()

        # Load repos
        self.load_repos()

        if not self.repos:
            print("No repos to process")
            return BatchSummary(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_repos=0,
                cloned=0,
                clone_failed=0,
                processed=0,
                process_failed=0,
                total_nodes=0,
                total_relationships=0,
                total_time_seconds=0,
                workers=self.config.workers,
                config={},
            )

        # Initialize UI
        self.ui = BatchProgressUI(
            total_repos=len(self.repos),
            skip_clone=self.config.skip_clone,
        )
        self.ui.start()

        with self.ui.live_context():
            # Clone phase
            if not self.config.skip_clone:
                self.ui.set_phase("cloning")
                self.clone_phase()

            # Process phase
            self.ui.set_phase("processing")
            self.process_phase()

            # Upload phase
            if self.config.upload_to:
                self.upload_phase()

            self.ui.finish()

        # Question generation phase (outside live UI context for cleaner output)
        if self.config.generate_questions:
            self.questions_phase()

        # Write summary
        summary = self.write_summary(start_time)

        # Print final summary
        self.ui.print_summary()

        return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Large-scale batch processing of GitHub repos"
    )
    parser.add_argument(
        "--repo-list",
        type=Path,
        required=True,
        help="Path to repos.json file",
    )
    parser.add_argument(
        "--clone-dir",
        type=Path,
        required=True,
        help="Directory to clone repos into",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write graph JSON files",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Number of parallel workers (default: cpu_count - 2 = {get_optimal_workers()})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N repos",
    )
    parser.add_argument(
        "--languages",
        type=str,
        default=None,
        help="Comma-separated list of languages to process",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from previous state (default: True)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Don't resume, start fresh",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip cloning, process already-cloned repos",
    )
    parser.add_argument(
        "--upload-to",
        type=str,
        default=None,
        help="Upload results to GCS (gs://bucket/prefix) or local path",
    )
    parser.add_argument(
        "--shallow",
        action="store_true",
        default=True,
        help="Use shallow clones (default: True)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max clone retries per repo (default: 3)",
    )
    # Question generation options
    parser.add_argument(
        "--generate-questions",
        action="store_true",
        help="Generate questions after graph processing",
    )
    parser.add_argument(
        "--questions-only",
        action="store_true",
        help="Skip clone/process, generate questions for existing graphs",
    )
    parser.add_argument(
        "--questions-dir",
        type=Path,
        default=None,
        help="Directory for question JSONL files (default: sibling of output-dir)",
    )
    parser.add_argument(
        "--target-questions",
        type=int,
        default=10000,
        help="Target questions per repo (default: 10000, capped by candidates)",
    )
    parser.add_argument(
        "--min-questions",
        type=int,
        default=10,
        help="Minimum candidates to generate questions (default: 10)",
    )
    parser.add_argument(
        "--question-workers",
        type=int,
        default=None,
        help=f"Workers for question generation (default: cpu_count - 2 = {get_optimal_workers()})",
    )
    parser.add_argument(
        "--questions-debug",
        action="store_true",
        help="Show debug stats during question generation (node counts, relationship counts, rejection reasons)",
    )
    parser.add_argument(
        "--questions-verbose",
        action="store_true",
        help="Run question generation sequentially with full output (disables parallel processing)",
    )
    parser.add_argument(
        "--no-sparse-fallback",
        action="store_true",
        help="Disable sparse mode fallback for repos with few CALLS connections",
    )

    args = parser.parse_args()

    # Validate inputs
    if args.questions_only:
        # Questions-only mode: validate directories exist
        if not args.output_dir.exists():
            print(f"Error: Output directory not found: {args.output_dir}")
            sys.exit(1)
        if not args.clone_dir.exists():
            print(f"Error: Clone directory not found: {args.clone_dir}")
            sys.exit(1)
    else:
        # Normal mode: validate repo list exists
        if not args.repo_list.exists():
            print(f"Error: Repo list not found: {args.repo_list}")
            sys.exit(1)

    # Parse languages
    languages = None
    if args.languages:
        languages = [lang.strip().lower() for lang in args.languages.split(",")]

    # Build config
    config = LargeScaleConfig(
        repo_list_json=args.repo_list,
        clone_dir=args.clone_dir,
        output_dir=args.output_dir,
        workers=args.workers or get_optimal_workers(),
        max_retries=args.max_retries,
        shallow_clone=args.shallow,
        upload_to=args.upload_to,
        resume=not args.no_resume,
        skip_clone=args.skip_clone,
        limit=args.limit,
        languages=languages,
        generate_questions=args.generate_questions,
        questions_dir=args.questions_dir,
        target_questions_per_repo=args.target_questions,
        min_questions=args.min_questions,
        questions_only=args.questions_only,
        question_workers=args.question_workers,
        questions_debug=args.questions_debug,
        sparse_fallback=not args.no_sparse_fallback,
        questions_verbose=args.questions_verbose,
    )

    print("=" * 60)
    if config.questions_only:
        print("Large-Scale Batch Processor (Questions-Only Mode)")
    else:
        print("Large-Scale Batch Processor")
    print("=" * 60)
    print(f"Repo list:   {config.repo_list_json}")
    print(f"Clone dir:   {config.clone_dir}")
    print(f"Output dir:  {config.output_dir}")
    if not config.questions_only:
        print(f"Workers:     {config.workers}")
        print(f"Resume:      {config.resume}")
        print(f"Skip clone:  {config.skip_clone}")
    if config.languages:
        print(f"Languages:   {', '.join(config.languages)}")
    if config.limit:
        print(f"Limit:       {config.limit}")
    if config.generate_questions or config.questions_only:
        print(f"Questions:   {config.target_questions_per_repo}/repo (min {config.min_questions} candidates)")
        print(f"Q. output:   {config.questions_dir or 'auto'}")
        print(f"Q. workers:  {config.question_workers or 'auto'}")
        print(f"Q. debug:    {config.questions_debug}")
        print(f"Q. verbose:  {config.questions_verbose}")
        print(f"Sparse mode: {config.sparse_fallback}")
    print("=" * 60)
    print()

    # Run processor
    processor = LargeScaleBatchProcessor(config)
    summary = processor.run()

    # Exit with error code if any failures
    if summary.process_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
