# Generate Diverse Questions

This script generates diverse meta-prompts for evaluating code search agents. It outputs prompts as JSONL with rich metadata for downstream processing.

## Quick Start

```bash
# Generate 100 prompts to a JSONL file
uv run python batch/generate_diverse_questions.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --num-prompts 100 \
    --output prompts.jsonl

# Verify the output
wc -l prompts.jsonl  # Should show 100
head -1 prompts.jsonl | jq .
```

## CLI Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--graph` | Yes | - | Path to exported graph JSON file |
| `--repo` | Yes | - | Path to the repository source code |
| `--num-prompts` | No | 10 | Number of prompts to generate |
| `--output` | No | stdout | Output JSONL file path |
| `--repo-name` | No | derived from `--repo` | Repository name for metadata |
| `--max-tokens` | No | 8000 | Maximum tokens for context |
| `--weights` | No | see below | Strategy weights |
| `--random-seed` | No | - | Random seed for reproducibility |

## JSONL Output Format

Each line is a JSON object with the following fields:

```json
{
  "prompt_id": "click_0001",
  "repo_name": "click",
  "primary_language": "python",
  "expansion_strategy": "callees",
  "seed_node_id": 42,
  "seed_node_name": "echo",
  "seed_node_qualified_name": "click.echo",
  "context_node_ids": [42, 55, 67, 89],
  "file_paths": ["src/click/utils.py", "src/click/core.py"],
  "prompt_text": "You are a test question writer..."
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `prompt_id` | string | Unique identifier: `{repo_name}_{index:04d}` |
| `repo_name` | string | Repository name (from `--repo-name` or derived) |
| `primary_language` | string | Detected language (python, javascript, etc.) |
| `expansion_strategy` | string | Strategy used: bfs, chain, callers, callees, file |
| `seed_node_id` | int | Graph node ID of the seed function/class |
| `seed_node_name` | string | Simple name of the seed node |
| `seed_node_qualified_name` | string | Fully qualified name (e.g., `module.Class.method`) |
| `context_node_ids` | list[int] | All node IDs included in the context |
| `file_paths` | list[string] | Source files involved in the context |
| `prompt_text` | string | The full meta-prompt text for LLM consumption |

## Expansion Strategies

The script uses weighted rotation through five expansion strategies:

| Strategy | Default Weight | Description |
|----------|---------------|-------------|
| `callees` | 3 | Follow outgoing CALLS (what does this function call?) |
| `chain` | 2 | Deep call chains with siblings at each level |
| `file` | 2 | Same-file functions + external call targets |
| `callers` | 2 | Follow incoming CALLS (who calls this function?) |
| `bfs` | 1 | Breadth-first expansion up to 2 hops |

### Customizing Weights

```bash
# Heavy focus on callee exploration
uv run python batch/generate_diverse_questions.py \
    --graph graph.json \
    --repo ./repo \
    --num-prompts 500 \
    --weights "callees:5,chain:2,file:2,callers:1,bfs:1" \
    --output prompts.jsonl
```

## Integration with agent_preamble

The JSONL output includes `repo_name` and `primary_language` fields that can be passed directly to the agent preamble template:

```python
from string import Template

# Load preamble template
preamble_template = Path("batch/agent_preamble.md").read_text()

# Hydrate with values from JSONL record
t = Template(preamble_template)
system_prompt = t.safe_substitute(
    REPO_NAME=record["repo_name"],
    PRIMARY_LANGUAGE=record["primary_language"]
)
```

## Performance

Benchmarks on a MacBook Pro (M1):

| Prompts | Time | File Size |
|---------|------|-----------|
| 100 | ~0.5s | ~2MB |
| 1000 | ~3.5s | ~22MB |

The script can generate 1000+ prompts efficiently. The limiting factor is the number of candidate seed nodes in the graph (functions/methods that have meaningful relationships).

## Example: Full Pipeline

```bash
# 1. Generate diverse prompts
uv run python batch/generate_diverse_questions.py \
    --graph batch/test_output/click.json \
    --repo batch/test_repos/click \
    --num-prompts 1000 \
    --random-seed 42 \
    --output click_prompts.jsonl

# 2. View stats
wc -l click_prompts.jsonl
jq -r '.expansion_strategy' click_prompts.jsonl | sort | uniq -c

# 3. Sample a record
head -1 click_prompts.jsonl | jq '{prompt_id, repo_name, primary_language, expansion_strategy, seed_node_name}'

# 4. Build evaluation prompts (downstream)
uv run python batch/evaluation_prompt_builder.py \
    --questions click_prompts.jsonl \
    --repo batch/test_repos/click \
    --output-dir evaluation_prompts/
```

## Reproducibility

Use `--random-seed` for reproducible generation:

```bash
# Same seed = same output
uv run python batch/generate_diverse_questions.py \
    --graph graph.json --repo ./repo \
    --num-prompts 100 --random-seed 42 \
    --output run1.jsonl

uv run python batch/generate_diverse_questions.py \
    --graph graph.json --repo ./repo \
    --num-prompts 100 --random-seed 42 \
    --output run2.jsonl

diff run1.jsonl run2.jsonl  # No differences
```
