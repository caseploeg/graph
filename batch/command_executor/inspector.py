from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Iterator

from .schemas import CommandResult, InspectorSummary


class ResultsInspector:
    def __init__(self, results_file: Path):
        self.results_file = results_file
        self._results: list[CommandResult] | None = None

    def _load_results(self) -> list[CommandResult]:
        if self._results is not None:
            return self._results

        results: list[CommandResult] = []
        with open(self.results_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(CommandResult.from_json(line))
        self._results = results
        return results

    def _iter_results(self) -> Iterator[CommandResult]:
        with open(self.results_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield CommandResult.from_json(line)

    def sample(self, n: int = 5) -> list[CommandResult]:
        results = self._load_results()
        if len(results) <= n:
            return results
        return random.sample(results, n)

    def filter_by_repo(self, repo: str) -> list[CommandResult]:
        return [r for r in self._iter_results() if repo in r.repo]

    def filter_by_cmd_pattern(self, pattern: str) -> list[CommandResult]:
        regex = re.compile(pattern)
        return [r for r in self._iter_results() if regex.search(r.cmd)]

    def filter_by_status(self, status: str) -> list[CommandResult]:
        if status == "success":
            return [r for r in self._iter_results() if r.return_code == 0]
        elif status == "failed":
            return [r for r in self._iter_results() if r.return_code != 0]
        elif status == "cached":
            return [r for r in self._iter_results() if r.cached]
        else:
            return []

    def get_by_index(self, idx: int) -> CommandResult | None:
        results = self._load_results()
        if 0 <= idx < len(results):
            return results[idx]
        return None

    def summary(self) -> InspectorSummary:
        results = self._load_results()

        if not results:
            return InspectorSummary(
                total=0,
                success=0,
                failed=0,
                cached=0,
                unique_repos=0,
                unique_commands=0,
                avg_duration_ms=0.0,
                slowest_commands=[],
            )

        success = sum(1 for r in results if r.return_code == 0)
        failed = sum(1 for r in results if r.return_code != 0)
        cached = sum(1 for r in results if r.cached)
        unique_repos = len({r.repo for r in results})
        unique_commands = len({r.cmd for r in results})

        exec_times = [r.duration_ms for r in results if not r.cached]
        avg_duration = sum(exec_times) / len(exec_times) if exec_times else 0.0

        non_cached = [r for r in results if not r.cached]
        slowest = sorted(non_cached, key=lambda r: r.duration_ms, reverse=True)[:5]

        return InspectorSummary(
            total=len(results),
            success=success,
            failed=failed,
            cached=cached,
            unique_repos=unique_repos,
            unique_commands=unique_commands,
            avg_duration_ms=avg_duration,
            slowest_commands=slowest,
        )


def format_result(result: CommandResult, index: int | None = None) -> str:
    lines: list[str] = []

    header = f"Result #{index}" if index is not None else "Result"
    cached_str = "yes" if result.cached else "no"
    lines.append("=" * 70)
    lines.append(
        f"{header} (cached: {cached_str}, return_code: {result.return_code}, "
        f"duration: {result.duration_ms:.1f}ms)"
    )
    lines.append("=" * 70)
    lines.append(f"Repo: {result.repo}")
    lines.append(f"Command: {result.cmd}")
    lines.append("")
    lines.append("-" * 10 + " stdout " + "-" * 52)

    if result.stdout:
        stdout_lines = result.stdout.split("\n")
        if len(stdout_lines) > 20:
            lines.extend(stdout_lines[:20])
            lines.append(f"... ({len(stdout_lines) - 20} more lines)")
        else:
            lines.append(result.stdout)
    else:
        lines.append("(empty)")

    lines.append("")
    lines.append("-" * 10 + " stderr " + "-" * 52)
    if result.stderr:
        lines.append(result.stderr)
    else:
        lines.append("(empty)")

    lines.append("=" * 70)
    return "\n".join(lines)


def format_summary(summary: InspectorSummary) -> str:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("RESULTS SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Total commands:    {summary.total:,}")
    lines.append(f"Successful:        {summary.success:,}")
    lines.append(f"Failed:            {summary.failed:,}")
    lines.append(f"Cached:            {summary.cached:,}")
    lines.append(f"Unique repos:      {summary.unique_repos:,}")
    lines.append(f"Unique commands:   {summary.unique_commands:,}")
    lines.append(f"Avg duration:      {summary.avg_duration_ms:.2f}ms")

    if summary.slowest_commands:
        lines.append("")
        lines.append("-" * 70)
        lines.append("SLOWEST COMMANDS (top 5)")
        lines.append("-" * 70)
        for i, r in enumerate(summary.slowest_commands, 1):
            lines.append(f"{i}. {r.duration_ms:.1f}ms - {r.cmd[:60]}")

    lines.append("=" * 70)
    return "\n".join(lines)
