"""
Memory-efficient stderr pattern detection using regex heuristics.

Strategy:
1. Normalize variable parts (paths, numbers, repo names) to placeholders
2. Hash normalized messages for grouping
3. Keep only top N patterns by frequency (bounded memory)
4. Periodically evict low-frequency patterns
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Regex patterns for normalization (order matters - more specific first)
NORMALIZERS = [
    # Git commit hashes (7-40 hex chars)
    (re.compile(r"\b[a-f0-9]{7,40}\b"), "{hash}"),
    # Absolute paths: /foo/bar/baz.py -> {path}
    (re.compile(r"(?:/[\w.-]+)+(?:/[\w.-]+)?"), "{path}"),
    # Repo names in paths: owner/repo -> {repo}
    (re.compile(r"\b[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+\b"), "{repo}"),
    # Line numbers: line 123 -> line {num}
    (re.compile(r"\bline\s+\d+\b", re.IGNORECASE), "line {num}"),
    # Numbers: 12345 -> {num}
    (re.compile(r"\b\d+\b"), "{num}"),
    # Quoted strings: "foo" or 'bar' -> {str}
    (re.compile(r'"[^"]*"'), "{str}"),
    (re.compile(r"'[^']*'"), "{str}"),
    # IP addresses: 192.168.1.1 -> {ip}
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "{ip}"),
    # Timestamps: 2024-01-13 -> {date}
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "{date}"),
    # Timestamps: 12:34:56 -> {time}
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "{time}"),
]


@dataclass
class ErrorPattern:
    """Represents a detected error pattern from stderr."""

    pattern_id: str  # Hash of normalized message
    canonical_message: str  # Representative normalized stderr message
    count: int = 0
    example_commands: list[str] = field(default_factory=list)
    example_repos: list[str] = field(default_factory=list)
    example_stderr: str = ""  # First raw stderr for context

    def add_example(
        self, cmd: str, repo: str, stderr: str, max_examples: int = 3
    ) -> None:
        if len(self.example_commands) < max_examples:
            self.example_commands.append(cmd)
            self.example_repos.append(repo)
            if not self.example_stderr:
                self.example_stderr = stderr[:500]  # Keep first example


def normalize_stderr(stderr: str) -> str:
    """Normalize stderr by replacing variable parts with placeholders."""
    if not stderr:
        return ""

    result = stderr.strip()

    # Take first line only (most error messages have key info in first line)
    if "\n" in result:
        result = result.split("\n")[0]

    # Truncate to reasonable length
    if len(result) > 200:
        result = result[:200]

    # Apply normalizers
    for pattern, replacement in NORMALIZERS:
        result = pattern.sub(replacement, result)

    return result


def compute_pattern_id(normalized: str) -> str:
    """Compute a stable ID for a normalized pattern."""
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


class StreamingPatternAggregator:
    """
    Memory-bounded pattern aggregation for stderr messages.

    For 1M+ commands, we can't store every unique error.
    Strategy:
    - Keep exact counts for top K patterns
    - Periodically evict low-frequency patterns
    """

    def __init__(self, max_patterns: int = 100):
        self.max_patterns = max_patterns
        self.patterns: dict[str, ErrorPattern] = {}
        self.total_errors = 0
        self._eviction_counter = 0

    def add_error(self, stderr: str, cmd: str, repo: str) -> None:
        """Add an error to the aggregator."""
        if not stderr or not stderr.strip():
            return

        self.total_errors += 1
        self._eviction_counter += 1

        normalized = normalize_stderr(stderr)
        if not normalized:
            return

        pattern_id = compute_pattern_id(normalized)

        if pattern_id in self.patterns:
            pattern = self.patterns[pattern_id]
            pattern.count += 1
            pattern.add_example(cmd, repo, stderr)
        else:
            # Check if we need to evict
            if len(self.patterns) >= self.max_patterns:
                self._evict_low_frequency()

            self.patterns[pattern_id] = ErrorPattern(
                pattern_id=pattern_id,
                canonical_message=normalized,
                count=1,
                example_commands=[cmd],
                example_repos=[repo],
                example_stderr=stderr[:500],
            )

        # Periodic eviction
        if self._eviction_counter >= 1000:
            self._eviction_counter = 0
            self._evict_low_frequency()

    def _evict_low_frequency(self) -> None:
        """Remove patterns below threshold to make room."""
        if len(self.patterns) < self.max_patterns * 0.9:
            return

        # Sort by count, keep top 80%
        sorted_patterns = sorted(
            self.patterns.items(),
            key=lambda x: x[1].count,
            reverse=True,
        )

        keep_count = int(self.max_patterns * 0.8)
        self.patterns = dict(sorted_patterns[:keep_count])

    def get_top_patterns(self, n: int = 10) -> list[ErrorPattern]:
        """Get top N patterns by frequency."""
        sorted_patterns = sorted(
            self.patterns.values(),
            key=lambda p: p.count,
            reverse=True,
        )
        return sorted_patterns[:n]

    def get_stats(self) -> dict:
        """Get aggregator statistics."""
        return {
            "total_errors": self.total_errors,
            "unique_patterns": len(self.patterns),
        }
