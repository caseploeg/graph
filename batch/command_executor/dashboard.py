from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .aggregators import CommandPrefixAggregator
from .pattern_detector import StreamingPatternAggregator
from .schemas import CommandResult


@dataclass
class DashboardStats:
    total_commands: int = 0
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    cached: int = 0
    start_time: float = field(default_factory=time.time)
    recent_rates: list[float] = field(default_factory=list)
    last_rate_time: float = field(default_factory=time.time)
    last_rate_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_result(self, success: bool, cached: bool) -> None:
        with self._lock:
            self.processed += 1
            if cached:
                self.cached += 1
            if success:
                self.succeeded += 1
            else:
                self.failed += 1

            now = time.time()
            if now - self.last_rate_time >= 1.0:
                rate = (self.processed - self.last_rate_count) / (now - self.last_rate_time)
                self.recent_rates.append(rate)
                if len(self.recent_rates) > 10:
                    self.recent_rates.pop(0)
                self.last_rate_time = now
                self.last_rate_count = self.processed

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def commands_per_sec(self) -> float:
        if self.recent_rates:
            return sum(self.recent_rates) / len(self.recent_rates)
        elapsed = self.elapsed_seconds
        if elapsed > 0:
            return self.processed / elapsed
        return 0.0

    @property
    def eta_seconds(self) -> float | None:
        rate = self.commands_per_sec
        if rate > 0 and self.total_commands > 0:
            remaining = self.total_commands - self.processed
            return remaining / rate
        return None

    @property
    def cache_hit_rate(self) -> float:
        if self.processed > 0:
            return self.cached / self.processed
        return 0.0

    @property
    def failure_rate(self) -> float:
        if self.processed > 0:
            return self.failed / self.processed
        return 0.0


def get_system_stats(output_path: Path | None = None) -> dict:
    cpu_percent = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()

    disk_path = output_path.parent if output_path else Path(".")
    try:
        disk = shutil.disk_usage(disk_path)
        disk_free_gb = disk.free / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_percent = (disk.used / disk.total) * 100
    except OSError:
        disk_free_gb = 0
        disk_total_gb = 0
        disk_percent = 0

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": memory.percent,
        "memory_used_gb": memory.used / (1024**3),
        "memory_total_gb": memory.total / (1024**3),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "disk_percent": disk_percent,
    }


def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:.0f}m {secs:.0f}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours:.0f}h {mins:.0f}m"


class Dashboard:
    def __init__(
        self,
        total_commands: int,
        output_path: Path,
        cache_path: Path,
        max_recent_failures: int = 10,
        max_prefix_groups: int = 100,
        max_error_patterns: int = 100,
    ):
        self.stats = DashboardStats(total_commands=total_commands)
        self.output_path = output_path
        self.cache_path = cache_path
        self.console = Console()
        self.live: Live | None = None
        self._stop_event = threading.Event()

        # Failure analysis aggregators
        self.prefix_aggregator = CommandPrefixAggregator(max_groups=max_prefix_groups)
        self.pattern_aggregator = StreamingPatternAggregator(
            max_patterns=max_error_patterns
        )

        # Recent failures ring buffer
        self.max_recent_failures = max_recent_failures
        self.recent_failures: list[CommandResult] = []
        self._lock = threading.Lock()

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[bold]{task.percentage:>5.1f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            expand=True,
        )
        self.task_id = self.progress.add_task(
            "Processing", total=total_commands
        )

    def _build_display(self) -> Group:
        sys_stats = get_system_stats(self.output_path)

        stats_table = Table(show_header=False, box=None, padding=(0, 2))
        stats_table.add_column("Label", style="bold")
        stats_table.add_column("Value", justify="right")
        stats_table.add_column("Label2", style="bold")
        stats_table.add_column("Value2", justify="right")

        rate = self.stats.commands_per_sec
        eta = self.stats.eta_seconds

        stats_table.add_row(
            "Processed:", f"{self.stats.processed:,}",
            "Rate:", f"{rate:,.0f} cmd/s",
        )
        stats_table.add_row(
            "Succeeded:", f"[green]{self.stats.succeeded:,}[/green]",
            "ETA:", format_time(eta) if eta else "calculating...",
        )
        stats_table.add_row(
            "Failed:", f"[red]{self.stats.failed:,}[/red]" if self.stats.failed else "0",
            "Cache hits:", f"{self.stats.cache_hit_rate:.1%}",
        )
        stats_table.add_row(
            "Cached:", f"[cyan]{self.stats.cached:,}[/cyan]",
            "Fail rate:", f"[red]{self.stats.failure_rate:.1%}[/red]" if self.stats.failure_rate > 0.05 else f"{self.stats.failure_rate:.1%}",
        )

        system_table = Table(show_header=False, box=None, padding=(0, 2))
        system_table.add_column("Label", style="bold")
        system_table.add_column("Value", justify="right")
        system_table.add_column("Label2", style="bold")
        system_table.add_column("Value2", justify="right")

        cpu_style = "red" if sys_stats["cpu_percent"] > 90 else "yellow" if sys_stats["cpu_percent"] > 70 else "green"
        mem_style = "red" if sys_stats["memory_percent"] > 90 else "yellow" if sys_stats["memory_percent"] > 70 else "green"
        disk_style = "red" if sys_stats["disk_percent"] > 90 else "yellow" if sys_stats["disk_percent"] > 80 else "green"

        system_table.add_row(
            "CPU:", f"[{cpu_style}]{sys_stats['cpu_percent']:.0f}%[/{cpu_style}]",
            "Disk free:", f"[{disk_style}]{sys_stats['disk_free_gb']:.1f} GB[/{disk_style}]",
        )
        system_table.add_row(
            "Memory:", f"[{mem_style}]{sys_stats['memory_percent']:.0f}%[/{mem_style}] ({sys_stats['memory_used_gb']:.1f}/{sys_stats['memory_total_gb']:.1f} GB)",
            "Disk used:", f"{sys_stats['disk_percent']:.0f}%",
        )

        output_size = self.output_path.stat().st_size / (1024**2) if self.output_path.exists() else 0
        cache_size = self.cache_path.stat().st_size / (1024**2) if self.cache_path.exists() else 0

        system_table.add_row(
            "Output:", f"{output_size:.1f} MB",
            "Cache DB:", f"{cache_size:.1f} MB",
        )

        # Build failure analysis sections
        prefix_panel = self._build_prefix_table()
        error_panel = self._build_error_patterns()
        recent_panel = self._build_recent_failures()

        return Group(
            Panel(stats_table, title="[bold]Progress Stats[/bold]", border_style="blue"),
            Panel(system_table, title="[bold]System Resources[/bold]", border_style="cyan"),
            self.progress,
            prefix_panel,
            error_panel,
            recent_panel,
        )

    def _build_prefix_table(self) -> Panel:
        """Build the failures by command prefix table."""
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("Prefix", style="cyan", width=20)
        table.add_column("Total", justify="right", width=8)
        table.add_column("Failed", justify="right", width=8)
        table.add_column("Rate", justify="right", width=8)
        table.add_column("Avg ms", justify="right", width=10)

        top_prefixes = self.prefix_aggregator.get_top_failing(10)
        if not top_prefixes:
            return Panel(
                Text("No commands processed yet", style="dim"),
                title="[bold]Failures by Command Prefix[/bold]",
                border_style="yellow",
            )

        for prefix_stat in top_prefixes:
            rate_pct = prefix_stat.failure_rate * 100
            rate_style = "red bold" if rate_pct > 5 else "red" if rate_pct > 1 else ""
            failed_style = "red" if prefix_stat.failed > 0 else ""

            table.add_row(
                prefix_stat.prefix[:20],
                f"{prefix_stat.total:,}",
                Text(f"{prefix_stat.failed:,}", style=failed_style),
                Text(f"{rate_pct:.1f}%", style=rate_style),
                f"{prefix_stat.avg_duration_ms:.1f}",
            )

        return Panel(
            table,
            title="[bold]Failures by Command Prefix[/bold]",
            border_style="yellow",
        )

    def _build_error_patterns(self) -> Panel:
        """Build the top error patterns section."""
        lines: list[Text] = []

        top_patterns = self.pattern_aggregator.get_top_patterns(5)
        if not top_patterns:
            return Panel(
                Text("No errors detected yet", style="dim green"),
                title="[bold]Top Error Patterns (stderr)[/bold]",
                border_style="red",
            )

        for i, pattern in enumerate(top_patterns, 1):
            line = Text()
            line.append(f"{i:2}. ", style="dim")
            line.append(f"{pattern.count:>6,}  ", style="bold red")
            # Truncate message for display
            msg = pattern.canonical_message[:55]
            if len(pattern.canonical_message) > 55:
                msg += "..."
            line.append(msg, style="")
            lines.append(line)

        return Panel(
            Group(*lines),
            title="[bold]Top Error Patterns (stderr)[/bold]",
            border_style="red",
        )

    def _build_recent_failures(self) -> Panel:
        """Build the recent failures section."""
        with self._lock:
            failures = list(self.recent_failures[-5:])

        if not failures:
            return Panel(
                Text("No failures yet", style="dim green"),
                title="[bold]Recent Failures[/bold]",
                border_style="dim",
            )

        lines: list[Text] = []
        for result in reversed(failures):
            line = Text()
            line.append("[FAIL] ", style="red")
            # Truncate command
            cmd_short = result.cmd[:35] + "..." if len(result.cmd) > 35 else result.cmd
            line.append(cmd_short, style="bold")
            # Extract repo name
            repo_short = result.repo.split("/")[-1][:15]
            line.append(f" @ {repo_short}", style="dim")
            line.append(f" (rc={result.return_code})", style="dim")
            lines.append(line)

        return Panel(
            Group(*lines),
            title=f"[bold]Recent Failures ({len(self.recent_failures)} total)[/bold]",
            border_style="dim",
        )

    def start(self) -> None:
        psutil.cpu_percent(interval=None)
        self.live = Live(
            self._build_display(),
            console=self.console,
            refresh_per_second=2,
            transient=False,
        )
        self.live.start()

    def update(self, success: bool, cached: bool) -> None:
        """Update with simple success/cached flags (backward compatible)."""
        self.stats.record_result(success, cached)
        self.progress.update(self.task_id, completed=self.stats.processed)
        if self.live:
            self.live.update(self._build_display())

    def update_result(self, result: CommandResult) -> None:
        """Update with full CommandResult for detailed failure analysis."""
        success = result.return_code == 0

        # Update basic stats
        self.stats.record_result(success, result.cached)
        self.progress.update(self.task_id, completed=self.stats.processed)

        # Update prefix aggregator
        self.prefix_aggregator.add_result(result)

        # Update error pattern aggregator and recent failures
        if not success:
            if result.stderr:
                self.pattern_aggregator.add_error(
                    result.stderr, result.cmd, result.repo
                )
            with self._lock:
                self.recent_failures.append(result)
                if len(self.recent_failures) > self.max_recent_failures:
                    self.recent_failures = self.recent_failures[
                        -self.max_recent_failures :
                    ]

        # Refresh display
        if self.live:
            self.live.update(self._build_display())

    def stop(self) -> None:
        if self.live:
            self.live.stop()

    def print_final_summary(self) -> None:
        self.console.print()
        self.console.print(Panel(
            f"""[bold]Final Results[/bold]

Total commands:  {self.stats.total_commands:,}
Processed:       {self.stats.processed:,}
Succeeded:       [green]{self.stats.succeeded:,}[/green]
Failed:          [red]{self.stats.failed:,}[/red]
Cached:          [cyan]{self.stats.cached:,}[/cyan]

Total time:      {format_time(self.stats.elapsed_seconds)}
Avg rate:        {self.stats.processed / self.stats.elapsed_seconds:,.0f} cmd/s
Cache hit rate:  {self.stats.cache_hit_rate:.1%}

Output file:     {self.output_path}
Cache DB:        {self.cache_path}
""",
            title="[bold green]Complete[/bold green]",
            border_style="green",
        ))

        # Print failure analysis if there were failures
        if self.stats.failed > 0:
            self.print_failure_report()

    def print_failure_report(self) -> None:
        """Print detailed failure analysis report."""
        self.console.print()
        self.console.rule("[bold red]Failure Analysis Report[/bold red]")

        # Top failing command prefixes
        self.console.print()
        self.console.print("[bold]Top Failing Command Prefixes:[/bold]")
        top_prefixes = self.prefix_aggregator.get_top_failing(15)
        if top_prefixes:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Prefix", style="cyan")
            table.add_column("Total", justify="right")
            table.add_column("Failed", justify="right")
            table.add_column("Rate", justify="right")
            table.add_column("Avg ms", justify="right")

            for p in top_prefixes:
                if p.failed > 0:
                    table.add_row(
                        p.prefix,
                        f"{p.total:,}",
                        f"[red]{p.failed:,}[/red]",
                        f"{p.failure_rate:.1%}",
                        f"{p.avg_duration_ms:.1f}",
                    )
            self.console.print(table)
        else:
            self.console.print("  No prefix data available", style="dim")

        # Top error patterns
        self.console.print()
        self.console.print("[bold]Top Error Patterns (stderr):[/bold]")
        top_patterns = self.pattern_aggregator.get_top_patterns(10)
        if top_patterns:
            for i, pattern in enumerate(top_patterns, 1):
                self.console.print(
                    f"  {i:2}. [red]{pattern.count:>6,}[/red] occurrences"
                )
                self.console.print(
                    f"      Pattern: [dim]{pattern.canonical_message[:80]}[/dim]"
                )
                if pattern.example_commands:
                    self.console.print(
                        f"      Example: {pattern.example_commands[0][:60]}"
                    )
                if pattern.example_repos:
                    self.console.print(
                        f"      Repo: {pattern.example_repos[0]}", style="dim"
                    )
                self.console.print()
        else:
            self.console.print("  No error patterns detected", style="dim")

        # Summary stats
        self.console.print()
        stats = self.pattern_aggregator.get_stats()
        self.console.print(
            f"[bold]Error Summary:[/bold] "
            f"{stats['total_errors']:,} total errors, "
            f"{stats['unique_patterns']} unique patterns"
        )
        self.console.print(
            f"[bold]Repos Processed:[/bold] {self.prefix_aggregator.unique_repos:,}"
        )
        self.console.print()
