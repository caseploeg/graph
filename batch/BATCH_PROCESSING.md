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
| `--generate-questions` | Generate questions after processing |
| `--questions-dir` | Directory for question JSONL files |
| `--target-questions` | Target questions per repo (default: 10000) |
| `--min-questions` | Minimum candidates to generate (default: 10) |

## Question Generation

Generate diverse evaluation questions alongside graphs:

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./graphs \
    --generate-questions \
    --target-questions 10000
```

### How It Works

1. Each question uses a unique "seed" node (Function/Method with connections)
2. Max questions per repo = number of candidate seed nodes
3. Repos with fewer than `--min-questions` candidates are skipped
4. Questions use various expansion strategies (callees, callers, chain, file, bfs)

### Standalone Question Generation

If you already have graphs, generate questions separately:

```bash
uv run python batch/batch_question_generator.py \
    --graphs-dir ./graphs \
    --clones-dir ./clones \
    --questions-dir ./questions \
    --target-per-repo 10000
```

### Question Output Format

Each repo produces `{repo}_questions.jsonl` with records like:

```json
{
  "prompt_id": "react_0001",
  "repo_name": "react",
  "primary_language": "javascript",
  "expansion_strategy": "callees",
  "seed_node_id": 42,
  "seed_node_name": "useState",
  "context_node_ids": [42, 55, 67],
  "file_paths": ["src/hooks.js"],
  "prompt_text": "..."
}
```

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
