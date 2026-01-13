from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from .allowlist import validate_command
from .schemas import CommandResult


class SafeCommandRunner:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def _execute_pipeline(
        self, segments: list[str], cwd: Path
    ) -> tuple[int, bytes, bytes]:
        start_time = time.monotonic()
        input_data: bytes | None = None
        all_stderr: list[bytes] = []
        last_return_code = 0

        for segment in segments:
            elapsed = time.monotonic() - start_time
            remaining_timeout = self.timeout - elapsed
            if remaining_timeout <= 0:
                raise TimeoutError("Command timed out")

            cmd_parts = shlex.split(segment)
            proc = await asyncio.create_subprocess_exec(
                cmd_parts[0],
                *cmd_parts[1:],
                stdin=asyncio.subprocess.PIPE if input_data is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=input_data), timeout=remaining_timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise

            last_return_code = proc.returncode if proc.returncode is not None else 1
            input_data = stdout

            if stderr:
                all_stderr.append(stderr)

        return last_return_code, input_data or b"", b"".join(all_stderr)

    def _split_pipeline(self, command: str) -> list[str]:
        segments: list[str] = []
        current: list[str] = []
        in_single = False
        in_double = False
        i = 0

        while i < len(command):
            char = command[i]
            if char == "\\" and i + 1 < len(command):
                current.append(char)
                current.append(command[i + 1])
                i += 2
                continue
            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
            elif char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
            elif char == "|" and not in_single and not in_double:
                if i + 1 < len(command) and command[i + 1] == "|":
                    current.append(char)
                else:
                    segments.append("".join(current).strip())
                    current = []
            else:
                current.append(char)
            i += 1

        if current:
            segments.append("".join(current).strip())

        return [s for s in segments if s]

    async def execute(self, cmd: str, cwd: Path) -> CommandResult:
        start_time = time.perf_counter()

        validation = validate_command(cmd)
        if not validation.valid:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return CommandResult(
                cmd=cmd,
                repo=str(cwd),
                stdout="",
                stderr=validation.error or "Validation failed",
                return_code=1,
                duration_ms=duration_ms,
                cached=False,
            )

        try:
            segments = self._split_pipeline(cmd)
            return_code, stdout_bytes, stderr_bytes = await self._execute_pipeline(
                segments, cwd
            )

            duration_ms = (time.perf_counter() - start_time) * 1000
            return CommandResult(
                cmd=cmd,
                repo=str(cwd),
                stdout=stdout_bytes.decode("utf-8", errors="replace").strip(),
                stderr=stderr_bytes.decode("utf-8", errors="replace").strip(),
                return_code=return_code,
                duration_ms=duration_ms,
                cached=False,
            )
        except TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return CommandResult(
                cmd=cmd,
                repo=str(cwd),
                stdout="",
                stderr=f"Command timed out after {self.timeout}s",
                return_code=124,
                duration_ms=duration_ms,
                cached=False,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return CommandResult(
                cmd=cmd,
                repo=str(cwd),
                stdout="",
                stderr=str(e),
                return_code=1,
                duration_ms=duration_ms,
                cached=False,
            )
