# Plan: Questions Phase Rich UI

## Summary

Add Rich-based terminal UI to the questions phase of batch processing, following the existing `BatchProgressUI` pattern. Shows aggregate stats (questions generated, timeouts, strategy distribution) and exports logs in both JSONL and plain text for replay.

## Data Flow

```
generate_diverse_prompts()  →  generate_questions_for_repo()  →  batch_generate_questions()
     ↓                              ↓                                    ↓
Returns: (prompts, gen_stats)  Returns: dict + gen_stats       Aggregates into QuestionsStats
                                                                        ↓
                                                               QuestionsProgressUI (Live)
                                                                        ↓
                                                         _questions_log.jsonl + .txt
```

## Files to Create/Modify

### 1. NEW: `batch/questions_rich_ui.py`

Contains:
- `GenerationStats` - Per-repo stats (timeout_count, strategy_counts, attempt_count, duration)
- `QuestionsStats` - Aggregate stats with computed properties (questions/min, ETA)
- `QuestionsActivityLogEntry` - Recent activity display
- `QuestionsLogExporter` - Writes both JSONL and TXT log files
- `QuestionsProgressUI` - Rich Live dashboard

**Dashboard layout:**
```
┌─────────────── Code Graph RAG Questions Generator ───────────────┐
│ Phase: Generating   Current: pallets/click                       │
│                                                                  │
│ Progress: [=====>                    ] 50/100 repos  5m ETA      │
│                                                                  │
│ Questions:    45,234    Repos/min:     8.3                       │
│ Timeouts:         12    Questions/min: 2,500                     │
│ Skipped:           3    Avg/repo:      904                       │
│                                                                  │
│ Strategy Distribution:                                           │
│   callees  [████████████████    ] 40%  18,093                    │
│   chain    [██████████          ] 25%  11,308                    │
│   file     [████████            ] 20%   9,046                    │
│                                                                  │
│ Currently Processing (8 workers):                                │
│   Worker 1: django/django                                        │
│   Worker 2: pallets/flask                                        │
│                                                                  │
│ Recent Activity:                                                 │
│   [OK]   pallets/click - 9,847 questions (45.2s)                 │
│   [SKIP] tiny/repo - Too few candidates                          │
└──────────────────────────────────────────────────────────────────┘
```

### 2. MODIFY: `batch/generate_diverse_questions.py`

- Add timing (`start_time = time.time()` at function start)
- Build `GenerationStats` at end with: timeout_count, strategy_counts, attempt_count, unique_seeds, duration
- Change return type: `list[DiversePromptRecord]` → `tuple[list[DiversePromptRecord], GenerationStats]`
- Update `main()` to handle tuple return

### 3. MODIFY: `batch/batch_question_generator.py`

- `generate_questions_for_repo()`: Capture gen_stats from upstream, add to return dict
- `batch_generate_questions()`: Accept `ui: QuestionsProgressUI | None` parameter
- Main loop: Call `ui.update_repo_complete(result, gen_stats)` when UI present
- Preserve legacy print output when `ui=None`

### 4. MODIFY: `batch/large_scale_processor.py`

- `questions_phase()`: Create `QuestionsLogExporter` and `QuestionsProgressUI`
- Wrap `batch_generate_questions()` in `ui.live_context()`
- Handle `--questions-verbose` (disable UI, use text output)
- Call `log_exporter.finish()` and `ui.print_summary()` at end

## Log Export Format

**JSONL (`_questions_log.jsonl`):**
```json
{"event": "batch_start", "timestamp": "...", "config": {...}}
{"event": "repo_complete", "repo": "owner/name", "questions": 9847, "timeouts": 3, "strategy_counts": {...}, "duration": 45.2}
{"event": "batch_complete", "summary": {...}}
```

**TXT (`_questions_log.txt`):**
```
[2024-01-11 12:00:00] BATCH START
  Total repos: 100, Target: 10000/repo, Workers: 8

[2024-01-11 12:01:30] OK: pallets/click
  Questions: 9,847 | Timeouts: 3 | Duration: 45.2s
  Strategy: callees(40%), chain(25%), file(20%), callers(10%), bfs(5%)

[2024-01-11 12:05:00] BATCH COMPLETE
  Total: 45,000 questions from 100 repos in 5m 0s
```

## Implementation Order

1. Create `batch/questions_rich_ui.py` with all dataclasses and classes
2. Modify `generate_diverse_questions.py` to return GenerationStats
3. Modify `batch_question_generator.py` to accept UI and pass stats through
4. Modify `large_scale_processor.py` to create and use UI
5. Test end-to-end

## Worker Communication

Workers return stats as dict (serializable across process boundary):
```python
{
    "repo": "owner/repo",
    "generated": 9847,
    "gen_stats": {
        "timeout_count": 3,
        "strategy_counts": {"callees": 3940, "chain": 2461, ...},
        "attempt_count": 10234,
        "duration_seconds": 45.2
    }
}
```

Main process reconstructs `GenerationStats.from_dict()` and merges into `QuestionsStats`.

## Verification

1. **Run batch question generation** with Rich UI enabled
2. **Check dashboard** shows live progress, strategy distribution, worker status
3. **Verify log files** created in questions output directory
4. **Test verbose mode** (`--questions-verbose`) disables UI, uses text output
5. **Review exported logs** - cat `_questions_log.txt` for human-readable replay
