#!/usr/bin/env python
from __future__ import annotations

import json
from pathlib import Path

from codebase_rag.node_text_extractor import (
    extract_nodes_file_to_jsonl,
    extract_nodes_to_jsonl,
    read_node_ids_from_file,
)

BATCH_DIR = Path(__file__).parent
GRAPH_PATH = BATCH_DIR / "test_output" / "log.json"
REPO_PATH = BATCH_DIR / "test_repos" / "log"


def main() -> None:
    print(f"Graph: {GRAPH_PATH}")
    print(f"Repo: {REPO_PATH}")
    print()

    node_ids = [29, 30, 31]
    print(f"Extracting nodes: {node_ids}")
    print("-" * 60)

    jsonl = extract_nodes_to_jsonl(GRAPH_PATH, REPO_PATH, node_ids)

    for line in jsonl.split("\n"):
        obj = json.loads(line)
        print(f"node_id: {obj['node_id']}")
        print(f"qualified_name: {obj['qualified_name']}")
        print(f"file_path: {obj['file_path']}")
        print(f"lines: {obj['start_line']}-{obj['end_line']}")
        print(f"error: {obj['error']}")
        print(f"text:\n{obj['text']}")
        print("-" * 60)

    output_file = BATCH_DIR / "test_output" / "test_nodes.jsonl"
    extract_nodes_to_jsonl(GRAPH_PATH, REPO_PATH, node_ids, output_file)
    print(f"\nWrote JSONL to: {output_file}")


if __name__ == "__main__":
    main()
