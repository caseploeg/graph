"""
Example: Evaluation Prompt Generation Flow

Demonstrates the complete pipeline from a mock GeneratedQuestion (produced by the
question writer) to a fully hydrated evaluation prompt ready for agent testing.

This script shows:
1. What a generated question looks like
2. How the agent preamble gets hydrated with repo info
3. How the final evaluation prompt is structured

Usage:
    uv run python batch/example_evaluation_prompt.py
"""
from __future__ import annotations

from pathlib import Path

from evaluation_prompt_builder import (
    EvaluationPrompt,
    build_evaluation_prompt,
    detect_primary_language,
    hydrate_preamble,
    load_preamble_template,
)

BATCH_DIR = Path(__file__).parent
TEST_REPOS_DIR = BATCH_DIR / "test_repos"


def create_mock_question() -> dict:
    """
    Simulate what question_generator.py produces after LLM generates a question.

    This represents the JSON output from the META_PROMPT that gets parsed
    into a GeneratedQuestion dataclass.
    """
    return {
        "question": (
            "How does Click propagate validation errors from custom parameter types "
            "back to the user? Specifically, when a ParamType.convert() method raises "
            "a BadParameter exception, what is the complete path from that exception "
            "to the error message displayed to the user?"
        ),
        "difficulty": "hard",
        "reasoning": (
            "This question requires understanding multiple interconnected components: "
            "1) The ParamType base class and its convert() method signature, "
            "2) The BadParameter exception class and its attributes, "
            "3) How Click's core decorators (@click.command, @click.option) wire up "
            "parameter processing, "
            "4) The error handling flow in the Context class, "
            "5) How exceptions are caught and formatted for display. "
            "An agent would need to trace through at least 4-5 files to fully answer this."
        ),
        "expected_search_strategy": (
            "1. Grep for 'class ParamType' to find the base type definition\n"
            "2. Read the convert() method to understand the interface\n"
            "3. Grep for 'class BadParameter' to find the exception\n"
            "4. Search for 'raise BadParameter' to see usage patterns\n"
            "5. Search for 'except BadParameter' to find where it's caught\n"
            "6. Trace the error handling up to Context.fail() or similar\n"
            "7. Find where error messages are formatted and printed to stderr"
        ),
        "seed_node": 1234,
        "context_quality": "excellent",
        "context_nodes": [1234, 1235, 1240, 1250, 1260, 1275, 1280],
    }


def main() -> None:
    repo_path = TEST_REPOS_DIR / "click"

    if not repo_path.exists():
        print(f"Error: Test repo not found at {repo_path}")
        print("Run: git clone https://github.com/pallets/click batch/test_repos/click")
        return

    print("=" * 80)
    print("EXAMPLE: Evaluation Prompt Generation Flow")
    print("=" * 80)
    print()

    print("STEP 1: Mock GeneratedQuestion (from question writer)")
    print("-" * 60)
    mock_question = create_mock_question()
    print(f"Question: {mock_question['question'][:100]}...")
    print(f"Difficulty: {mock_question['difficulty']}")
    print(f"Context Quality: {mock_question['context_quality']}")
    print(f"Seed Node ID: {mock_question['seed_node']}")
    print()

    print("STEP 2: Load and hydrate the agent preamble")
    print("-" * 60)
    preamble_template = load_preamble_template()
    language = detect_primary_language(repo_path)
    repo_name = repo_path.name

    print(f"Repository: {repo_name}")
    print(f"Detected Language: {language}")
    print(f"Preamble template length: {len(preamble_template)} chars")
    print()

    hydrated_system_prompt = hydrate_preamble(preamble_template, repo_name, language)
    print("Hydrated variables:")
    print(f"  ${{REPO_NAME}} -> {repo_name}")
    print(f"  ${{PRIMARY_LANGUAGE}} -> {language}")
    print()

    print("STEP 3: Build full EvaluationPrompt")
    print("-" * 60)
    eval_prompt = build_evaluation_prompt(
        question_data=mock_question,
        repo_path=repo_path,
        preamble_template=preamble_template,
        question_index=0,
    )

    print(f"Question ID: {eval_prompt.question_id}")
    print(f"Repo: {eval_prompt.repo_name}")
    print(f"Language: {eval_prompt.primary_language}")
    print()

    print("=" * 80)
    print("FINAL OUTPUT: Complete Evaluation Prompt")
    print("=" * 80)
    print()

    print("┌" + "─" * 78 + "┐")
    print("│ SYSTEM PROMPT (sent as system message to agent)" + " " * 31 + "│")
    print("└" + "─" * 78 + "┘")
    print()
    print(eval_prompt.system_prompt)
    print()

    print("┌" + "─" * 78 + "┐")
    print("│ USER PROMPT (the question to answer)" + " " * 40 + "│")
    print("└" + "─" * 78 + "┘")
    print()
    print(eval_prompt.user_prompt)
    print()

    print("┌" + "─" * 78 + "┐")
    print("│ METADATA (for evaluation, not sent to agent)" + " " * 32 + "│")
    print("└" + "─" * 78 + "┘")
    print()
    print(f"Question ID: {eval_prompt.question_id}")
    print(f"Difficulty: {eval_prompt.difficulty}")
    print(f"Context Quality: {eval_prompt.context_quality}")
    print(f"Seed Node: {eval_prompt.seed_node}")
    print()
    print("Expected Search Strategy:")
    print(eval_prompt.expected_search_strategy)
    print()
    print("Reasoning (why this question is hard):")
    print(eval_prompt.reasoning)


if __name__ == "__main__":
    main()
