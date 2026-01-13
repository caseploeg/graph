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
    ):
        self.stats = DashboardStats(total_commands=total_commands)
        self.output_path = output_path
        self.cache_path = cache_path
        self.console = Console()
        self.live: Live | None = None
        self._stop_event = threading.Event()

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

        return Group(
            Panel(stats_table, title="[bold]Progress Stats[/bold]", border_style="blue"),
            Panel(system_table, title="[bold]System Resources[/bold]", border_style="cyan"),
            self.progress,
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
        self.stats.record_result(success, cached)
        self.progress.update(self.task_id, completed=self.stats.processed)
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
