from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from batch.command_executor.allowlist import (
    READONLY_ALLOWLIST,
    SAFE_GIT_SUBCOMMANDS,
    SAFE_PIPE_SINKS,
    validate_command,
)
from batch.command_executor.cache import CommandCache
from batch.command_executor.executor_async import AsyncBatchExecutor
from batch.command_executor.inspector import ResultsInspector, format_result
from batch.command_executor.runner import SafeCommandRunner
from batch.command_executor.schemas import CommandInput, CommandResult


class TestAllowlist:
    def test_allowed_simple_commands(self) -> None:
        for cmd in ["ls", "ls -la", "find . -name '*.py'", "grep foo bar.txt"]:
            result = validate_command(cmd)
            assert result.valid, f"Expected {cmd} to be valid: {result.error}"

    def test_blocked_commands_not_in_allowlist(self) -> None:
        for cmd in ["rm file.txt", "chmod 755 script.sh", "wget http://evil.com"]:
            result = validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"
            assert "not in allowlist" in result.error.lower()

    def test_redirects_blocked(self) -> None:
        for cmd in ["ls > out.txt", "cat file >> log", "grep foo < input.txt"]:
            result = validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"
            assert "redirect" in result.error.lower()

    def test_chains_blocked(self) -> None:
        for cmd in ["ls && rm file", "cat foo || echo bar", "ls; rm -rf /"]:
            result = validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"
            assert "chain" in result.error.lower()

    def test_subshells_blocked(self) -> None:
        for cmd in ["echo $(whoami)", "ls `pwd`"]:
            result = validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"
            assert "subshell" in result.error.lower()

    def test_background_blocked(self) -> None:
        result = validate_command("sleep 10 &")
        assert not result.valid
        assert "background" in result.error.lower()

    def test_pipes_to_safe_sinks_allowed(self) -> None:
        for cmd in [
            "grep foo file | head -10",
            "ls -la | tail -5",
            "find . -name '*.py' | wc -l",
            "cat file.txt | grep pattern | head",
        ]:
            result = validate_command(cmd)
            assert result.valid, f"Expected {cmd} to be valid: {result.error}"

    def test_pipes_to_unsafe_sinks_blocked(self) -> None:
        for cmd in ["ls | xargs rm", "find . | xargs chmod"]:
            result = validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"
            assert "not allowed after pipe" in result.error.lower()

    def test_git_safe_subcommands_allowed(self) -> None:
        for sub in ["status", "log", "diff", "branch", "ls-files"]:
            result = validate_command(f"git {sub}")
            assert result.valid, f"Expected git {sub} to be valid: {result.error}"

    def test_git_unsafe_subcommands_blocked(self) -> None:
        for sub in ["push", "commit", "reset", "checkout"]:
            result = validate_command(f"git {sub}")
            assert not result.valid, f"Expected git {sub} to be blocked"
            assert "subcommand" in result.error.lower()

    def test_quoted_special_chars_allowed(self) -> None:
        result = validate_command("grep '>' file.txt")
        assert result.valid, f"Expected quoted > to be allowed: {result.error}"

        result = validate_command("grep \"&&\" file.txt")
        assert result.valid, f"Expected quoted && to be allowed: {result.error}"


class TestCache:
    def test_cache_set_and_get(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.sqlite"
        cache = CommandCache(db_path)

        result = CommandResult(
            cmd="ls -la",
            repo="/tmp/repo",
            stdout="file1\nfile2",
            stderr="",
            return_code=0,
            duration_ms=10.5,
            cached=False,
        )

        cache.set("ls -la", "/tmp/repo", result)
        cached = cache.get("ls -la", "/tmp/repo")

        assert cached is not None
        assert cached.stdout == "file1\nfile2"
        assert cached.return_code == 0

        cache.close()

    def test_cache_miss(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.sqlite"
        cache = CommandCache(db_path)

        cached = cache.get("nonexistent", "/tmp/repo")
        assert cached is None

        cache.close()

    def test_cache_stats(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.sqlite"
        cache = CommandCache(db_path)

        result = CommandResult(
            cmd="ls",
            repo="/tmp",
            stdout="",
            stderr="",
            return_code=0,
            duration_ms=1.0,
            cached=False,
        )

        cache.set("ls", "/tmp", result)
        cache.get("ls", "/tmp")
        cache.get("nonexistent", "/tmp")

        stats = cache.stats()
        assert stats.total_entries == 1
        assert stats.hits == 1
        assert stats.misses == 1

        cache.close()

    def test_cache_clear(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.sqlite"
        cache = CommandCache(db_path)

        result = CommandResult(
            cmd="ls",
            repo="/tmp",
            stdout="",
            stderr="",
            return_code=0,
            duration_ms=1.0,
            cached=False,
        )

        cache.set("ls", "/tmp", result)
        count = cache.clear()

        assert count == 1
        assert cache.get("ls", "/tmp") is None

        cache.close()


class TestRunner:
    @pytest.mark.anyio
    async def test_execute_simple_command(self, tmp_path: Path) -> None:
        runner = SafeCommandRunner(timeout=10)
        result = await runner.execute("ls -la", tmp_path)

        assert result.return_code == 0
        assert result.cached is False
        assert result.duration_ms > 0

    @pytest.mark.anyio
    async def test_execute_blocked_command(self, tmp_path: Path) -> None:
        runner = SafeCommandRunner(timeout=10)
        result = await runner.execute("rm file.txt", tmp_path)

        assert result.return_code == 1
        assert "not in allowlist" in result.stderr.lower()

    @pytest.mark.anyio
    async def test_execute_pipeline(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")

        runner = SafeCommandRunner(timeout=10)
        result = await runner.execute(f"cat {test_file} | head -2", tmp_path)

        assert result.return_code == 0
        assert "line1" in result.stdout
        assert "line2" in result.stdout
        assert "line3" not in result.stdout

    @pytest.mark.anyio
    async def test_execute_timeout(self, tmp_path: Path) -> None:
        runner = SafeCommandRunner(timeout=1)
        result = await runner.execute("find / -name 'impossible_file_xyz'", tmp_path)

        assert result.return_code in (0, 1, 124)


class TestExecutor:
    @pytest.mark.anyio
    async def test_executor_runs_commands(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.sqlite"
        cache = CommandCache(db_path)
        executor = AsyncBatchExecutor(cache, concurrency=10)

        commands = [
            CommandInput(cmd="ls", repo=str(tmp_path)),
            CommandInput(cmd="pwd", repo=str(tmp_path)),
        ]

        results: list[CommandResult] = []

        def on_result(r: CommandResult) -> None:
            results.append(r)

        batch_result = await executor.run(iter(commands), on_result)

        assert batch_result.total_commands == 2
        assert len(results) == 2

        cache.close()

    @pytest.mark.anyio
    async def test_executor_uses_cache(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.sqlite"
        cache = CommandCache(db_path)
        executor = AsyncBatchExecutor(cache, concurrency=10)

        commands = [CommandInput(cmd="ls", repo=str(tmp_path))]

        await executor.run(iter(commands))

        second_commands = [CommandInput(cmd="ls", repo=str(tmp_path))]
        batch_result = await executor.run(iter(second_commands))

        assert batch_result.cached == 1
        assert batch_result.executed == 0

        cache.close()


class TestInspector:
    def test_inspector_summary(self, tmp_path: Path) -> None:
        results_file = tmp_path / "results.jsonl"
        results = [
            CommandResult("ls", "/tmp", "out", "", 0, 10.0, False),
            CommandResult("grep foo", "/tmp", "", "err", 1, 20.0, False),
            CommandResult("ls", "/tmp", "cached", "", 0, 0.0, True),
        ]

        with open(results_file, "w") as f:
            for r in results:
                f.write(r.to_json() + "\n")

        inspector = ResultsInspector(results_file)
        summary = inspector.summary()

        assert summary.total == 3
        assert summary.success == 2
        assert summary.failed == 1
        assert summary.cached == 1

    def test_inspector_filter_by_status(self, tmp_path: Path) -> None:
        results_file = tmp_path / "results.jsonl"
        results = [
            CommandResult("ls", "/tmp", "out", "", 0, 10.0, False),
            CommandResult("grep foo", "/tmp", "", "err", 1, 20.0, False),
        ]

        with open(results_file, "w") as f:
            for r in results:
                f.write(r.to_json() + "\n")

        inspector = ResultsInspector(results_file)
        failed = inspector.filter_by_status("failed")

        assert len(failed) == 1
        assert failed[0].cmd == "grep foo"

    def test_format_result(self) -> None:
        result = CommandResult(
            cmd="ls -la",
            repo="/tmp/test",
            stdout="file1\nfile2",
            stderr="",
            return_code=0,
            duration_ms=15.5,
            cached=False,
        )

        formatted = format_result(result, 0)

        assert "ls -la" in formatted
        assert "/tmp/test" in formatted
        assert "file1" in formatted
        assert "15.5ms" in formatted
