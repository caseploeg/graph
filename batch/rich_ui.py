"""
Rich Terminal UI for Batch Processing

Provides a live dashboard with progress bars, stats, and activity log.

Usage:
    from batch.rich_ui import BatchProgressUI

    ui = BatchProgressUI(total_repos=100)
    with ui.live_context():
        ui.update_clone_progress("facebook/react", success=True)
        ui.update_process_progress(result)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.layout import Layout
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

if TYPE_CHECKING:
    from batch.batch_processor import ProcessResult
    from batch.github_cloner import CloneResult


@dataclass
class BatchStats:
    """Statistics for batch processing."""
    total_repos: int = 0
    cloned: int = 0
    clone_failed: int = 0
    processed: int = 0
    process_failed: int = 0
    total_nodes: int = 0
    total_relationships: int = 0
    start_time: float = field(default_factory=time.time)
    current_repo: str = ""
    phase: str = "initializing"  # "cloning" | "processing" | "uploading" | "done"

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def repos_per_minute(self) -> float:
        elapsed_min = self.elapsed_seconds / 60
        if elapsed_min < 0.1:
            return 0
        return self.processed / elapsed_min

    @property
    def nodes_per_second(self) -> float:
        if self.elapsed_seconds < 1:
            return 0
        return self.total_nodes / self.elapsed_seconds

    @property
    def eta_seconds(self) -> float | None:
        if self.processed == 0:
            return None
        rate = self.processed / self.elapsed_seconds
        remaining = self.total_repos - self.processed
        if rate < 0.001:
            return None
        return remaining / rate


@dataclass
class ActivityLogEntry:
    """Entry in the activity log."""
    timestamp: float
    repo_name: str
    success: bool
    message: str
    nodes: int = 0
    relationships: int = 0
    duration: float = 0


class BatchProgressUI:
    """
    Rich terminal UI for batch processing.

    Displays:
    - Header with title and phase
    - Progress bars for cloning and processing
    - Stats table with counts and throughput
    - Activity log with recent results
    """

    def __init__(
        self,
        total_repos: int,
        skip_clone: bool = False,
        max_activity_log: int = 5,
    ):
        self.console = Console()
        self.total_repos = total_repos
        self.skip_clone = skip_clone
        self.max_activity_log = max_activity_log

        self.stats = BatchStats(total_repos=total_repos)
        self.activity_log: list[ActivityLogEntry] = []
        self._live: Live | None = None

        # Progress bars
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

        # Task IDs
        self._clone_task: TaskID | None = None
        self._process_task: TaskID | None = None

    def start(self) -> None:
        """Initialize progress bars."""
        if not self.skip_clone:
            self._clone_task = self.progress.add_task(
                "Cloning",
                total=self.total_repos,
            )
        self._process_task = self.progress.add_task(
            "Processing",
            total=self.total_repos,
        )
        self.stats.phase = "cloning" if not self.skip_clone else "processing"

    def live_context(self) -> Live:
        """Get a Live context for updating the display."""
        self._live = Live(
            self._render_dashboard(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )
        return self._live

    def update_clone_progress(self, result: CloneResult) -> None:
        """Update after a clone completes."""
        if result.success:
            self.stats.cloned += 1
        else:
            self.stats.clone_failed += 1

        repo_name = result.github_url.split("/")[-2:]
        repo_name = "/".join(repo_name)
        self.stats.current_repo = repo_name

        # Add to activity log
        self.activity_log.append(ActivityLogEntry(
            timestamp=time.time(),
            repo_name=repo_name,
            success=result.success,
            message=result.error or "Cloned",
            duration=result.duration_seconds,
        ))
        self._trim_activity_log()

        # Update progress bar
        if self._clone_task is not None:
            self.progress.update(
                self._clone_task,
                completed=self.stats.cloned + self.stats.clone_failed,
            )

        self._refresh()

    def update_process_progress(self, result: ProcessResult) -> None:
        """Update after processing completes."""
        if result.success:
            self.stats.processed += 1
            self.stats.total_nodes += result.node_count
            self.stats.total_relationships += result.relationship_count
        else:
            self.stats.process_failed += 1

        repo_name = result.repo_path.split("/")[-1]
        self.stats.current_repo = repo_name

        # Add to activity log
        if result.success:
            msg = f"{result.node_count:,} nodes, {result.relationship_count:,} rels"
        else:
            msg = result.error or "Failed"

        self.activity_log.append(ActivityLogEntry(
            timestamp=time.time(),
            repo_name=repo_name,
            success=result.success,
            message=msg,
            nodes=result.node_count,
            relationships=result.relationship_count,
            duration=result.duration_seconds,
        ))
        self._trim_activity_log()

        # Update progress bar
        if self._process_task is not None:
            completed = self.stats.processed + self.stats.process_failed
            self.progress.update(self._process_task, completed=completed)

        self._refresh()

    def set_phase(self, phase: str) -> None:
        """Set the current phase."""
        self.stats.phase = phase
        self._refresh()

    def finish(self) -> None:
        """Mark processing as complete."""
        self.stats.phase = "done"
        self._refresh()

    def _trim_activity_log(self) -> None:
        """Keep activity log at max size."""
        if len(self.activity_log) > self.max_activity_log:
            self.activity_log = self.activity_log[-self.max_activity_log:]

    def _refresh(self) -> None:
        """Refresh the live display."""
        if self._live:
            self._live.update(self._render_dashboard())

    def _render_dashboard(self) -> Panel:
        """Render the full dashboard."""
        layout = Layout()

        # Build sections
        header = self._render_header()
        progress_section = self._render_progress()
        stats_section = self._render_stats()
        activity_section = self._render_activity()

        # Combine into a group
        content = Group(
            header,
            Text(""),
            progress_section,
            Text(""),
            stats_section,
            Text(""),
            activity_section,
        )

        return Panel(
            content,
            title="[bold]Code Graph RAG Batch Processor[/bold]",
            border_style="blue",
        )

    def _render_header(self) -> Text:
        """Render the header with phase and current repo."""
        phase_colors = {
            "initializing": "dim",
            "cloning": "yellow",
            "processing": "cyan",
            "uploading": "magenta",
            "done": "green",
        }
        color = phase_colors.get(self.stats.phase, "white")

        text = Text()
        text.append("Phase: ", style="dim")
        text.append(self.stats.phase.capitalize(), style=f"bold {color}")
        text.append("   Current: ", style="dim")
        text.append(self.stats.current_repo or "-", style="bold")
        return text

    def _render_progress(self) -> Panel:
        """Render progress bars."""
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

        # Row 1: Success/Failed counts
        successful = self.stats.processed - self.stats.process_failed
        table.add_row(
            "Successful:",
            f"[green]{successful}[/green]",
            "Repos/min:",
            f"{self.stats.repos_per_minute:.1f}",
        )

        # Row 2: Failed and nodes/sec
        failed_style = "[red]" if self.stats.process_failed > 0 else ""
        failed_end = "[/red]" if self.stats.process_failed > 0 else ""
        table.add_row(
            "Failed:",
            f"{failed_style}{self.stats.process_failed}{failed_end}",
            "Nodes/sec:",
            f"{self.stats.nodes_per_second:.1f}",
        )

        # Row 3: Node counts and ETA
        eta = self.stats.eta_seconds
        eta_str = self._format_duration(eta) if eta else "-"
        table.add_row(
            "Total Nodes:",
            f"{self.stats.total_nodes:,}",
            "ETA:",
            eta_str,
        )

        # Row 4: Relationship counts and elapsed
        elapsed_str = self._format_duration(self.stats.elapsed_seconds)
        table.add_row(
            "Total Rels:",
            f"{self.stats.total_relationships:,}",
            "Elapsed:",
            elapsed_str,
        )

        return table

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
                line.append("[FAIL] ", style="red")
            line.append(f"{entry.repo_name}", style="bold")
            line.append(f" - {entry.message}", style="dim")
            line.append(f" ({entry.duration:.1f}s)", style="dim")
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
        self.console.rule("[bold]Final Summary[/bold]")

        table = Table(show_header=False, box=None)
        table.add_column("Label", style="dim")
        table.add_column("Value", style="bold")

        successful = self.stats.processed - self.stats.process_failed
        table.add_row("Total Repos:", str(self.total_repos))
        table.add_row("Successful:", f"[green]{successful}[/green]")
        table.add_row("Failed:", f"[red]{self.stats.process_failed}[/red]")
        table.add_row("Total Nodes:", f"{self.stats.total_nodes:,}")
        table.add_row("Total Relationships:", f"{self.stats.total_relationships:,}")
        table.add_row("Total Time:", self._format_duration(self.stats.elapsed_seconds))
        table.add_row("Avg Time/Repo:", self._format_duration(
            self.stats.elapsed_seconds / max(1, self.stats.processed)
        ))

        self.console.print(table)
        self.console.print()
