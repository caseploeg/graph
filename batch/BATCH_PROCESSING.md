# Batch Processing Guide

Process 100+ GitHub repositories at scale with parallel graph generation.

## Quick Start

### 1. Build Repo List

Search GitHub for MIT-licensed repos:

```bash
uv run python batch/repo_discovery.py \
    --output repos.json \
    --languages python,javascript,typescript,rust,java,cpp,lua \
    --min-stars 1000 \
    --license mit
```

Or merge with an existing list:

```bash
uv run python batch/repo_discovery.py \
    --output repos.json \
    --merge-from scripts/mit_repos.txt \
    --fetch-metadata
```

### 2. Run Batch Processing

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./graphs
```

### 3. Monitor Progress

The rich terminal UI shows live progress:

```
+------------------------------------------------------------------+
| Code Graph RAG Batch Processor                                    |
+------------------------------------------------------------------+
| Phase: Processing | Current: facebook/react                      |
+------------------------------------------------------------------+
| Clone:   [========================================] 100/100       |
| Process: [================                        ] 42/100        |
+------------------------------------------------------------------+
| Successful: 38    | Repos/min: 2.4                               |
| Failed:      4    | Nodes/sec: 156.2                             |
| Nodes:    12,456  | ETA: 24m 15s                                 |
| Rels:     28,901  | Elapsed: 17m 32s                             |
+------------------------------------------------------------------+
```

## Repo Discovery

### Search GitHub

Search for repos by language with star thresholds:

```bash
uv run python batch/repo_discovery.py \
    --output repos.json \
    --languages python,javascript,typescript,rust \
    --min-stars 1000 \
    --license mit \
    --limit-per-language 100
```

### Merge Multiple Sources

Combine GitHub search with existing lists:

```bash
uv run python batch/repo_discovery.py \
    --output repos.json \
    --merge-from scripts/mit_repos.txt \
    --merge-from other_repos.txt \
    --languages python,javascript
```

### Discovery Options

| Flag | Description |
|------|-------------|
| `--output` | Output JSON file path |
| `--languages` | Comma-separated languages (default: all supported) |
| `--min-stars` | Minimum star count (default: per-language thresholds) |
| `--max-size-kb` | Maximum repo size in KB (default: 500000) |
| `--license` | License type (default: mit) |
| `--limit-per-language` | Max repos per language from search (default: 100) |
| `--merge-from` | Merge with existing txt file (can use multiple) |
| `--fetch-metadata` | Fetch metadata for repos from txt files |
| `--filter-supported` | Only include repos with supported languages |
| `--skip-search` | Skip GitHub search, only process merge files |

## Processing Options

### Full Pipeline

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./output \
    --workers 6 \
    --upload-to gs://bucket/prefix
```

### Resume After Interruption

Processing automatically saves state. To resume:

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./output \
    --resume
```

### Skip Cloning

If repos are already cloned:

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./output \
    --skip-clone
```

### Filter by Language

Process only specific languages:

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./output \
    --languages python,rust
```

### Processing Options Table

| Flag | Description |
|------|-------------|
| `--repo-list` | Path to repos.json |
| `--clone-dir` | Directory to clone repos into |
| `--output-dir` | Directory for graph JSON output |
| `--workers` | Parallel workers (default: cpu_count - 2) |
| `--limit` | Process only first N repos |
| `--languages` | Filter to specific languages |
| `--resume` | Resume from previous state (default: True) |
| `--no-resume` | Start fresh, don't resume |
| `--skip-clone` | Skip cloning, use existing clones |
| `--upload-to` | Upload to GCS or local path |
| `--shallow` | Use shallow clones (default: True) |
| `--max-retries` | Max clone retries per repo (default: 3) |

## Supported Languages

Only repos with these languages are processed:

- Python
- JavaScript
- TypeScript
- Rust
- C++
- Java
- Lua

Languages in development (not processed): Go, Scala, C#, PHP

## Output Files

### Per-Repo Graph

Each processed repo creates `output/<repo-name>.json`:

```json
{
  "metadata": {
    "repo_path": "/path/to/repo",
    "total_nodes": 1234,
    "total_relationships": 5678
  },
  "nodes": [...],
  "relationships": [...]
}
```

### Batch Summary

`output/_batch_summary.json` contains aggregate stats:

```json
{
  "timestamp": "2026-01-10T15:30:00Z",
  "total_repos": 100,
  "processed": 95,
  "process_failed": 5,
  "total_nodes": 123456,
  "total_relationships": 456789,
  "total_time_seconds": 3600,
  "workers": 6
}
```

### Clone State

`.clone_state.json` in clone directory tracks progress:

```json
{
  "completed": ["https://github.com/facebook/react", ...],
  "failed": {"https://github.com/some/repo": 3},
  "in_progress": null
}
```

## Error Handling

| Error | Handling |
|-------|----------|
| GitHub search rate limit | Pause 60s, retry |
| Clone rate limit (403) | Pause 60s, retry |
| Clone not found (404) | Skip permanently |
| Clone timeout | Retry up to 3x |
| Process parse error | Log, continue to next repo |
| Memory error | Skip oversized repos |

## CPU Optimization

By default, uses `cpu_count - 2` workers to leave headroom for:

- Sequential clone operations (I/O-bound)
- OS and Rich UI updates
- System responsiveness

Override with `--workers N` for custom parallelism.

## Tips

### For Large Runs (100+ repos)

1. Start with `--limit 10` to test
2. Use `--shallow` (default) for faster clones
3. Filter to specific languages to reduce scope
4. Use `--upload-to` to persist results

### For Debugging

1. Check `.clone_state.json` for clone failures
2. Check `_batch_summary.json` for process failures
3. Individual repo errors are in the summary JSON

### For Re-runs

1. Delete `.clone_state.json` to re-clone all
2. Use `--no-resume` to start fresh
3. Use `--skip-clone` to only re-process
