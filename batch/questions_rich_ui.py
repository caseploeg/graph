"""
Rich Terminal UI for Question Generation Phase

Provides a live dashboard with progress bars, aggregate stats, and activity log
for the questions generation phase of batch processing.

Usage:
    from batch.questions_rich_ui import QuestionsProgressUI, QuestionsLogExporter

    log_exporter = QuestionsLogExporter(questions_dir)
    ui = QuestionsProgressUI(total_repos=100, log_exporter=log_exporter)
    ui.start(config={"target_per_repo": 10000})

    with ui.live_context():
        for result in process_repos():
            ui.update_repo_complete(result, gen_stats)

    log_exporter.finish(ui.stats)
    ui.print_summary()
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text


@dataclass
class GenerationStats:
    """Statistics from a single repo's question generation."""

    timeout_count: int = 0
    strategy_counts: dict[str, int] = field(default_factory=dict)
    attempt_count: int = 0
    unique_seeds_used: int = 0
    unique_combos_used: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timeout_count": self.timeout_count,
            "strategy_counts": dict(self.strategy_counts),
            "attempt_count": self.attempt_count,
            "unique_seeds_used": self.unique_seeds_used,
            "unique_combos_used": self.unique_combos_used,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GenerationStats:
        return cls(
            timeout_count=d.get("timeout_count", 0),
            strategy_counts=d.get("strategy_counts", {}),
            attempt_count=d.get("attempt_count", 0),
            unique_seeds_used=d.get("unique_seeds_used", 0),
            unique_combos_used=d.get("unique_combos_used", 0),
            duration_seconds=d.get("duration_seconds", 0.0),
        )


@dataclass
class QuestionsStats:
    """Aggregate statistics for batch question generation."""

    total_repos: int = 0
    completed_repos: int = 0
    skipped_repos: int = 0
    sparse_mode_repos: int = 0
    total_questions: int = 0
    total_timeouts: int = 0
    total_attempts: int = 0
    strategy_counts: Counter = field(default_factory=Counter)
    start_time: float = field(default_factory=time.time)
    in_progress_repos: list[str] = field(default_factory=list)
    current_repo: str = ""

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def repos_per_minute(self) -> float:
        elapsed_min = self.elapsed_seconds / 60
        if elapsed_min < 0.1:
            return 0
        return self.completed_repos / elapsed_min

    @property
    def questions_per_minute(self) -> float:
        elapsed_min = self.elapsed_seconds / 60
        if elapsed_min < 0.1:
            return 0
        return self.total_questions / elapsed_min

    @property
    def avg_questions_per_repo(self) -> float:
        successful = self.completed_repos - self.skipped_repos
        if successful < 1:
            return 0
        return self.total_questions / successful

    @property
    def eta_seconds(self) -> float | None:
        if self.completed_repos == 0:
            return None
        rate = self.completed_repos / self.elapsed_seconds
        remaining = self.total_repos - self.completed_repos
        if rate < 0.001:
            return None
        return remaining / rate

    def merge_generation_stats(self, gen_stats: GenerationStats) -> None:
        """Merge per-repo stats into aggregates."""
        self.total_timeouts += gen_stats.timeout_count
        self.total_attempts += gen_stats.attempt_count
        for strategy, count in gen_stats.strategy_counts.items():
            self.strategy_counts[strategy] += count


@dataclass
class QuestionsActivityLogEntry:
    """Entry in the questions activity log."""

    timestamp: float
    repo_name: str
    success: bool
    message: str
    questions_generated: int = 0
    timeouts: int = 0
    duration: float = 0
    sparse_mode: bool = False


class QuestionsLogExporter:
    """Exports question generation events to JSONL and plain text files."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.jsonl_path = output_dir / "_questions_log.jsonl"
        self.txt_path = output_dir / "_questions_log.txt"
        self._jsonl_file: TextIO | None = None
        self._txt_file: TextIO | None = None
        self._started = False

    def start(self, config: dict) -> None:
        """Write batch_start event."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_file = open(self.jsonl_path, "w")
        self._txt_file = open(self.txt_path, "w")
        self._started = True

        timestamp = datetime.now(UTC).isoformat()

        event = {
            "event": "batch_start",
            "timestamp": timestamp,
            "config": config,
        }
        self._jsonl_file.write(json.dumps(event) + "\n")
        self._jsonl_file.flush()

        self._txt_file.write(f"[{self._format_timestamp()}] BATCH START\n")
        for key, value in config.items():
            self._txt_file.write(f"  {key}: {value}\n")
        self._txt_file.write("\n")
        self._txt_file.flush()

    def log_repo_complete(
        self,
        result: dict,
        gen_stats: GenerationStats | None,
    ) -> None:
        """Write repo_complete event to both files."""
        if not self._started:
            return

        timestamp = datetime.now(UTC).isoformat()
        repo_name = result.get("repo", "unknown")
        questions = result.get("generated", 0)
        skipped = result.get("skipped", False)
        reason = result.get("reason", "")
        sparse_mode = result.get("sparse_mode", False)

        event = {
            "event": "repo_complete",
            "timestamp": timestamp,
            "repo": repo_name,
            "questions": questions,
            "skipped": skipped,
            "sparse_mode": sparse_mode,
        }
        if reason:
            event["reason"] = reason
        if gen_stats:
            event["timeout_count"] = gen_stats.timeout_count
            event["strategy_counts"] = gen_stats.strategy_counts
            event["attempt_count"] = gen_stats.attempt_count
            event["duration_seconds"] = gen_stats.duration_seconds

        self._jsonl_file.write(json.dumps(event) + "\n")
        self._jsonl_file.flush()

        status = "SKIP" if skipped else "OK"
        self._txt_file.write(f"[{self._format_timestamp()}] {status}: {repo_name}\n")

        if skipped:
            self._txt_file.write(f"  Reason: {reason}\n")
        else:
            duration = gen_stats.duration_seconds if gen_stats else 0
            timeouts = gen_stats.timeout_count if gen_stats else 0
            self._txt_file.write(
                f"  Questions: {questions:,} | Timeouts: {timeouts} | Duration: {duration:.1f}s"
            )
            if sparse_mode:
                self._txt_file.write(" [sparse]")
            self._txt_file.write("\n")

            if gen_stats and gen_stats.strategy_counts:
                total = sum(gen_stats.strategy_counts.values())
                if total > 0:
                    parts = []
                    for strat, cnt in sorted(
                        gen_stats.strategy_counts.items(),
                        key=lambda x: -x[1],
                    ):
                        pct = cnt / total * 100
                        parts.append(f"{strat}({pct:.0f}%)")
                    self._txt_file.write(f"  Strategy: {', '.join(parts)}\n")

        self._txt_file.write("\n")
        self._txt_file.flush()

    def finish(self, summary: QuestionsStats) -> None:
        """Write batch_complete event."""
        if not self._started:
            return

        timestamp = datetime.now(UTC).isoformat()

        successful = summary.completed_repos - summary.skipped_repos
        event = {
            "event": "batch_complete",
            "timestamp": timestamp,
            "summary": {
                "total_repos": summary.total_repos,
                "completed_repos": summary.completed_repos,
                "skipped_repos": summary.skipped_repos,
                "sparse_mode_repos": summary.sparse_mode_repos,
                "total_questions": summary.total_questions,
                "total_timeouts": summary.total_timeouts,
                "total_attempts": summary.total_attempts,
                "elapsed_seconds": summary.elapsed_seconds,
                "strategy_counts": dict(summary.strategy_counts),
            },
        }
        self._jsonl_file.write(json.dumps(event) + "\n")

        elapsed_str = self._format_duration(summary.elapsed_seconds)
        self._txt_file.write(f"[{self._format_timestamp()}] BATCH COMPLETE\n")
        self._txt_file.write(
            f"  Total: {summary.total_questions:,} questions from {successful} repos in {elapsed_str}\n"
        )
        self._txt_file.write(f"  Skipped: {summary.skipped_repos} repos\n")
        self._txt_file.write(f"  Timeouts: {summary.total_timeouts}\n")

        if summary.strategy_counts:
            total = sum(summary.strategy_counts.values())
            if total > 0:
                self._txt_file.write("  Strategy distribution:\n")
                for strat, cnt in sorted(
                    summary.strategy_counts.items(),
                    key=lambda x: -x[1],
                ):
                    pct = cnt / total * 100
                    self._txt_file.write(f"    {strat}: {cnt:,} ({pct:.1f}%)\n")

    def close(self) -> None:
        """Close file handles."""
        if self._jsonl_file:
            self._jsonl_file.close()
            self._jsonl_file = None
        if self._txt_file:
            self._txt_file.close()
            self._txt_file = None

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_duration(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


class QuestionsProgressUI:
    """
    Rich terminal UI for question generation phase.

    Displays:
    - Progress bar for repo completion
    - Stats table: questions generated, timeouts, strategy distribution
    - Worker panel showing currently processing repos
    - Activity log with recent repo completions
    """

    def __init__(
        self,
        total_repos: int,
        max_activity_log: int = 5,
        log_exporter: QuestionsLogExporter | None = None,
    ):
        self.console = Console()
        self.total_repos = total_repos
        self.max_activity_log = max_activity_log
        self.log_exporter = log_exporter

        self.stats = QuestionsStats(total_repos=total_repos)
        self.activity_log: list[QuestionsActivityLogEntry] = []
        self._live: Live | None = None

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            expand=False,
        )

        self._repo_task: TaskID | None = None

    def start(self, config: dict | None = None) -> None:
        """Initialize progress bar and optionally log batch start."""
        self._repo_task = self.progress.add_task(
            "Generating",
            total=self.total_repos,
        )
        self.stats.start_time = time.time()

        if self.log_exporter and config:
            self.log_exporter.start(config)

    def live_context(self) -> Live:
        """Get a Live context for updating the display."""
        self._live = Live(
            self._render_dashboard(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )
        return self._live

    def mark_repo_started(self, repo_name: str) -> None:
        """Mark a repo as started processing."""
        if repo_name not in self.stats.in_progress_repos:
            self.stats.in_progress_repos.append(repo_name)
        self.stats.current_repo = repo_name
        self._refresh()

    def update_repo_complete(
        self,
        result: dict,
        gen_stats: GenerationStats | None = None,
    ) -> None:
        """Update after a repo completes question generation."""
        repo_name = result.get("repo", "unknown")
        questions = result.get("generated", 0)
        skipped = result.get("skipped", False)
        sparse_mode = result.get("sparse_mode", False)
        reason = result.get("reason", "")

        self.stats.completed_repos += 1
        self.stats.total_questions += questions

        if skipped:
            self.stats.skipped_repos += 1
        if sparse_mode:
            self.stats.sparse_mode_repos += 1

        if gen_stats:
            self.stats.merge_generation_stats(gen_stats)

        if repo_name in self.stats.in_progress_repos:
            self.stats.in_progress_repos.remove(repo_name)
        self.stats.current_repo = repo_name

        if skipped:
            msg = reason or "Skipped"
        else:
            msg = f"{questions:,} questions"

        duration = gen_stats.duration_seconds if gen_stats else 0
        timeouts = gen_stats.timeout_count if gen_stats else 0

        self.activity_log.append(
            QuestionsActivityLogEntry(
                timestamp=time.time(),
                repo_name=repo_name,
                success=not skipped,
                message=msg,
                questions_generated=questions,
                timeouts=timeouts,
                duration=duration,
                sparse_mode=sparse_mode,
            )
        )
        self._trim_activity_log()

        if self._repo_task is not None:
            self.progress.update(
                self._repo_task,
                completed=self.stats.completed_repos,
            )

        if self.log_exporter:
            self.log_exporter.log_repo_complete(result, gen_stats)

        self._refresh()

    def finish(self) -> None:
        """Mark processing as complete."""
        self._refresh()

    def _trim_activity_log(self) -> None:
        """Keep activity log at max size."""
        if len(self.activity_log) > self.max_activity_log:
            self.activity_log = self.activity_log[-self.max_activity_log :]

    def _refresh(self) -> None:
        """Refresh the live display."""
        if self._live:
            self._live.update(self._render_dashboard())

    def _render_dashboard(self) -> Panel:
        """Render the full dashboard."""
        header = self._render_header()
        progress_section = self._render_progress()
        stats_section = self._render_stats()
        strategy_section = self._render_strategy_distribution()
        workers_section = self._render_workers()
        activity_section = self._render_activity()

        content = Group(
            header,
            Text(""),
            progress_section,
            Text(""),
            stats_section,
            Text(""),
            strategy_section,
            Text(""),
            workers_section,
            Text(""),
            activity_section,
        )

        return Panel(
            content,
            title="[bold]Code Graph RAG Questions Generator[/bold]",
            border_style="green",
        )

    def _render_header(self) -> Text:
        """Render the header with phase and current repo."""
        text = Text()
        text.append("Phase: ", style="dim")
        text.append("Generating", style="bold green")
        text.append("   Current: ", style="dim")
        text.append(self.stats.current_repo or "-", style="bold")
        return text

    def _render_progress(self) -> Panel:
        """Render progress bar."""
        return Panel(
            self.progress,
            title="Progress",
            border_style="dim",
        )

    def _render_stats(self) -> Table:
        """Render stats table."""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Label", style="dim")
        table.add_column("Value", style="bold")
        table.add_column("Label2", style="dim")
        table.add_column("Value2", style="bold")

        successful = self.stats.completed_repos - self.stats.skipped_repos
        table.add_row(
            "Questions:",
            f"[green]{self.stats.total_questions:,}[/green]",
            "Repos/min:",
            f"{self.stats.repos_per_minute:.1f}",
        )

        table.add_row(
            "Timeouts:",
            f"[yellow]{self.stats.total_timeouts}[/yellow]",
            "Questions/min:",
            f"{self.stats.questions_per_minute:,.0f}",
        )

        skipped_style = "[red]" if self.stats.skipped_repos > 0 else ""
        skipped_end = "[/red]" if self.stats.skipped_repos > 0 else ""
        table.add_row(
            "Skipped:",
            f"{skipped_style}{self.stats.skipped_repos}{skipped_end}",
            "Avg/repo:",
            f"{self.stats.avg_questions_per_repo:,.0f}",
        )

        eta = self.stats.eta_seconds
        eta_str = self._format_duration(eta) if eta else "-"
        table.add_row(
            "Sparse mode:",
            str(self.stats.sparse_mode_repos),
            "ETA:",
            eta_str,
        )

        elapsed_str = self._format_duration(self.stats.elapsed_seconds)
        table.add_row(
            "Successful:",
            f"[green]{successful}[/green]",
            "Elapsed:",
            elapsed_str,
        )

        return table

    def _render_strategy_distribution(self) -> Panel:
        """Render strategy distribution with visual bars."""
        if not self.stats.strategy_counts:
            return Panel(
                Text("No strategy data yet", style="dim"),
                title="Strategy Distribution",
                border_style="dim",
            )

        total = sum(self.stats.strategy_counts.values())
        if total == 0:
            return Panel(
                Text("No strategy data yet", style="dim"),
                title="Strategy Distribution",
                border_style="dim",
            )

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Strategy", style="bold", width=10)
        table.add_column("Bar", width=20)
        table.add_column("Pct", style="dim", width=5)
        table.add_column("Count", style="dim", width=10)

        sorted_strategies = sorted(
            self.stats.strategy_counts.items(),
            key=lambda x: -x[1],
        )

        bar_width = 20
        for strategy, count in sorted_strategies:
            pct = count / total * 100
            filled = int(bar_width * count / total)
            bar = "[green]" + "\u2588" * filled + "[/green]" + "\u2591" * (bar_width - filled)
            table.add_row(strategy, bar, f"{pct:.0f}%", f"{count:,}")

        return Panel(
            table,
            title="Strategy Distribution",
            border_style="dim",
        )

    def _render_workers(self) -> Panel:
        """Render currently processing repos."""
        if not self.stats.in_progress_repos:
            return Panel(
                Text("Waiting for workers...", style="dim"),
                title="Currently Processing",
                border_style="dim",
            )

        lines: list[Text] = []
        for i, repo_name in enumerate(self.stats.in_progress_repos):
            line = Text()
            line.append(f"  Worker {i + 1}: ", style="dim")
            line.append(repo_name, style="bold cyan")
            lines.append(line)

        return Panel(
            Group(*lines),
            title=f"Currently Processing ({len(self.stats.in_progress_repos)} workers)",
            border_style="cyan",
        )

    def _render_activity(self) -> Panel:
        """Render activity log."""
        if not self.activity_log:
            return Panel("No activity yet", title="Recent Activity", border_style="dim")

        lines: list[Text] = []
        for entry in reversed(self.activity_log):
            line = Text()
            if entry.success:
                line.append("[OK]   ", style="green")
            else:
                line.append("[SKIP] ", style="red")
            line.append(f"{entry.repo_name}", style="bold")
            line.append(f" - {entry.message}", style="dim")
            if entry.duration > 0:
                line.append(f" ({entry.duration:.1f}s)", style="dim")
            if entry.sparse_mode:
                line.append(" [sparse]", style="yellow")
            lines.append(line)

        return Panel(
            Group(*lines),
            title="Recent Activity",
            border_style="dim",
        )

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def print_summary(self) -> None:
        """Print final summary to console."""
        self.console.print()
        self.console.rule("[bold]Questions Generation Summary[/bold]")

        table = Table(show_header=False, box=None)
        table.add_column("Label", style="dim")
        table.add_column("Value", style="bold")

        successful = self.stats.completed_repos - self.stats.skipped_repos
        table.add_row("Total Repos:", str(self.stats.total_repos))
        table.add_row("Successful:", f"[green]{successful}[/green]")
        table.add_row("Skipped:", f"[red]{self.stats.skipped_repos}[/red]")
        table.add_row("Sparse Mode:", str(self.stats.sparse_mode_repos))
        table.add_row("Total Questions:", f"{self.stats.total_questions:,}")
        table.add_row("Total Timeouts:", str(self.stats.total_timeouts))
        if successful > 0:
            table.add_row("Avg Questions/Repo:", f"{self.stats.avg_questions_per_repo:,.0f}")
        table.add_row("Total Time:", self._format_duration(self.stats.elapsed_seconds))

        self.console.print(table)

        if self.stats.strategy_counts:
            self.console.print()
            self.console.print("[dim]Strategy Distribution:[/dim]")
            total = sum(self.stats.strategy_counts.values())
            for strategy, count in sorted(
                self.stats.strategy_counts.items(),
                key=lambda x: -x[1],
            ):
                pct = count / total * 100
                self.console.print(f"  {strategy}: {count:,} ({pct:.1f}%)")

        if self.log_exporter:
            self.console.print()
            self.console.print("[dim]Log files:[/dim]")
            self.console.print(f"  JSONL: {self.log_exporter.jsonl_path}")
            self.console.print(f"  TXT:   {self.log_exporter.txt_path}")

        self.console.print()
