# E2E Regression Tests for Graph Generation

End-to-end tests that ensure graph generation produces consistent results across code changes. Essential for validating optimizations don't break correctness.

## Overview

The e2e test suite:
1. Clones real GitHub repositories at pinned versions
2. Processes them through the graph generator
3. Compares output against committed baseline JSON files
4. Reports detailed diffs on mismatches

## Directory Structure

```
tests/e2e/
├── __init__.py
├── conftest.py              # Session-scoped fixtures for cloning/processing
├── graph_compare.py         # Comparison utilities with diff reporting
├── test_graph_regression.py # 15 regression tests
├── regenerate_baselines.py  # Script to regenerate baseline files
└── baselines/
    ├── click.json           # pallets/click @ 8.1.8
    └── log.json             # rust-lang/log @ 0.4.27
```

## Test Repositories

| Repo | Tag | Language | Nodes | Relationships | Purpose |
|------|-----|----------|-------|---------------|---------|
| `pallets/click` | 8.1.8 | Python | ~1592 | ~7322 | Decorators, CLI patterns, classes |
| `rust-lang/log` | 0.4.27 | Rust | ~46 | ~49 | Traits, impl blocks, macros |

Repos are pinned to specific tags to ensure deterministic line numbers.

## Running Tests

```bash
# Run all e2e tests
uv run python -m pytest tests/e2e -v -m e2e

# Run a specific test
uv run python -m pytest tests/e2e -v -m e2e -k "test_click"

# Run with verbose diff output
uv run python -m pytest tests/e2e -v --tb=long -m e2e
```

## Test Categories

### 1. Regression Tests (`TestGraphGenerationRegression`)
Compare full graph output against baselines:
- `test_click_graph_matches_baseline` - Python repo
- `test_log_graph_matches_baseline` - Rust repo

### 2. Metadata Tests (`TestGraphMetadata`)
Verify node and relationship counts are within tolerance:
- `test_click_node_count`
- `test_click_relationship_count`
- `test_log_node_count`
- `test_log_relationship_count`

### 3. Structure Tests (`TestGraphStructure`)
Verify expected node types exist:
- `test_click_has_project_node`
- `test_click_has_modules`
- `test_click_has_functions`
- `test_click_has_classes`
- `test_click_has_calls_relationships`

### 4. Diff Reporting Tests (`TestDiffReporting`)
Verify the comparison utilities work correctly.

## Handling Non-Determinism

The graph generator has some non-deterministic behavior in:
- Line number extraction for decorated functions
- Call resolution order

To handle this, the tests:

1. **Ignore volatile properties**: `start_line`, `end_line`, `decorators`, `docstring`
2. **Allow count tolerance**: ±5 nodes/relationships difference

These can be configured in `test_graph_regression.py`:

```python
VOLATILE_PROPERTIES = {
    "start_line",
    "end_line",
    "decorators",
    "docstring",
}

COUNT_TOLERANCE = 5
```

## Regenerating Baselines

When you intentionally change the graph output format:

```bash
# Regenerate all baseline files
uv run python tests/e2e/regenerate_baselines.py

# Review changes
git diff tests/e2e/baselines/

# Commit if correct
git add tests/e2e/baselines/
git commit -m "Update e2e baselines for <reason>"
```

The script:
1. Clones repos at pinned tags
2. Processes them with current implementation
3. Saves JSON to `tests/e2e/baselines/`

## Graph Comparison API

### `compare_graphs(actual, expected) -> GraphDiff`

Returns a `GraphDiff` object with:
- `node_count_diff` - Tuple of (expected, actual) if different
- `rel_count_diff` - Tuple of (expected, actual) if different
- `missing_nodes` - Node signatures in expected but not actual
- `extra_nodes` - Node signatures in actual but not expected
- `missing_rels` - Relationship signatures missing
- `extra_rels` - Relationship signatures extra
- `node_property_diffs` - List of property differences

### `assert_graphs_equal(actual, expected, **kwargs)`

Raises `AssertionError` with detailed diff on mismatch.

Options:
- `ignore_properties: set[str]` - Property names to skip
- `count_tolerance: int` - Allow this much count difference (default: 5)

## CI Integration

The tests run automatically on PRs via `.github/workflows/test-e2e.yml`:

```yaml
on:
  pull_request:
    paths:
      - 'codebase_rag/**'
      - 'batch/**'
      - 'tests/e2e/**'
```

## Adding New Test Repos

1. Add to `TEST_REPOS` in `conftest.py`:
```python
TEST_REPOS = {
    "click": {"url": "...", "commit": "8.1.8", ...},
    "log": {"url": "...", "commit": "0.4.27", ...},
    # Add new repo here
    "myrepo": {
        "url": "https://github.com/org/repo",
        "commit": "v1.0.0",  # Use a tag for stability
        "language": "python",
        "description": "Description",
    },
}
```

2. Add to `REPOS` in `regenerate_baselines.py`:
```python
REPOS = {
    "click": {"url": "...", "commit": "8.1.8"},
    "log": {"url": "...", "commit": "0.4.27"},
    "myrepo": {"url": "...", "commit": "v1.0.0"},
}
```

3. Regenerate baselines:
```bash
uv run python tests/e2e/regenerate_baselines.py
```

4. Add tests in `test_graph_regression.py` if needed.

## Troubleshooting

### Tests fail with "baseline not found"
Run `regenerate_baselines.py` to create baseline files.

### Clone fails with "tag not found"
The pinned tag may have been deleted. Update `TEST_REPOS` with a valid tag.

### Large diff in relationship count
Check for non-determinism issues. If the diff is significant (>5), investigate the cause rather than increasing tolerance.

### Tests pass locally but fail in CI
Ensure baselines are committed. CI doesn't regenerate them.
