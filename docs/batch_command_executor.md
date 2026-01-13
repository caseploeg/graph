# Batch Command Executor

Execute millions of read-only bash commands on cloned repos with strict allowlist, SQLite caching, and performance tracking.

## Performance Results

Tested with 10,000 commands on Flask repository:

| Metric | First Run | Cached Run |
|--------|-----------|------------|
| Commands/sec | 4,155 | 35,446 |
| Total time | 2.41s | 0.28s |
| Cache hit rate | 94.9% | 100% |

## Quick Start

```bash
# Generate test commands
uv run python -m batch.command_executor.cli generate-test /path/to/repo --count 10000 > commands.jsonl

# Run batch execution
uv run python -m batch.command_executor.cli run commands.jsonl \
    --output results.jsonl \
    --cache-db cache.sqlite \
    --concurrency 50

# Inspect results
uv run python -m batch.command_executor.cli inspect results.jsonl --summary
uv run python -m batch.command_executor.cli inspect results.jsonl --sample 5
uv run python -m batch.command_executor.cli inspect results.jsonl --status failed
```

## Command Allowlist

### Primary Commands (can start a pipeline)
- `ls`, `find`, `tree`, `stat`, `file`
- `grep`, `rg`, `ag`, `ack`
- `head`, `tail`, `wc`, `cat`
- `git` (with subcommand restrictions)

### Safe Git Subcommands
`status`, `log`, `diff`, `show`, `branch`, `tag`, `ls-files`, `ls-tree`, `rev-parse`, `describe`, `rev-list`, `cat-file`, `name-rev`, `shortlog`, `blame`

### Safe Pipe Sinks (allowed after `|`)
`head`, `tail`, `wc`, `less`, `sort`, `uniq`, `grep`, `cut`, `awk`, `sed`

### Always Blocked
- Redirects: `>`, `>>`, `<`
- Chains: `&&`, `||`, `;`
- Subshells: `$(...)`, backticks
- Background: `&`
- Dangerous commands: `rm`, `chmod`, `chown`, `sudo`, etc.

## Input Format (JSONL)

```json
{"cmd": "grep -r 'def ' . --include='*.py' | head -20", "repo": "/path/to/repo"}
{"cmd": "git log --oneline -10", "repo": "/path/to/repo"}
```

## Output Format (JSONL)

```json
{
  "cmd": "ls -la",
  "repo": "/path/to/repo",
  "stdout": "file1\nfile2",
  "stderr": "",
  "return_code": 0,
  "duration_ms": 12.5,
  "cached": false
}
```

## CLI Commands

### `run` - Execute batch commands
```bash
uv run python -m batch.command_executor.cli run commands.jsonl \
    --output results.jsonl \
    --cache-db cache.sqlite \
    --concurrency 100 \
    --timeout 30
```

### `inspect` - Query and sample results
```bash
# Summary statistics
uv run python -m batch.command_executor.cli inspect results.jsonl --summary

# Random sample
uv run python -m batch.command_executor.cli inspect results.jsonl --sample 10

# Filter by status
uv run python -m batch.command_executor.cli inspect results.jsonl --status failed

# Filter by repo
uv run python -m batch.command_executor.cli inspect results.jsonl --repo /path/to/repo

# Filter by command pattern (regex)
uv run python -m batch.command_executor.cli inspect results.jsonl --cmd-pattern "grep.*TODO"

# Specific result by index
uv run python -m batch.command_executor.cli inspect results.jsonl --index 42
```

### `stats` - Show cache statistics
```bash
uv run python -m batch.command_executor.cli stats --cache-db cache.sqlite
```

### `clear-cache` - Clear the cache
```bash
uv run python -m batch.command_executor.cli clear-cache --cache-db cache.sqlite
```

### `generate-test` - Generate test commands
```bash
uv run python -m batch.command_executor.cli generate-test /path/to/repo --count 10000 > commands.jsonl
```

## Architecture

```
commands.jsonl → AsyncBatchExecutor → SQLite Cache
                        ↓
                SafeCommandRunner (allowlist validation)
                        ↓
                results.jsonl + metrics
```

### Key Files
- `batch/command_executor/allowlist.py` - Command validation with pipe rules
- `batch/command_executor/cache.py` - SQLite result cache
- `batch/command_executor/runner.py` - Safe async command execution
- `batch/command_executor/executor_async.py` - Batch executor with concurrency
- `batch/command_executor/inspector.py` - Results query and inspection
- `batch/command_executor/cli.py` - CLI entrypoint

## Extending the Allowlist

Edit `batch/command_executor/allowlist.py`:

```python
# Add to primary allowlist
READONLY_ALLOWLIST = frozenset({
    ...,
    "your_command",
})

# Add to pipe sinks
SAFE_PIPE_SINKS = frozenset({
    ...,
    "your_sink",
})

# Add git subcommand
SAFE_GIT_SUBCOMMANDS = frozenset({
    ...,
    "your_subcommand",
})
```

## Tests

```bash
uv run pytest batch/tests/test_command_executor.py -v
```
