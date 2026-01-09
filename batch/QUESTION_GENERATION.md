# Question Generation for Code Search Agent Evaluation

This guide explains how to use the graph-based question generation system to create challenging test questions for evaluating code search agents.

## Overview

The system exploits **asymmetric information** between the question writer and the agent being tested:

| Question Writer Sees | Agent Has Access To |
|---------------------|---------------------|
| Graph structure (CALLS, INHERITS, IMPORTS) | Root directory only |
| Qualified names and file locations | Glob patterns |
| Source code snippets | Grep searches |
| Relationship context | File reading |

Hard questions exploit this gap - they require discovering relationships that are visible in the graph but not immediately searchable via text.

## Quick Start

### 1. Generate a Graph

First, export a repository to JSON format:

```bash
uv run cgr export-json --repo-path /path/to/repo --output repo.json
```

### 2. Preview a Single Prompt

Use `preview_prompt.py` to see a hydrated meta-prompt:

```bash
uv run python batch/preview_prompt.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click
```

Save to file for manual testing:

```bash
uv run python batch/preview_prompt.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --output prompt.txt
```

### 3. Generate Diverse Prompts

Use `generate_diverse_questions.py` to create multiple prompts with varied strategies:

```bash
uv run python batch/generate_diverse_questions.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --num-prompts 20 \
    --output-dir prompts/
```

## Expansion Strategies

The system supports five graph expansion strategies, each producing different context shapes:

### `callees` (downstream focus)
Follows what the seed function calls, up to 4 levels deep.

Best for: "What does X depend on?", "How does X process data?"

```
seed() → helper() → util() → lib()
```

### `callers` (upstream focus)
Follows what calls the seed function, up to 3 levels deep.

Best for: "How is X used?", "What triggers X?"

```
main() → handler() → process() → seed()
```

### `chain` (deep path + siblings)
Follows a single call chain deeply, plus sibling functions in the same files.

Best for: "How does data flow through X?", tracing execution paths.

### `file` (co-location)
Gets all functions in the same file as the seed, plus their external dependencies.

Best for: "How does this module work?", understanding file-level organization.

### `bfs` (broad)
Standard breadth-first expansion across all relationship types.

Best for: Broad architectural questions, general understanding.

## Strategy Weights

The diverse generator rotates through strategies based on weights:

```bash
# Default weights
--weights "callees:3,chain:2,file:2,callers:2,bfs:1"

# Favor deep call chains
--weights "callees:5,chain:4,callers:1,file:1,bfs:0"

# Balanced
--weights "callees:2,chain:2,file:2,callers:2,bfs:2"
```

## Context Structure

Each generated prompt includes:

### 1. ASCII Graph Diagram

Visual representation of relationships around the seed:

```
=== GRAPH STRUCTURE ===

Seed: validate_input (Function)
│
├─► CALLS (downstream)
│   ├── check_format
│   │   └── parse_value
│   └── sanitize_data
│
├─◄ CALLED BY (upstream)
│   ├── process_request
│   └── batch_processor
│
└── SIBLINGS (same file)
    ├── validate_output
    └── validate_config

Files involved:
  - src/validation.py
  - src/parsers.py
```

### 2. Structured Graph Context

File structure, call graph edges, and inheritance relationships in a scannable format.

### 3. Source Code Chunks

XML-tagged code snippets with metadata:

```xml
<code_chunk>
  <node_id>42</node_id>
  <type>Function</type>
  <qualified_name>click.core.Command.invoke</qualified_name>
  <file>src/click/core.py</file>
  <lines>1024-1048</lines>
  <calls>Context.invoke, _process_result</calls>
  <called_by>Group.invoke, main</called_by>
  <source>
def invoke(self, ctx: Context) -> t.Any:
    return _process_result(self.callback(*args, **kwargs))
  </source>
</code_chunk>
```

## CLI Reference

### preview_prompt.py

```
usage: preview_prompt.py [options]

Options:
  --graph PATH          Path to exported graph JSON file (required)
  --repo PATH           Path to the repository (required)
  --strategy STRATEGY   Expansion strategy: bfs, chain, callers, callees, file
  --seed-node ID        Specific node ID to use as seed (default: random)
  --max-hops N          Maximum hops for BFS expansion (default: 2)
  --max-nodes N         Maximum nodes in context (default: 25)
  --max-tokens N        Maximum tokens for context (default: 8000)
  --output PATH         Save prompt to file (default: stdout)
```

### generate_diverse_questions.py

```
usage: generate_diverse_questions.py [options]

Options:
  --graph PATH          Path to exported graph JSON file (required)
  --repo PATH           Path to the repository (required)
  --num-prompts N       Number of prompts to generate (default: 10)
  --max-tokens N        Maximum tokens for context (default: 8000)
  --weights SPEC        Strategy weights (e.g., "callees:4,chain:3,file:2")
  --random-seed N       Random seed for reproducibility
  --output-dir PATH     Output directory for prompt files
```

## Token Budget Management

The system uses priority-based truncation to fit within token limits:

1. **Seed node first** - always included
2. **Priority by distance** - 1-hop neighbors before 2-hop
3. **No mid-truncation** - skips entire chunks that don't fit
4. **Omission summary** - tells LLM what was left out

Adjust with `--max-tokens`:

```bash
# Smaller context for faster models
--max-tokens 4000

# Larger context for more capable models
--max-tokens 16000
```

## Example Workflow

### Generate prompts for a codebase:

```bash
# 1. Export the graph
uv run cgr export-json --repo-path ./my-project --output my-project.json

# 2. Generate diverse prompts
uv run python batch/generate_diverse_questions.py \
    --graph my-project.json \
    --repo ./my-project \
    --num-prompts 50 \
    --random-seed 42 \
    --output-dir prompts/

# 3. Review and send to your LLM
ls prompts/
# 001_callees_main.txt
# 002_chain_process_data.txt
# 003_file_validators.txt
# ...
```

### Reproducible generation:

```bash
# Same seed = same prompts
uv run python batch/generate_diverse_questions.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --num-prompts 20 \
    --random-seed 42 \
    --output-dir run1/

uv run python batch/generate_diverse_questions.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --num-prompts 20 \
    --random-seed 42 \
    --output-dir run2/

# run1/ and run2/ will be identical
```

## Meta-Prompt Design

The generated prompt instructs the LLM to:

1. Generate questions that require **multi-file navigation**
2. Avoid questions solvable by simple grep
3. Focus on **relationship discovery** and **semantic understanding**
4. Output structured JSON with question, reasoning, and expected search strategy

See `question_generator.py:META_PROMPT` for the full template.

## Files

| File | Purpose |
|------|---------|
| `question_generator.py` | Core library: sampling, expansion, context building |
| `preview_prompt.py` | CLI to preview a single hydrated prompt |
| `generate_diverse_questions.py` | CLI to generate multiple diverse prompts |
