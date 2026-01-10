"""
Evaluation Prompt Builder for Code Search Agent Testing

Takes filtered GeneratedQuestion objects and produces hydrated evaluation prompts
ready for testing code search agents.

Usage:
    # Basic usage
    uv run python batch/evaluation_prompt_builder.py \
        --questions batch/filtered_questions.jsonl \
        --repo batch/test_repos/click \
        --output-dir batch/evaluation_prompts

    # With custom preamble template
    uv run python batch/evaluation_prompt_builder.py \
        --questions batch/filtered_questions.jsonl \
        --repo batch/test_repos/click \
        --output-dir batch/evaluation_prompts \
        --preamble batch/custom_preamble.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template

BATCH_DIR = Path(__file__).parent
DEFAULT_PREAMBLE_PATH = BATCH_DIR / "agent_preamble.md"

LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py"],
    "javascript": [".js", ".jsx", ".mjs"],
    "typescript": [".ts", ".tsx"],
    "java": [".java"],
    "go": [".go"],
    "rust": [".rs"],
    "c": [".c", ".h"],
    "cpp": [".cpp", ".hpp", ".cc", ".cxx"],
    "ruby": [".rb"],
    "php": [".php"],
    "swift": [".swift"],
    "kotlin": [".kt", ".kts"],
    "scala": [".scala"],
    "lua": [".lua"],
    "shell": [".sh", ".bash"],
}


@dataclass
class EvaluationPrompt:
    question_id: str
    repo_name: str
    primary_language: str
    question: str
    difficulty: str
    system_prompt: str
    user_prompt: str
    seed_node: int
    expected_search_strategy: str
    context_quality: str
    reasoning: str


def load_preamble_template(template_path: Path | None = None) -> str:
    if template_path is None:
        template_path = DEFAULT_PREAMBLE_PATH
    return template_path.read_text(encoding="utf-8")


def detect_primary_language(repo_path: Path) -> str:
    extension_counts: Counter[str] = Counter()

    for ext_list in LANGUAGE_EXTENSIONS.values():
        for ext in ext_list:
            count = len(list(repo_path.rglob(f"*{ext}")))
            extension_counts[ext] = count

    if not extension_counts:
        return "unknown"

    top_ext = extension_counts.most_common(1)[0][0]

    for lang, extensions in LANGUAGE_EXTENSIONS.items():
        if top_ext in extensions:
            return lang

    return "unknown"


def hydrate_preamble(template: str, repo_name: str, language: str) -> str:
    t = Template(template)
    return t.safe_substitute(REPO_NAME=repo_name, PRIMARY_LANGUAGE=language)


def build_evaluation_prompt(
    question_data: dict,
    repo_path: Path,
    preamble_template: str,
    question_index: int,
) -> EvaluationPrompt:
    repo_name = repo_path.name
    language = detect_primary_language(repo_path)

    system_prompt = hydrate_preamble(preamble_template, repo_name, language)

    question_id = f"{repo_name}_{question_index + 1:03d}"

    return EvaluationPrompt(
        question_id=question_id,
        repo_name=repo_name,
        primary_language=language,
        question=question_data.get("question", ""),
        difficulty=question_data.get("difficulty", "unknown"),
        system_prompt=system_prompt,
        user_prompt=question_data.get("question", ""),
        seed_node=question_data.get("seed_node", 0),
        expected_search_strategy=question_data.get("expected_search_strategy", ""),
        context_quality=question_data.get("context_quality", "unknown"),
        reasoning=question_data.get("reasoning", ""),
    )


def load_questions(questions_file: Path) -> list[dict]:
    questions = []
    for line in questions_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            questions.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse line: {e}", file=sys.stderr)
    return questions


def write_jsonl_output(prompts: list[EvaluationPrompt], output_path: Path) -> None:
    lines = []
    for prompt in prompts:
        data = {
            "question_id": prompt.question_id,
            "repo_name": prompt.repo_name,
            "primary_language": prompt.primary_language,
            "question": prompt.question,
            "difficulty": prompt.difficulty,
            "system_prompt": prompt.system_prompt,
            "user_prompt": prompt.user_prompt,
            "metadata": {
                "seed_node": prompt.seed_node,
                "expected_search_strategy": prompt.expected_search_strategy,
                "context_quality": prompt.context_quality,
                "reasoning": prompt.reasoning,
            },
        }
        lines.append(json.dumps(data, ensure_ascii=False))
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_individual_files(prompts: list[EvaluationPrompt], output_dir: Path) -> None:
    for prompt in prompts:
        filename = f"{prompt.question_id}.txt"
        filepath = output_dir / filename

        content = f"""=== SYSTEM PROMPT ===
{prompt.system_prompt}

=== USER PROMPT ===
{prompt.user_prompt}

=== METADATA (for reference, not sent to agent) ===
Question ID: {prompt.question_id}
Repo: {prompt.repo_name}
Language: {prompt.primary_language}
Difficulty: {prompt.difficulty}
Context Quality: {prompt.context_quality}
Seed Node: {prompt.seed_node}

Expected Search Strategy:
{prompt.expected_search_strategy}

Reasoning:
{prompt.reasoning}
"""
        filepath.write_text(content, encoding="utf-8")


def build_batch(
    questions_file: Path,
    repo_path: Path,
    output_dir: Path,
    preamble_path: Path | None = None,
) -> list[EvaluationPrompt]:
    preamble_template = load_preamble_template(preamble_path)
    questions = load_questions(questions_file)

    if not questions:
        print("No questions found in input file", file=sys.stderr)
        return []

    print(f"Loaded {len(questions)} questions", file=sys.stderr)
    print(f"Repository: {repo_path}", file=sys.stderr)
    print(f"Output directory: {output_dir}", file=sys.stderr)
    print(file=sys.stderr)

    prompts = []
    for i, q in enumerate(questions):
        prompt = build_evaluation_prompt(q, repo_path, preamble_template, i)
        prompts.append(prompt)

    output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = output_dir / "evaluation_prompts.jsonl"
    write_jsonl_output(prompts, jsonl_path)
    print(f"Wrote JSONL: {jsonl_path}", file=sys.stderr)

    write_individual_files(prompts, output_dir)
    print(f"Wrote {len(prompts)} individual .txt files to {output_dir}", file=sys.stderr)

    print(file=sys.stderr)
    print("Summary:", file=sys.stderr)
    print(f"  Total prompts: {len(prompts)}", file=sys.stderr)
    if prompts:
        print(f"  Repo: {prompts[0].repo_name}", file=sys.stderr)
        print(f"  Language: {prompts[0].primary_language}", file=sys.stderr)

    quality_counts = Counter(p.context_quality for p in prompts)
    if quality_counts:
        print("  Context quality distribution:", file=sys.stderr)
        for quality, count in sorted(quality_counts.items()):
            print(f"    {quality}: {count}", file=sys.stderr)

    return prompts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build evaluation prompts from filtered questions"
    )
    parser.add_argument(
        "--questions",
        type=Path,
        required=True,
        help="Path to JSONL file with filtered questions",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        required=True,
        help="Path to the repository",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for evaluation prompts",
    )
    parser.add_argument(
        "--preamble",
        type=Path,
        default=None,
        help=f"Custom preamble template (default: {DEFAULT_PREAMBLE_PATH})",
    )

    args = parser.parse_args()

    if not args.questions.exists():
        print(f"Error: Questions file not found: {args.questions}", file=sys.stderr)
        sys.exit(1)

    if not args.repo.exists():
        print(f"Error: Repository not found: {args.repo}", file=sys.stderr)
        sys.exit(1)

    if args.preamble and not args.preamble.exists():
        print(f"Error: Preamble file not found: {args.preamble}", file=sys.stderr)
        sys.exit(1)

    build_batch(args.questions, args.repo, args.output_dir, args.preamble)


if __name__ == "__main__":
    main()
