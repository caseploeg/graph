# Question Generation Pipeline

This document explains the code path for generating questions from code graphs, including the sparse repo handling and debug logging features.

## Overview

The question generation pipeline processes code graph JSON files and generates training questions for code search evaluation. It supports both regular repos (with rich call graphs) and sparse repos (with fewer function calls but other relationships like imports and inheritance).

## Code Path

```
large_scale_processor.py (graph-generator-optimization/batch/)
│
├── CLI Arguments:
│   --generate-questions      Enable question generation
│   --questions-dir PATH      Output directory
│   --target-questions N      Questions per repo (default: 10000)
│   --min-questions N         Min candidates (default: 10)
│   --questions-debug         Show debug stats
│   --no-sparse-fallback      Disable sparse mode
│
├── LargeScaleConfig dataclass stores all config
│
└── LargeScaleBatchProcessor.run()
        │
        ├── 1. clone_phase()     - Clones repos to clone_dir
        ├── 2. process_phase()   - Generates graph JSON files to output_dir
        ├── 3. upload_phase()    - Optional GCS upload
        │
        └── 4. questions_phase() - Runs AFTER Rich UI closes
                │
                └── batch_generate_questions()  ← (more-queries/batch/)
                        │
                        ├── Finds all *.json graphs in output_dir
                        ├── Matches each graph to its cloned repo
                        │
                        └── For each repo (parallel via ProcessPoolExecutor):
                            │
                            └── generate_questions_for_repo()
                                    │
                                    ├── count_candidate_seeds(debug=True/False)
                                    │       │
                                    │       └── get_all_candidate_seeds()
                                    │           - Counts CALLS relationships
                                    │           - Requires 3+ connections per node
                                    │           - Tracks rejections if debug=True
                                    │
                                    ├── If too few candidates AND sparse_fallback=True:
                                    │       │
                                    │       └── count_candidate_seeds(sparse_mode=True)
                                    │               │
                                    │               └── get_sparse_candidate_seeds()
                                    │                   - Includes Module/Class nodes
                                    │                   - Counts IMPORTS, INHERITS, DEFINES
                                    │                   - Lower threshold (1+ connections)
                                    │
                                    └── generate_diverse_prompts()
                                            │
                                            └── Uses expansion strategies:
                                                - bfs, chain, callers, callees, file
                                                - imports, inheritance, definitions (sparse)
```

## Key Files

| File | Location | Purpose |
|------|----------|---------|
| `large_scale_processor.py` | `graph-generator-optimization/batch/` | Main entry point, orchestrates all phases |
| `batch_question_generator.py` | `more-queries/batch/` | Parallel question generation across repos |
| `question_generator.py` | `more-queries/batch/` | Candidate selection and expansion strategies |
| `generate_diverse_questions.py` | `more-queries/batch/` | LLM prompt generation with timeouts |
| `question_debug_stats.py` | `more-queries/batch/` | Debug statistics collection |
| `QUESTIONS.py` | `more-queries/` | Question categories, templates, and finders |

## Question Categories

### Original Categories (require CALLS relationships)
- **TRACE** - Call chain tracing across modules
- **IMPACT** - Blast radius analysis (what breaks if X changes)
- **BRIDGE** - Functions called from 3+ different modules
- **OVERRIDE** - Method override comparisons

### Sparse Repo Categories (use other relationships)
- **IMPORT** - Module import structure and chains
- **HIERARCHY** - Class inheritance hierarchies
- **DEFINITION** - "Where is X defined" questions
- **EXTERNAL** - External package dependencies

## Candidate Selection

### Regular Mode (`get_all_candidate_seeds`)
- Only considers Function and Method nodes
- Counts incoming + outgoing CALLS relationships
- Requires `min_connections` (default: 3)
- Used for repos with rich call graphs

### Sparse Mode (`get_sparse_candidate_seeds`)
- Includes Module and Class nodes
- Counts multiple relationship types:
  - CALLS (for functions/methods)
  - IMPORTS (for modules)
  - INHERITS (for classes)
  - DEFINES, DEFINES_METHOD
- Lower threshold: `min_connections` = 1
- Automatically used when regular mode has too few candidates

## Expansion Strategies

### Standard Strategies
| Strategy | Description | Depth |
|----------|-------------|-------|
| `bfs` | Breadth-first from seed | 2 hops, 25 nodes |
| `chain` | Deep call chain + siblings | 4 levels |
| `callers` | Upstream focus (who calls this) | 3 levels, 30 nodes |
| `callees` | Downstream focus (what this calls) | 4 levels, 30 nodes |
| `file` | All functions in same file | + 5 external |

### Sparse Strategies
| Strategy | Description | Use Case |
|----------|-------------|----------|
| `imports` | Follow IMPORTS relationships | Module structure questions |
| `inheritance` | Follow INHERITS + methods | Class hierarchy questions |
| `definitions` | Expand module to its definitions | Definition lookup questions |

## Debug Output

When `--questions-debug` is enabled, each repo shows:

```
=== DEBUG: repo_name ===
=== NODE COUNTS ===
  Function            : 245
  Method              : 189
  Class               : 42
  Module              : 15

=== RELATIONSHIP COUNTS ===
  CALLS               : 523
  DEFINES             : 245
  IMPORTS             : 87
  INHERITS            : 12

=== CANDIDATE SELECTION ===
  Functions examined:   245
  Methods examined:     189
  Candidates accepted:  156
  Candidates rejected:  278

=== REJECTION REASONS ===
    145x 0 CALLS (need 3+)
     89x 1 CALLS (need 3+)
     44x 2 CALLS (need 3+)
```

## Usage Examples

### Full Pipeline with Debug
```bash
uv run python batch/large_scale_processor.py \
    --repo-list repos.json \
    --clone-dir ./clones \
    --output-dir ./output \
    --generate-questions \
    --questions-debug \
    --limit 5
```

### Standalone Question Generation
```bash
uv run python batch/batch_question_generator.py \
    --graphs-dir ./output \
    --clones-dir ./clones \
    --questions-dir ./questions \
    --debug \
    --target-per-repo 1000
```

### Disable Sparse Fallback
```bash
uv run python batch/batch_question_generator.py \
    --graphs-dir ./output \
    --clones-dir ./clones \
    --questions-dir ./questions \
    --no-sparse-fallback
```

## Output

### Per-Repo Output
- `{repo_name}_questions.jsonl` - Generated questions in JSONL format

### Summary Output
- `_questions_summary.json` - Statistics including:
  - Total repos processed
  - Successful vs skipped counts
  - Sparse mode usage counts
  - Total questions generated
