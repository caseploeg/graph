from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from codebase_rag.node_text_extractor import NodeTextExtractor, NodeTextResult


class QuestionCategory(StrEnum):
    TRACE = "trace"
    IMPACT = "impact"
    RESOLUTION = "resolution"
    DEPENDENCY = "dependency"
    BRIDGE = "bridge"
    OVERRIDE = "override"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


@dataclass
class GraphPath:
    node_names: list[str]
    node_ids: list[int]
    edge_types: list[str]
    modules: set[str]


@dataclass
class CodeContext:
    node_name: str
    node_id: int
    file_path: str | None
    start_line: int | None
    end_line: int | None
    code_chunk: str | None


@dataclass
class GeneratedQuestion:
    question: str
    category: QuestionCategory
    difficulty: Difficulty
    answer_path: GraphPath
    code_contexts: list[CodeContext] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)


class CodeGraphAnalyzer:

    def __init__(self, graph_path: str | Path, repo_base_path: str | Path | None = None):
        self.graph_path = Path(graph_path)
        data = json.loads(self.graph_path.read_text())
        self.nodes = {n["node_id"]: n for n in data.get("nodes", [])}
        self.edges = data.get("relationships", [])
        self._build_indexes()

        self.repo_base_path = Path(repo_base_path) if repo_base_path else Path.cwd()
        self._text_extractor: NodeTextExtractor | None = None

    @property
    def text_extractor(self) -> NodeTextExtractor:
        if self._text_extractor is None:
            self._text_extractor = NodeTextExtractor(self.graph_path, self.repo_base_path)
        return self._text_extractor

    def _build_indexes(self) -> None:
        self.outgoing: dict[int, list[tuple[str, int]]] = defaultdict(list)
        self.incoming: dict[int, list[tuple[str, int]]] = defaultdict(list)
        self.by_type: dict[str, list[int]] = defaultdict(list)
        self.by_edge_type: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.qname_to_id: dict[str, int] = {}

        for e in self.edges:
            src, tgt, etype = e["from_id"], e["to_id"], e["type"]
            self.outgoing[src].append((etype, tgt))
            self.incoming[tgt].append((etype, src))
            self.by_edge_type[etype].append((src, tgt))

        for nid, node in self.nodes.items():
            for label in node.get("labels", []):
                self.by_type[label].append(nid)
            qname = self.get_qname(nid)
            if qname:
                self.qname_to_id[qname] = nid

    def extract_code_context(self, node_id: int) -> NodeTextResult:
        return self.text_extractor.extract(node_id)

    def extract_code_contexts_batch(self, node_ids: list[int]) -> dict[int, NodeTextResult]:
        return self.text_extractor.extract_batch(node_ids)

    def get_qname(self, nid: int) -> str | None:
        if nid not in self.nodes:
            return None
        props = self.nodes[nid].get("properties", {})
        return props.get("qualified_name", props.get("name"))

    def get_module(self, qname: str | None) -> str:
        if not qname:
            return ""
        short = qname.replace("code-graph-rag.codebase_rag.", "")
        parts = short.split(".")
        return parts[0] if parts else ""

    def short_name(self, qname: str | None) -> str:
        if not qname:
            return ""
        return qname.replace("code-graph-rag.codebase_rag.", "")

    def is_production(self, qname: str | None) -> bool:
        if not qname:
            return False
        lower = qname.lower()
        return "test" not in lower and "conftest" not in lower


class QuestionGenerator:

    def __init__(self, analyzer: CodeGraphAnalyzer):
        self.g = analyzer
        self.templates = self._load_templates()

    def _load_templates(self) -> dict[QuestionCategory, list[str]]:
        return {
            QuestionCategory.TRACE: [
                "Trace the execution from `{start}` to `{end}`. What intermediate functions are involved?",
                "When `{start}` is called, what chain of calls eventually reaches `{end}`?",
                "Follow the call path from `{start}` through `{mid}` to `{end}`. What data transformations occur?",
                "Starting from `{start}`, how many function calls does it take to reach `{end}`?",
            ],
            QuestionCategory.IMPACT: [
                "If `{target}` changed its return type, which {count} callers would need updates?",
                "What functions depend on `{target}` completing successfully?",
                "If `{target}` raised an exception, which upstream callers handle it?",
                "`{target}` is called by {count} different modules. What's the blast radius if it changes?",
            ],
            QuestionCategory.RESOLUTION: [
                "When `{child}.{method}` is called, does it use its own implementation or the one from `{parent}`?",
                "Which `{parent}` subclass's `{method}` method handles {context}?",
                "`{child}` inherits from multiple mixins. Which one provides `{method}`?",
                "If `{child}.{method}` is invoked, which parent class originally defined this method?",
            ],
            QuestionCategory.DEPENDENCY: [
                "What external packages does `{module}` depend on?",
                "Which type definitions does `{target}` require from `types_defs`?",
                "What shared utilities do both `{a}` and `{b}` import?",
                "List the transitive dependencies of `{module}` up to depth 2.",
            ],
            QuestionCategory.BRIDGE: [
                "`{target}` is called from {count} different submodules. What architectural role does it serve?",
                "Why is `{target}` the convergence point for {modules}?",
                "`{target}` connects {count} different modules. What pattern does this represent?",
                "What makes `{target}` a critical integration point in the architecture?",
            ],
            QuestionCategory.OVERRIDE: [
                "What does `{child}.{method}` add beyond the base `{parent}.{method}` behavior?",
                "How does `{child_a}.{method}` differ from `{child_b}.{method}`?",
                "When `{child}.{method}` overrides the parent, what specific logic does it add?",
                "Does `{child}.{method}` call super()? What parent behavior does it preserve or replace?",
            ],
        }

    def find_call_chains(
        self, min_hops: int = 3, max_hops: int = 4, cross_module: bool = True
    ) -> list[GraphPath]:
        chains = []
        func_nodes = self.g.by_type.get("Function", []) + self.g.by_type.get(
            "Method", []
        )

        for start_id in func_nodes:
            start_qn = self.g.get_qname(start_id)
            if not self.g.is_production(start_qn):
                continue

            paths = self._dfs_calls(start_id, [], [], max_hops)
            for path_ids, edge_types in paths:
                if len(path_ids) < min_hops + 1:
                    continue

                qnames = [self.g.get_qname(nid) for nid in path_ids]
                if not all(self.g.is_production(qn) for qn in qnames):
                    continue

                modules = {self.g.get_module(qn) for qn in qnames}
                modules.discard("")

                if cross_module and len(modules) < 2:
                    continue

                chains.append(
                    GraphPath(
                        node_names=[self.g.short_name(qn) for qn in qnames],
                        node_ids=path_ids,
                        edge_types=edge_types,
                        modules=modules,
                    )
                )

        return chains

    def _dfs_calls(
        self, current: int, path: list[int], edges: list[str], max_depth: int
    ) -> list[tuple[list[int], list[str]]]:
        if current in path:
            return []

        new_path = path + [current]
        new_edges = edges

        if len(new_path) > max_depth:
            return [(new_path, new_edges)]

        results = []
        for edge_type, target in self.g.outgoing[current]:
            if edge_type == "CALLS":
                child_results = self._dfs_calls(
                    target, new_path, new_edges + ["CALLS"], max_depth
                )
                results.extend(child_results)

        if not results:
            results.append((new_path, new_edges))

        return results

    def find_bridge_functions(self, min_modules: int = 3) -> list[tuple[str, int, set[str]]]:
        bridges = []

        func_nodes = self.g.by_type.get("Function", []) + self.g.by_type.get(
            "Method", []
        )

        for target_id in func_nodes:
            target_qn = self.g.get_qname(target_id)
            if not self.g.is_production(target_qn):
                continue

            caller_modules = set()
            for edge_type, caller_id in self.g.incoming[target_id]:
                if edge_type == "CALLS":
                    caller_qn = self.g.get_qname(caller_id)
                    if self.g.is_production(caller_qn):
                        mod = self.g.get_module(caller_qn)
                        if mod:
                            caller_modules.add(mod)

            if len(caller_modules) >= min_modules:
                bridges.append((self.g.short_name(target_qn), target_id, caller_modules))

        bridges.sort(key=lambda x: -len(x[2]))
        return bridges

    def find_overrides(self) -> list[tuple[str, int, str, int]]:
        overrides = []
        for src, tgt in self.g.by_edge_type.get("OVERRIDES", []):
            child_qn = self.g.get_qname(src)
            parent_qn = self.g.get_qname(tgt)
            if self.g.is_production(child_qn) and self.g.is_production(parent_qn):
                overrides.append(
                    (self.g.short_name(child_qn), src, self.g.short_name(parent_qn), tgt)
                )
        return overrides

    def _build_code_context(self, node_name: str, node_id: int) -> CodeContext:
        result = self.g.extract_code_context(node_id)
        return CodeContext(
            node_name=node_name,
            node_id=node_id,
            file_path=str(result.file_path) if result.file_path else None,
            start_line=result.start_line,
            end_line=result.end_line,
            code_chunk=result.code_chunk,
        )

    def _build_code_contexts_for_path(self, path: GraphPath) -> list[CodeContext]:
        contexts = []
        for name, nid in zip(path.node_names, path.node_ids):
            contexts.append(self._build_code_context(name, nid))
        return contexts

    def find_inheritance_trees(self) -> dict[str, list[str]]:
        trees: dict[str, list[str]] = defaultdict(list)
        for src, tgt in self.g.by_edge_type.get("INHERITS", []):
            child_qn = self.g.get_qname(src)
            parent_qn = self.g.get_qname(tgt)
            if self.g.is_production(child_qn):
                trees[self.g.short_name(parent_qn)].append(self.g.short_name(child_qn))
        return dict(trees)

    def generate_trace_question(
        self, path: GraphPath, include_context: bool = True
    ) -> GeneratedQuestion:
        template = random.choice(self.templates[QuestionCategory.TRACE])

        if len(path.node_names) >= 3:
            question = template.format(
                start=path.node_names[0],
                mid=path.node_names[len(path.node_names) // 2],
                end=path.node_names[-1],
            )
        else:
            question = template.format(
                start=path.node_names[0], end=path.node_names[-1], mid=""
            )

        difficulty = self._assess_difficulty(len(path.node_names), len(path.modules))

        code_contexts = []
        source_files = []
        if include_context:
            code_contexts = self._build_code_contexts_for_path(path)
            source_files = list({c.file_path for c in code_contexts if c.file_path})

        return GeneratedQuestion(
            question=question,
            category=QuestionCategory.TRACE,
            difficulty=difficulty,
            answer_path=path,
            code_contexts=code_contexts,
            source_files=source_files,
        )

    def generate_bridge_question(
        self, target: str, target_id: int, modules: set[str], include_context: bool = True
    ) -> GeneratedQuestion:
        template = random.choice(self.templates[QuestionCategory.BRIDGE])
        question = template.format(
            target=target, count=len(modules), modules=", ".join(sorted(modules)[:4])
        )

        difficulty = Difficulty.MEDIUM if len(modules) < 5 else Difficulty.HARD

        path = GraphPath(
            node_names=[target], node_ids=[target_id], edge_types=[], modules=modules
        )

        code_contexts = []
        source_files = []
        if include_context:
            code_contexts = [self._build_code_context(target, target_id)]
            source_files = list({c.file_path for c in code_contexts if c.file_path})

        return GeneratedQuestion(
            question=question,
            category=QuestionCategory.BRIDGE,
            difficulty=difficulty,
            answer_path=path,
            code_contexts=code_contexts,
            source_files=source_files,
        )

    def generate_override_question(
        self,
        child_method: str,
        child_id: int,
        parent_method: str,
        parent_id: int,
        include_context: bool = True,
    ) -> GeneratedQuestion:
        template = random.choice(self.templates[QuestionCategory.OVERRIDE])

        child_parts = child_method.rsplit(".", 1)
        parent_parts = parent_method.rsplit(".", 1)

        child_class = child_parts[0] if len(child_parts) > 1 else child_method
        method_name = child_parts[1] if len(child_parts) > 1 else "unknown"
        parent_class = parent_parts[0] if len(parent_parts) > 1 else parent_method

        question = template.format(
            child=child_class,
            parent=parent_class,
            method=method_name,
            child_a=child_class,
            child_b=parent_class,
        )

        path = GraphPath(
            node_names=[child_method, parent_method],
            node_ids=[child_id, parent_id],
            edge_types=["OVERRIDES"],
            modules=set(),
        )

        code_contexts = []
        source_files = []
        if include_context:
            code_contexts = [
                self._build_code_context(child_method, child_id),
                self._build_code_context(parent_method, parent_id),
            ]
            source_files = list({c.file_path for c in code_contexts if c.file_path})

        return GeneratedQuestion(
            question=question,
            category=QuestionCategory.OVERRIDE,
            difficulty=Difficulty.MEDIUM,
            answer_path=path,
            code_contexts=code_contexts,
            source_files=source_files,
        )

    def _assess_difficulty(self, hops: int, modules_crossed: int) -> Difficulty:
        score = hops + modules_crossed
        if score <= 3:
            return Difficulty.EASY
        elif score <= 5:
            return Difficulty.MEDIUM
        elif score <= 7:
            return Difficulty.HARD
        return Difficulty.EXPERT

    def generate_question_set(
        self,
        count: int = 20,
        categories: list[QuestionCategory] | None = None,
        include_context: bool = True,
    ) -> list[GeneratedQuestion]:
        if categories is None:
            categories = list(QuestionCategory)

        questions = []

        if QuestionCategory.TRACE in categories:
            chains = self.find_call_chains(min_hops=3, max_hops=4)
            for path in random.sample(chains, min(count // 3, len(chains))):
                questions.append(self.generate_trace_question(path, include_context))

        if QuestionCategory.BRIDGE in categories:
            bridges = self.find_bridge_functions(min_modules=3)
            for target, target_id, modules in bridges[: count // 3]:
                questions.append(
                    self.generate_bridge_question(target, target_id, modules, include_context)
                )

        if QuestionCategory.OVERRIDE in categories:
            overrides = self.find_overrides()
            for child, child_id, parent, parent_id in random.sample(
                overrides, min(count // 3, len(overrides))
            ):
                questions.append(
                    self.generate_override_question(
                        child, child_id, parent, parent_id, include_context
                    )
                )

        random.shuffle(questions)
        return questions[:count]


def truncate_code(code: str | None, max_lines: int = 10) -> str:
    if not code:
        return "(no code available)"
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


def main():
    graph_path = Path("code-graph-rag-graph.json")
    if not graph_path.exists():
        print(f"Graph file not found: {graph_path}")
        return

    repo_path = Path.cwd()
    analyzer = CodeGraphAnalyzer(graph_path, repo_path)
    generator = QuestionGenerator(analyzer)

    print("=" * 70)
    print("GENERATED QUESTIONS WITH CODE CONTEXT")
    print("=" * 70)

    questions = generator.generate_question_set(count=5, include_context=True)

    for i, q in enumerate(questions, 1):
        print(f"\n{'='*70}")
        print(f"QUESTION {i}: [{q.category.upper()}] [{q.difficulty}]")
        print("=" * 70)
        print(f"\n{q.question}\n")

        if q.answer_path.node_names:
            print(f"Answer path: {' -> '.join(q.answer_path.node_names[:4])}")
        if q.answer_path.modules:
            print(f"Modules involved: {', '.join(sorted(q.answer_path.modules))}")
        if q.source_files:
            print(f"Source files: {', '.join(q.source_files)}")

        if q.code_contexts:
            print("\n--- Code Context ---")
            for ctx in q.code_contexts:
                print(f"\n[{ctx.node_name}]")
                if ctx.file_path and ctx.start_line:
                    print(f"  File: {ctx.file_path}:{ctx.start_line}-{ctx.end_line}")
                print(f"  Code:\n{truncate_code(ctx.code_chunk, max_lines=100)}")

    print("\n" + "=" * 70)
    print("STATISTICS")
    print("=" * 70)

    print(f"\nCall chains found: {len(generator.find_call_chains())}")
    print(f"Bridge functions: {len(generator.find_bridge_functions())}")
    print(f"Method overrides: {len(generator.find_overrides())}")
    print(f"Inheritance trees: {len(generator.find_inheritance_trees())}")


if __name__ == "__main__":
    main()
