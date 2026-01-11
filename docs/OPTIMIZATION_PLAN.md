# Graph Generation Speed Optimization Plan

Optimizing the large batch processor pipeline for JSON export.

## Current State

**What's already parallelized:** Multi-repo processing via `ProcessPoolExecutor` in `large_scale_processor.py`

**What's sequential (the bottlenecks):** Within each worker, `GraphUpdater.run()` processes files one at a time.

```
large_scale_processor.py
    └── process_phase() [ProcessPoolExecutor, N workers]
        └── process_single_repo() [per worker]
            └── GraphUpdater.run()
                ├── Phase 1: identify_structure()      ← rglob #1
                ├── Phase 2: _process_files()          ← SEQUENTIAL
                ├── Phase 3: _process_function_calls() ← SEQUENTIAL
                └── flush_all() → JSON write
```

**Key insight:** If one repo is huge, it becomes the bottleneck since it's processed sequentially.

## Identified Bottlenecks

| # | Bottleneck | Location | Impact |
|---|-----------|----------|--------|
| 1 | Sequential file processing | `graph_updater.py:319-347` | HIGH |
| 2 | Sequential call resolution | `graph_updater.py:349-354` | HIGH |
| 3 | Multi-pass AST traversal (3x per file) | `call_processor.py:68-70` | MEDIUM-HIGH |
| 4 | Double rglob scans | `structure_processor.py`, `graph_updater.py:320` | MEDIUM |
| 5 | O(N) cache size calculation | `graph_updater.py:212-220` | LOW-MEDIUM |

## Optimization Strategy

### Priority 1: Parallel File Parsing

**Strategy:** Use `ThreadPoolExecutor` within each worker process.

Why threads instead of processes?
- Already inside a subprocess (from ProcessPoolExecutor)
- Tree-sitter releases GIL during parsing
- Avoids nested ProcessPoolExecutor complexity
- Shared memory for `function_registry` and `simple_name_lookup`

**Expected improvement:** 2-4x speedup on file parsing phase

### Priority 2: Single-Pass AST Traversal

**Strategy:** Collect all captures in one traversal instead of three.

Currently:
```python
self._process_calls_in_functions(root_node, ...)  # Pass 1
self._process_calls_in_classes(root_node, ...)    # Pass 2
self._process_module_level_calls(root_node, ...)  # Pass 3
```

**Expected improvement:** ~2-3x reduction in AST traversal time per file

### Priority 3: Shared File Enumeration

**Strategy:** Single `rglob` scan shared between structure and file processing.

**Expected improvement:** Eliminate duplicate filesystem traversal

### Priority 4: Incremental Cache Size Tracking

**Strategy:** Track size on insert/delete instead of O(N) calculation.

**Expected improvement:** O(1) per operation instead of O(N)

### Priority 5: Parallel Call Resolution

**Strategy:** Process call resolution in parallel threads after all files parsed.

**Expected improvement:** 1.5x speedup on call resolution phase

## Implementation Phases

### Phase 0: E2E Test Setup ✅
- [x] Create `tests/e2e/` directory structure
- [x] Implement comparison utilities
- [x] Implement regression tests
- [x] Generate baseline JSON files
- [x] Add CI workflow

### Phase 1: Quick Wins
- [ ] Shared file enumeration
- [ ] Incremental cache tracking
- [ ] Run e2e tests to verify correctness

### Phase 2: Core Parallelization
- [ ] Parallel file parsing with ThreadPoolExecutor
- [ ] Single-pass AST traversal
- [ ] Run e2e tests to verify correctness

### Phase 3: Further Optimization
- [ ] Parallel call resolution
- [ ] Benchmark and tune thread counts

## Files to Modify

| File | Changes |
|------|---------|
| `codebase_rag/graph_updater.py` | Parallel file parsing, cache tracking, shared enumeration |
| `codebase_rag/parsers/call_processor.py` | Single-pass traversal, cursor pooling |
| `codebase_rag/parsers/structure_processor.py` | Accept pre-enumerated files |
| `codebase_rag/utils/file_enumerator.py` | New shared enumerator class |
| `codebase_rag/config.py` | Thread count configuration |

## Configuration (Proposed)

```python
# Intra-repo parallelization (threads within each worker process)
FILE_PARSE_THREADS: int = 4
CALL_RESOLUTION_THREADS: int = 2
CACHE_SIZE_CHECK_INTERVAL: int = 100
```

## Expected Results

| Optimization | Est. Speedup |
|-------------|--------------|
| Parallel file parsing | 2-4x |
| Single-pass AST | 2-3x |
| Shared enumeration | 1.2x |
| Cache tracking | 1.1x |
| Parallel call resolution | 1.5x |

**Combined estimate:** 3-6x faster per-repo processing for large repositories.

## Verification

1. **Correctness:** E2E regression tests compare output against baselines
2. **Performance:** Benchmark single large repo and batch of 10 repos
3. **Existing tests:** Run full test suite to ensure no regressions
