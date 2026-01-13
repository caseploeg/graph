from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum


class ValidationError(StrEnum):
    NOT_IN_ALLOWLIST = "Command '{cmd}' not in allowlist"
    PIPE_SINK_NOT_ALLOWED = "Command '{cmd}' not allowed after pipe. Allowed: {allowed}"
    GIT_SUBCOMMAND_NOT_ALLOWED = "Git subcommand '{sub}' not allowed. Allowed: {allowed}"
    REDIRECT_NOT_ALLOWED = "Redirects (>, >>, <) are not allowed"
    CHAIN_NOT_ALLOWED = "Command chains (&&, ||, ;) are not allowed"
    SUBSHELL_NOT_ALLOWED = "Subshells ($(...), `...`) are not allowed"
    BACKGROUND_NOT_ALLOWED = "Background execution (&) is not allowed"
    PARSE_ERROR = "Failed to parse command: {error}"


READONLY_ALLOWLIST = frozenset({
    "ls",
    "find",
    "tree",
    "stat",
    "file",
    "grep",
    "rg",
    "ag",
    "ack",
    "head",
    "tail",
    "wc",
    "cat",
    "git",
})

SAFE_PIPE_SINKS = frozenset({
    "head",
    "tail",
    "wc",
    "less",
    "sort",
    "uniq",
    "grep",
    "cut",
    "awk",
    "sed",
})

SAFE_GIT_SUBCOMMANDS = frozenset({
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "tag",
    "ls-files",
    "ls-tree",
    "rev-parse",
    "describe",
    "rev-list",
    "cat-file",
    "name-rev",
    "shortlog",
    "blame",
})

DANGEROUS_PATTERNS = (
    (re.compile(r"[>]"), ValidationError.REDIRECT_NOT_ALLOWED),
    (re.compile(r"<(?![<])"), ValidationError.REDIRECT_NOT_ALLOWED),
    (re.compile(r"&&"), ValidationError.CHAIN_NOT_ALLOWED),
    (re.compile(r"\|\|"), ValidationError.CHAIN_NOT_ALLOWED),
    (re.compile(r";"), ValidationError.CHAIN_NOT_ALLOWED),
    (re.compile(r"\$\("), ValidationError.SUBSHELL_NOT_ALLOWED),
    (re.compile(r"`"), ValidationError.SUBSHELL_NOT_ALLOWED),
    (re.compile(r"&(?!&)"), ValidationError.BACKGROUND_NOT_ALLOWED),
)


@dataclass
class ValidationResult:
    valid: bool
    error: str | None = None


def _is_outside_quotes(command: str, pos: int) -> bool:
    in_single = False
    in_double = False
    i = 0
    while i < pos:
        char = command[i]
        if char == "\\" and i + 1 < len(command):
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        i += 1
    return not in_single and not in_double


def _check_dangerous_patterns(command: str) -> str | None:
    for pattern, error in DANGEROUS_PATTERNS:
        for match in pattern.finditer(command):
            if _is_outside_quotes(command, match.start()):
                return error
    return None


def _split_pipeline(command: str) -> list[str]:
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


def _validate_segment(segment: str, is_first: bool) -> str | None:
    try:
        parts = shlex.split(segment)
    except ValueError as e:
        return ValidationError.PARSE_ERROR.format(error=str(e))

    if not parts:
        return None

    base_cmd = parts[0]

    if is_first:
        if base_cmd not in READONLY_ALLOWLIST:
            return ValidationError.NOT_IN_ALLOWLIST.format(cmd=base_cmd)
    else:
        if base_cmd not in SAFE_PIPE_SINKS:
            allowed = ", ".join(sorted(SAFE_PIPE_SINKS))
            return ValidationError.PIPE_SINK_NOT_ALLOWED.format(
                cmd=base_cmd, allowed=allowed
            )

    if base_cmd == "git" and is_first:
        if len(parts) < 2:
            return None
        subcommand = parts[1]
        if subcommand.startswith("-"):
            return None
        if subcommand not in SAFE_GIT_SUBCOMMANDS:
            allowed = ", ".join(sorted(SAFE_GIT_SUBCOMMANDS))
            return ValidationError.GIT_SUBCOMMAND_NOT_ALLOWED.format(
                sub=subcommand, allowed=allowed
            )

    return None


def validate_command(command: str) -> ValidationResult:
    if not command or not command.strip():
        return ValidationResult(valid=False, error="Empty command")

    command = command.strip()

    if error := _check_dangerous_patterns(command):
        return ValidationResult(valid=False, error=error)

    segments = _split_pipeline(command)
    if not segments:
        return ValidationResult(valid=False, error="Empty command")

    for i, segment in enumerate(segments):
        is_first = i == 0
        if error := _validate_segment(segment, is_first):
            return ValidationResult(valid=False, error=error)

    return ValidationResult(valid=True)
