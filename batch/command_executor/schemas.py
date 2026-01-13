from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CommandInput:
    cmd: str
    repo: str

    @classmethod
    def from_json(cls, line: str) -> CommandInput:
        data = json.loads(line)
        return cls(cmd=data["cmd"], repo=data["repo"])


@dataclass
class CommandResult:
    cmd: str
    repo: str
    stdout: str
    stderr: str
    return_code: int
    duration_ms: float
    cached: bool

    def to_json(self) -> str:
        return json.dumps({
            "cmd": self.cmd,
            "repo": self.repo,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
        })

    @classmethod
    def from_json(cls, line: str) -> CommandResult:
        data = json.loads(line)
        return cls(
            cmd=data["cmd"],
            repo=data["repo"],
            stdout=data["stdout"],
            stderr=data["stderr"],
            return_code=data["return_code"],
            duration_ms=data["duration_ms"],
            cached=data["cached"],
        )


@dataclass
class BatchResult:
    total_commands: int
    executed: int
    cached: int
    failed: int
    total_duration_s: float
    avg_execution_ms: float


@dataclass
class CacheStats:
    total_entries: int
    hits: int
    misses: int
    hit_rate: float


@dataclass
class InspectorSummary:
    total: int
    success: int
    failed: int
    cached: int
    unique_repos: int
    unique_commands: int
    avg_duration_ms: float
    slowest_commands: list[CommandResult] = field(default_factory=list)
