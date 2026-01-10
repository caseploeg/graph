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
| `--questions-only` | Skip clone/process, only generate questions |
| `--questions-dir` | Directory for question JSONL files |
| `--target-questions` | Target questions per repo (default: 10000) |
| `--min-questions` | Minimum candidates to generate (default: 10) |
| `--question-workers` | Workers for parallel question generation |

## Question Generation

Generate diverse evaluation questions for processed repos. Question generation runs in parallel across repos for maximum throughput.

### Full Pipeline with Questions

Generate graphs and questions in one run:

```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./graphs \
    --generate-questions \
    --question-workers 8 \
    --target-questions 10000
```

### Questions-Only Mode

If you already have graphs and clones, skip straight to question generation:

```bash
uv run python batch/large_scale_processor.py \
    --questions-only \
    --clone-dir ./clones \
    --output-dir ./graphs \
    --questions-dir ./questions \
    --question-workers 8 \
    --repo-list repos.json
```

This is useful for:
- Re-generating questions with different parameters
- Running question generation after a previous batch run
- Testing question generation without re-processing repos

### Standalone Question Generator

Use the standalone script for maximum flexibility:

```bash
uv run python batch/batch_question_generator.py \
    --graphs-dir ./graphs \
    --clones-dir ./clones \
    --questions-dir ./questions \
    --target-per-repo 10000 \
    --workers 8
```

### Parallel Processing

Question generation processes multiple repos in parallel using ProcessPoolExecutor:

- Default workers: `cpu_count - 2`
- Override with `--question-workers` (large_scale_processor) or `--workers` (batch_question_generator)
- Each worker processes one repo at a time
- Progress is shown as repos complete

### How It Works

1. Each question uses a unique "seed" node (Function/Method with connections)
2. Max questions per repo = number of candidate seed nodes
3. Repos with fewer than `--min-questions` candidates are skipped
4. Questions use various expansion strategies (callees, callers, chain, file, bfs)
5. Each prompt has a timeout (default 30s) - slow seeds are skipped to prevent blocking

### Question Generation Options

| Flag | Description |
|------|-------------|
| `--generate-questions` | Enable question generation (full pipeline) |
| `--questions-only` | Skip clone/process, only generate questions |
| `--questions-dir` | Output directory for JSONL files |
| `--target-questions` | Target questions per repo (default: 10000) |
| `--min-questions` | Minimum candidates required (default: 10) |
| `--question-workers` | Parallel workers for question generation |
| `--timeout` | Timeout in seconds per prompt generation (default: 30) |

### Question Output Structure

Questions are written as one JSONL file per repo:

```
questions/
├── react_questions.jsonl      # One file per repo
├── express_questions.jsonl
├── ruff_questions.jsonl
├── svelte_questions.jsonl
├── ...
└── _questions_summary.json    # Aggregate stats
```

### Question Record Format

Each line in a JSONL file is one question:

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

### Questions Summary

The `_questions_summary.json` contains aggregate stats:

```json
{
  "total_repos": 100,
  "successful": 85,
  "skipped": 15,
  "total_questions": 523000,
  "target_per_repo": 10000,
  "results": [...]
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
