"""
Batch Processor for Code Graph RAG

Process multiple repositories in parallel, outputting JSON graphs.
No Memgraph required.

Usage:
    python batch/batch_processor.py repos.txt output_dir/ --workers 8

Where repos.txt contains one repository path per line:
    /path/to/repo1
    /path/to/repo2
    ...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BATCH_DIR = Path(__file__).parent
PROJECT_ROOT = BATCH_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class ProcessResult:
    repo_path: str
    output_path: str | None
    success: bool
    error: str | None
    duration_seconds: float
    node_count: int
    relationship_count: int


def process_single_repo(args: tuple[Path, Path]) -> ProcessResult:
    repo_path, output_dir = args
    start_time = time.time()

    try:
        from codebase_rag.graph_updater import GraphUpdater
        from codebase_rag.parser_loader import load_parsers
        from codebase_rag.services import JsonFileIngestor

        output_file = output_dir / f"{repo_path.name}.json"

        ingestor = JsonFileIngestor(str(output_file))
        parsers, queries = load_parsers()
        updater = GraphUpdater(ingestor, repo_path, parsers, queries)
        updater.run()

        with open(output_file) as f:
            data = json.load(f)

        duration = time.time() - start_time
        return ProcessResult(
            repo_path=str(repo_path),
            output_path=str(output_file),
            success=True,
            error=None,
            duration_seconds=duration,
            node_count=data["metadata"]["total_nodes"],
            relationship_count=data["metadata"]["total_relationships"],
        )

    except Exception as e:
        duration = time.time() - start_time
        return ProcessResult(
            repo_path=str(repo_path),
            output_path=None,
            success=False,
            error=str(e),
            duration_seconds=duration,
            node_count=0,
            relationship_count=0,
        )


def batch_process(
    repo_list_file: Path,
    output_dir: Path,
    workers: int = 4,
    limit: int | None = None,
    upload_to: str | None = None,
) -> list[ProcessResult]:
    repos = []
    for line in repo_list_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            repo_path = Path(line)
            if repo_path.exists():
                repos.append(repo_path)
            else:
                print(f"Warning: Repo not found: {line}")

    if limit:
        repos = repos[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(repos)} repositories with {workers} workers")
    print(f"Output directory: {output_dir}")
    print("-" * 60)

    results: list[ProcessResult] = []
    args_list = [(repo, output_dir) for repo in repos]

    start_time = time.time()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_single_repo, args): args[0] for args in args_list
        }

        for i, future in enumerate(as_completed(futures), 1):
            repo_path = futures[future]
            result = future.result()
            results.append(result)

            status = "OK" if result.success else "FAILED"
            print(
                f"[{i}/{len(repos)}] {status}: {repo_path.name} "
                f"({result.duration_seconds:.1f}s)"
            )

            if not result.success:
                print(f"         Error: {result.error}")

    total_time = time.time() - start_time

    print("-" * 60)
    print("SUMMARY")
    print("-" * 60)

    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"Total repos:       {len(results)}")
    print(f"Successful:        {len(successful)}")
    print(f"Failed:            {len(failed)}")
    print(f"Total time:        {total_time:.1f}s")
    if results:
        print(f"Avg time per repo: {total_time / len(results):.1f}s")

    if successful:
        total_nodes = sum(r.node_count for r in successful)
        total_rels = sum(r.relationship_count for r in successful)
        print(f"Total nodes:       {total_nodes:,}")
        print(f"Total relationships: {total_rels:,}")

    summary_file = output_dir / "_batch_summary.json"
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_repos": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "total_time_seconds": total_time,
        "workers": workers,
        "results": [
            {
                "repo": r.repo_path,
                "output": r.output_path,
                "success": r.success,
                "error": r.error,
                "duration_seconds": r.duration_seconds,
                "nodes": r.node_count,
                "relationships": r.relationship_count,
            }
            for r in results
        ],
    }

    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSummary written to: {summary_file}")

    if upload_to:
        from batch.upload import is_gcs_path, upload_directory

        print("")
        print("-" * 60)
        print("UPLOAD")
        print("-" * 60)
        dest_type = "GCS" if is_gcs_path(upload_to) else "local path"
        print(f"Uploading to {dest_type}: {upload_to}")

        try:
            uploaded = upload_directory(output_dir, upload_to, "*.json")
            print(f"Uploaded {len(uploaded)} files:")
            for path in uploaded:
                print(f"  {path}")
        except ImportError as e:
            print(f"Upload failed: {e}")
        except Exception as e:
            print(f"Upload error: {e}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch process repositories to JSON graphs"
    )
    parser.add_argument(
        "repo_list",
        type=Path,
        help="File containing list of repository paths (one per line)",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to write JSON graph files",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N repositories (for testing)",
    )
    parser.add_argument(
        "--upload-to",
        type=str,
        default=None,
        help="Upload JSON files to GCS (gs://bucket/prefix) or local path",
    )

    args = parser.parse_args()

    if not args.repo_list.exists():
        print(f"Error: Repo list file not found: {args.repo_list}")
        sys.exit(1)

    batch_process(
        args.repo_list,
        args.output_dir,
        workers=args.workers,
        limit=args.limit,
        upload_to=args.upload_to,
    )


if __name__ == "__main__":
    main()
