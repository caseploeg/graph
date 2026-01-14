"""
Memory-efficient streaming aggregators for batch command results.

Aggregates results by command prefix (e.g., "git log", "find -name") with
bounded memory using periodic eviction of low-frequency groups.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import CommandResult


@dataclass
class CommandPrefixStats:
    """Stats for a command prefix group (e.g., 'git log', 'find -name')."""

    prefix: str
    total: int = 0
    success: int = 0
    failed: int = 0
    cached: int = 0
    total_duration_ms: float = 0.0

    @property
    def failure_rate(self) -> float:
        return self.failed / self.total if self.total > 0 else 0.0

    @property
    def avg_duration_ms(self) -> float:
        non_cached = self.total - self.cached
        return self.total_duration_ms / non_cached if non_cached > 0 else 0.0


def extract_command_prefix(cmd: str) -> str:
    """
    Extract command prefix for grouping.

    Examples:
    - "git log --oneline" -> "git log"
    - "find . -name '*.py'" -> "find -name"
    - "grep -r pattern" -> "grep -r"
    - "ls -la /path" -> "ls -la"
    """
    parts = cmd.split()
    if not parts:
        return "unknown"

    base_cmd = parts[0]

    # Handle git - include subcommand
    if base_cmd == "git" and len(parts) > 1:
        subcommand = parts[1]
        # Skip flags to get actual subcommand
        if subcommand.startswith("-"):
            for p in parts[2:]:
                if not p.startswith("-"):
                    return f"git {p}"
            return "git"
        return f"git {subcommand}"

    # Handle find - include key options
    if base_cmd == "find" and len(parts) > 1:
        for opt in ["-name", "-type", "-exec", "-path", "-iname"]:
            if opt in parts:
                return f"find {opt}"
        return "find"

    # Handle grep variants - include key flags
    if base_cmd in ("grep", "rg", "ag", "ack"):
        flags = []
        for p in parts[1:4]:  # Check first few args
            if p.startswith("-") and not p.startswith("--"):
                flags.append(p)
            elif p.startswith("--"):
                # Extract flag name for long options
                flag_name = p.split("=")[0]
                if flag_name in ("--recursive", "-r"):
                    flags.append("-r")
        if flags:
            return f"{base_cmd} {' '.join(flags[:2])}"
        return base_cmd

    # Handle ls with flags
    if base_cmd == "ls" and len(parts) > 1 and parts[1].startswith("-"):
        return f"ls {parts[1]}"

    # Handle cat/head/tail with flags
    if base_cmd in ("cat", "head", "tail", "wc"):
        for p in parts[1:3]:
            if p.startswith("-"):
                return f"{base_cmd} {p}"
        return base_cmd

    return base_cmd


class CommandPrefixAggregator:
    """
    Aggregates command results by prefix with bounded memory.

    Keeps top N prefixes by total count, evicts infrequent ones periodically.
    """

    def __init__(self, max_groups: int = 100):
        self.max_groups = max_groups
        self.groups: dict[str, CommandPrefixStats] = {}
        self._unique_repos: set[str] = set()
        self._update_count = 0

    @property
    def unique_repos(self) -> int:
        return len(self._unique_repos)

    def add_result(self, result: CommandResult) -> None:
        """Add a command result to the aggregator."""
        prefix = extract_command_prefix(result.cmd)
        self._unique_repos.add(result.repo)
        self._update_count += 1

        if prefix not in self.groups:
            if len(self.groups) >= self.max_groups:
                self._evict_smallest()
            self.groups[prefix] = CommandPrefixStats(prefix=prefix)

        stats = self.groups[prefix]
        stats.total += 1

        if result.return_code == 0:
            stats.success += 1
        else:
            stats.failed += 1

        if result.cached:
            stats.cached += 1
        else:
            stats.total_duration_ms += result.duration_ms

        # Periodic eviction to keep memory bounded
        if self._update_count >= 10000:
            self._update_count = 0
            self._evict_smallest()

    def _evict_smallest(self) -> None:
        """Evict smallest groups to stay under limit."""
        if len(self.groups) < self.max_groups * 0.9:
            return

        sorted_groups = sorted(
            self.groups.items(),
            key=lambda x: x[1].total,
            reverse=True,
        )

        keep = int(self.max_groups * 0.8)
        self.groups = dict(sorted_groups[:keep])

    def get_top_failing(self, n: int = 10) -> list[CommandPrefixStats]:
        """Get top N prefixes sorted by failure count."""
        return sorted(
            self.groups.values(),
            key=lambda s: s.failed,
            reverse=True,
        )[:n]

    def get_top_by_total(self, n: int = 10) -> list[CommandPrefixStats]:
        """Get top N prefixes sorted by total count."""
        return sorted(
            self.groups.values(),
            key=lambda s: s.total,
            reverse=True,
        )[:n]

    def get_all_sorted(self) -> list[CommandPrefixStats]:
        """Get all prefixes sorted by total count."""
        return sorted(
            self.groups.values(),
            key=lambda s: s.total,
            reverse=True,
        )
