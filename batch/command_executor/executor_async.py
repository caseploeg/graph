from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator

from .cache import CachedResult, CommandCache
from .runner import SafeCommandRunner
from .schemas import BatchResult, CommandInput, CommandResult


class AsyncBatchExecutor:
    def __init__(
        self,
        cache: CommandCache,
        runner: SafeCommandRunner | None = None,
        concurrency: int = 100,
    ):
        self.cache = cache
        self.runner = runner or SafeCommandRunner()
        self.concurrency = concurrency
        self._semaphore: asyncio.Semaphore | None = None

    async def _execute_one(
        self,
        cmd_input: CommandInput,
        on_result: Callable[[CommandResult], None] | None = None,
    ) -> CommandResult:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)

        async with self._semaphore:
            cached = self.cache.get(cmd_input.cmd, cmd_input.repo)
            if cached:
                result = CommandResult(
                    cmd=cmd_input.cmd,
                    repo=cmd_input.repo,
                    stdout=cached.stdout,
                    stderr=cached.stderr,
                    return_code=cached.return_code,
                    duration_ms=0.0,
                    cached=True,
                )
                if on_result:
                    on_result(result)
                return result

            result = await self.runner.execute(cmd_input.cmd, Path(cmd_input.repo))
            self.cache.set(cmd_input.cmd, cmd_input.repo, result)

            if on_result:
                on_result(result)
            return result

    async def run(
        self,
        commands: Iterator[CommandInput],
        on_result: Callable[[CommandResult], None] | None = None,
    ) -> BatchResult:
        start_time = time.perf_counter()

        tasks: list[asyncio.Task[CommandResult]] = []
        for cmd_input in commands:
            task = asyncio.create_task(self._execute_one(cmd_input, on_result))
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        total_duration = time.perf_counter() - start_time
        executed = sum(1 for r in results if not r.cached)
        cached = sum(1 for r in results if r.cached)
        failed = sum(1 for r in results if r.return_code != 0)
        exec_times = [r.duration_ms for r in results if not r.cached]
        avg_exec = sum(exec_times) / len(exec_times) if exec_times else 0.0

        return BatchResult(
            total_commands=len(results),
            executed=executed,
            cached=cached,
            failed=failed,
            total_duration_s=total_duration,
            avg_execution_ms=avg_exec,
        )

    async def run_streaming(
        self,
        commands: Iterator[CommandInput],
    ) -> AsyncIterator[CommandResult]:
        self._semaphore = asyncio.Semaphore(self.concurrency)

        async def process(cmd_input: CommandInput) -> CommandResult:
            return await self._execute_one(cmd_input)

        tasks: dict[asyncio.Task[CommandResult], CommandInput] = {}

        for cmd_input in commands:
            task = asyncio.create_task(process(cmd_input))
            tasks[task] = cmd_input

        while tasks:
            done, _ = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                yield task.result()
                del tasks[task]
