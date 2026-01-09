# Batch JSON Graph Export: Feasibility Analysis

## Executive Summary

**Verdict: Highly Feasible**

Your concern that "Memgraph is not actually that important" is correct for your use case. The codebase has excellent separation of concerns via the `IngestorProtocol` abstraction. Creating a direct JSON export path without Memgraph requires minimal changes.

**Key findings:**
1. Graph structure is built entirely in memory before persistence
2. `ProtobufFileIngestor` already demonstrates Memgraph-free operation
3. A `JsonFileIngestor` (~100 lines) would enable direct JSON export
4. `GraphLoader` already supports querying JSON files without Memgraph

---

## Current Architecture

### The Three-Pass Parsing Pipeline

```
Pass 1: StructureProcessor → Project/Package/Folder nodes
Pass 2: DefinitionProcessor → Module/Class/Function nodes + function_registry
Pass 3: CallProcessor      → CALLS relationships (uses function_registry)
                ↓
         IngestorProtocol.flush_all()
                ↓
    [MemgraphIngestor | ProtobufFileIngestor | ???]
```

The parsing pipeline is **completely decoupled** from Memgraph. All three passes operate on in-memory data structures:

| Structure | Purpose | Populated |
|-----------|---------|-----------|
| `FunctionRegistryTrie` | O(log n) qualified name lookups | Pass 2 |
| `SimpleNameLookup` | simple_name → {qualified_names} | Pass 2 |
| `BoundedASTCache` | LRU cache of parsed ASTs | Pass 2 |
| `class_inheritance` | class → parent classes | Pass 2 |

### IngestorProtocol Interface

All node/relationship creation goes through this protocol (`codebase_rag/services/__init__.py`):

```python
@runtime_checkable
class IngestorProtocol(Protocol):
    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None: ...
    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None: ...
    def flush_all(self) -> None: ...
```

### Current Implementations

| Ingestor | Output | CLI Command | Requires DB |
|----------|--------|-------------|-------------|
| `MemgraphIngestor` | Memgraph DB | `cgr start --update-graph` | Yes |
| `ProtobufFileIngestor` | `.proto` files | `cgr index` | No |
| **JsonFileIngestor** (proposed) | `.json` files | `cgr export-json` | No |

---

## Memgraph Coupling Points

### Required for Core Parsing: NONE

The `GraphUpdater.run()` method (`graph_updater.py:264-285`) only depends on `IngestorProtocol`:

```python
def run(self) -> None:
    self.ingestor.ensure_node_batch(...)  # Protocol method
    self.factory.structure_processor.identify_structure()  # Pass 1
    self._process_files()  # Pass 2
    self._process_function_calls()  # Pass 3
    self.ingestor.flush_all()  # Protocol method
    self._generate_semantic_embeddings()  # Optional, see below
```

### Optional Features Requiring QueryProtocol

Only semantic embeddings require `QueryProtocol` (graph querying capability):

```python
def _generate_semantic_embeddings(self) -> None:
    if not isinstance(self.ingestor, QueryProtocol):
        logger.info(ls.INGESTOR_NO_QUERY)
        return  # Gracefully skips if ingestor can't query
```

This is already handled - non-Memgraph ingestors simply skip embedding generation.

### Current JSON Export Flow (Suboptimal for Batch)

```
Repository → Parse → Memgraph → Query → JSON
                        ↑
                 (unnecessary for batch)
```

Current export requires:
1. Memgraph running
2. Ingesting to database
3. Querying database back
4. Writing JSON

---

## Proposed Solution: JsonFileIngestor

### Implementation Pattern

Follow `ProtobufFileIngestor` pattern (`services/protobuf_service.py`):

```python
class JsonFileIngestor:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self._nodes: dict[str, dict] = {}  # id → node data
        self._relationships: list[dict] = []
        self._node_counter = 0

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        node_id = self._get_node_id(label, properties)
        if node_id not in self._nodes:
            self._nodes[node_id] = {
                "node_id": self._node_counter,
                "labels": [label],
                "properties": dict(properties),
            }
            self._node_counter += 1

    def ensure_relationship_batch(
        self, from_spec, rel_type, to_spec, properties=None
    ) -> None:
        self._relationships.append({
            "from_id": from_spec[2],  # value
            "to_id": to_spec[2],
            "type": rel_type,
            "properties": dict(properties) if properties else {},
        })

    def flush_all(self) -> None:
        # Resolve relationship IDs and write JSON
        graph_data = self._build_graph_data()
        with open(self.output_path, "w") as f:
            json.dump(graph_data, f, indent=2)
```

### New Data Flow

```
Repository → Parse → JsonFileIngestor → JSON file
                            ↑
                     (no Memgraph needed)
```

### CLI Integration

Add new command in `cli.py`:

```python
@app.command()
def export_json(
    repo_path: str,
    output: str,
    exclude: list[str] | None = None,
):
    """Parse repository directly to JSON without Memgraph."""
    ingestor = JsonFileIngestor(output_path=output)
    parsers, queries = load_parsers()
    updater = GraphUpdater(ingestor, repo_to_index, parsers, queries, ...)
    updater.run()
```

---

## Batch Processing Architecture

### For Thousands of Repositories

```
┌─────────────────────────────────────────────────────────────┐
│                    Batch Processing                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   repos.txt          Worker Pool              Output Dir     │
│   ─────────         ────────────             ───────────     │
│   repo1/      ───►  [Process 1] ───►        repo1.json      │
│   repo2/      ───►  [Process 2] ───►        repo2.json      │
│   repo3/      ───►  [Process 3] ───►        repo3.json      │
│   ...               [Process N]              ...             │
│                                                              │
│   Each worker:                                               │
│   1. Load tree-sitter parsers (once per worker)              │
│   2. For each repo: GraphUpdater → JsonFileIngestor          │
│   3. Write JSON to output directory                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Python Multiprocessing Script

```python
from multiprocessing import Pool
from pathlib import Path

def process_repo(args):
    repo_path, output_dir = args
    output_file = output_dir / f"{repo_path.name}.json"

    ingestor = JsonFileIngestor(str(output_file))
    parsers, queries = load_parsers()
    updater = GraphUpdater(ingestor, repo_path, parsers, queries)
    updater.run()

    return str(output_file)

def batch_process(repo_list: Path, output_dir: Path, workers: int = 8):
    repos = [Path(line.strip()) for line in repo_list.read_text().splitlines()]
    args = [(repo, output_dir) for repo in repos]

    with Pool(workers) as pool:
        results = pool.map(process_repo, args)

    return results
```

### Performance Considerations

| Factor | Impact | Mitigation |
|--------|--------|------------|
| Tree-sitter parser loading | ~500ms per language | Load once per worker process |
| Large repos (>10k files) | Memory pressure | `BoundedASTCache` already handles this |
| Many small repos | Process spawn overhead | Use worker pool, not spawn per repo |
| Disk I/O | JSON write bottleneck | Use SSD, consider streaming writes |

### Estimated Throughput

Based on typical parsing performance:
- Small repo (100 files): ~2-5 seconds
- Medium repo (1000 files): ~10-30 seconds
- Large repo (10000 files): ~2-5 minutes

With 8 workers on 8-core machine:
- **1000 small repos**: ~20-40 minutes
- **1000 medium repos**: ~2-6 hours
- **1000 large repos**: ~4-10 hours

---

## Files to Modify/Create

### New Files

| File | Purpose | LOC |
|------|---------|-----|
| `codebase_rag/services/json_service.py` | JsonFileIngestor implementation | ~100 |
| `batch/batch_processor.py` | Multiprocessing batch script | ~150 |

### Modified Files

| File | Change |
|------|--------|
| `cli.py` | Add `export-json` command |
| `services/__init__.py` | Export JsonFileIngestor |

### No Changes Required

- `graph_updater.py` - Already uses IngestorProtocol
- `parsers/*` - All use IngestorProtocol
- `types_defs.py` - GraphData format already defined

---

## Alternative: Use Existing Protobuf Path

If you prefer binary format over JSON:

```bash
# Current capability - no Memgraph needed
cgr index --repo-path /path/to/repo -o output_dir/
```

Then convert protobuf to JSON post-hoc if needed.

---

## Recommendations

1. **Short term**: Create `JsonFileIngestor` (~2 hours work)
   - Follow ProtobufFileIngestor pattern
   - Add CLI command `cgr export-json`

2. **Batch processing**: Create multiprocessing wrapper (~2 hours work)
   - Worker pool for parallel processing
   - Progress tracking and error handling

3. **Future consideration**: If you need to query the graphs later
   - `GraphLoader` class already handles JSON files
   - Could add batch loading to merge multiple JSON graphs

---

## Conclusion

Memgraph is **optional** for your use case. The architecture already supports:
- Non-Memgraph ingestors (ProtobufFileIngestor proves this)
- In-memory graph building before persistence
- JSON format for both export and loading

A `JsonFileIngestor` implementation would give you direct:
```
Repository → JSON graph
```

Without any database dependency, perfect for batch processing thousands of codebases.
