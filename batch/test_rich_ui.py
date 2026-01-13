#!/usr/bin/env python
"""Test script to verify Rich UI and log exports for questions generation."""
from __future__ import annotations

import sys
from pathlib import Path

BATCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BATCH_DIR.parent))

from batch.batch_question_generator import batch_generate_questions
from batch.questions_rich_ui import QuestionsLogExporter, QuestionsProgressUI


def main():
    graphs_dir = BATCH_DIR / "test_output"
    clones_dir = BATCH_DIR / "test_repos"
    questions_dir = BATCH_DIR / "test_questions_rich"

    questions_dir.mkdir(parents=True, exist_ok=True)

    graph_files = [
        f for f in graphs_dir.glob("*.json")
        if f.name != "_batch_summary.json"
    ][:2]

    total_repos = len(graph_files)
    print(f"Testing Rich UI with {total_repos} repos")

    log_exporter = QuestionsLogExporter(questions_dir)

    config_dict = {
        "target_per_repo": 30,
        "min_questions": 10,
        "workers": 2,
        "sparse_fallback": True,
    }

    questions_ui = QuestionsProgressUI(
        total_repos=total_repos,
        log_exporter=log_exporter,
    )
    questions_ui.start(config=config_dict)

    with questions_ui.live_context():
        results = batch_generate_questions(
            graphs_dir=graphs_dir,
            clones_dir=clones_dir,
            questions_dir=questions_dir,
            target_per_repo=30,
            min_questions=10,
            workers=2,
            limit=2,
            sparse_fallback=True,
            verbose=False,
            ui=questions_ui,
        )
        questions_ui.finish()

    log_exporter.finish(questions_ui.stats)
    log_exporter.close()
    questions_ui.print_summary()

    print("\n" + "=" * 60)
    print("VERIFYING LOG EXPORTS")
    print("=" * 60)

    jsonl_path = questions_dir / "_questions_log.jsonl"
    txt_path = questions_dir / "_questions_log.txt"

    print(f"\nJSONL log exists: {jsonl_path.exists()}")
    print(f"TXT log exists: {txt_path.exists()}")

    if jsonl_path.exists():
        print(f"\n--- JSONL Log ({jsonl_path}) ---")
        print(jsonl_path.read_text())

    if txt_path.exists():
        print(f"\n--- TXT Log ({txt_path}) ---")
        print(txt_path.read_text())


if __name__ == "__main__":
    main()
