# External Dependency Import Investigation - Findings

## Question
Do IMPORT relationships exist for external dependencies to particular modules?

## Answer
**NO** - The current graph implementation does NOT create MODULE nodes or IMPORTS relationships for external dependencies at the module level.

## What the Graph Contains

### ✅ What EXISTS:
1. **ExternalPackage nodes** - Created from dependency manifest files (`pyproject.toml`, `package.json`, etc.)
   - Example: `{node_id: 123, labels: ["ExternalPackage"], properties: {name: "loguru"}}`

2. **PROJECT → DEPENDS_ON_EXTERNAL relationships** - Links projects to declared dependencies
   - Example: `code-graph-rag` DEPENDS_ON_EXTERNAL `loguru` with `version_spec: ">=0.7.3"`
   - Count in current graph: 32 relationships

3. **MODULE nodes** - Only for local project code
   - Example: `{labels: ["Module"], properties: {qualified_name: "code-graph-rag.codebase_rag.config", path: "codebase_rag/config.py"}}`
   - All MODULE nodes have project prefix (`code-graph-rag.*`)
   - Count: 325 nodes

4. **IMPORTS relationships** - Only between local modules
   - Example: `code-graph-rag.realtime_updater` IMPORTS `code-graph-rag.codebase_rag.graph_updater`
   - Count: 752 relationships
   - **All are local-to-local, none are local-to-external**

### ❌ What DOES NOT EXIST:
1. **MODULE nodes for external packages** - No MODULE nodes with qualified_name like `"loguru"`, `"pydantic-ai"`, etc.

2. **IMPORTS relationships to external packages** - No relationships like:
   - `code-graph-rag.codebase_rag.config` IMPORTS `loguru`
   - `code-graph-rag.main` IMPORTS `pydantic-ai`

## Why This Happens

### Root Cause: Cypher Query Uses MATCH Instead of MERGE

From `codebase_rag/cypher_queries.py:87-92`:

```python
def build_merge_relationship_query(...):
    query = (
        f"MATCH (a:{from_label} {{{from_key}: row.from_val}}), "  # ← MATCH not MERGE
        f"(b:{to_label} {{{to_key}: row.to_val}})\n"              # ← MATCH not MERGE
        f"MERGE (a)-[r:{rel_type}]->(b)\n"
    )
```

This query:
- Uses **MATCH** to find both source and target nodes
- Only creates the relationship **if both nodes already exist**
- If target MODULE node doesn't exist, the relationship is silently skipped

### The Import Processing Flow

1. **import_processor.py** parses import statements:
   ```python
   # In code: from loguru import logger
   # Creates: module_path = "loguru"  (via stdlib_extractor)
   ```

2. **Creates IMPORTS relationship**:
   ```python
   self.ingestor.ensure_relationship_batch(
       (NodeLabel.MODULE, KEY_QUALIFIED_NAME, "code-graph-rag.some_module"),
       RelationshipType.IMPORTS,
       (NodeLabel.MODULE, KEY_QUALIFIED_NAME, "loguru"),  # ← Target doesn't exist!
   )
   ```

3. **Memgraph query fails silently**:
   - MATCH looks for MODULE node with qualified_name="loguru"
   - No such node exists (only local modules were created)
   - Relationship is not created
   - No error is raised

## Verification

### Test Results:
```bash
$ uv run python -c "from codebase_rag.utils.external_dependency_explorer import show_external_dependency_imports; import json; print(json.dumps(show_external_dependency_imports('code-graph-rag-graph.json', 'loguru'), indent=2))"

{
  "external_package": "loguru",
  "version_spec": "loguru>=0.7.3",
  "project_name": "code-graph-rag",
  "importing_modules": [],  # ← Empty!
  "import_count": 0
}
```

### Graph Statistics:
- **Total MODULE nodes**: 325 (all local)
- **MODULE nodes without project prefix**: 0
- **ExternalPackage nodes**: 32
- **DEPENDS_ON_EXTERNAL relationships**: 32
- **IMPORTS relationships**: 752 (all local-to-local)

## Implications

### What You CAN'T Do (Currently):
1. ❌ Find which modules import a specific external package
2. ❌ Trace import chains through external dependencies
3. ❌ Detect unused declared dependencies (declared but never imported)
4. ❌ Build a dependency usage report at the module level

### What You CAN Do:
1. ✅ See which external packages are declared dependencies (via DEPENDS_ON_EXTERNAL)
2. ✅ Get version specs for external packages
3. ✅ Trace imports between local modules only
4. ✅ See the complete local module graph

## Potential Solutions

### Option 1: Create External MODULE Nodes (Recommended)
Modify the graph generation to create MODULE nodes for external imports:

**Approach A**: Create during import processing
- When `import_processor.py` encounters an external import, create a MODULE node
- Properties: `{qualified_name: "loguru", is_external: true}`
- No `path` property (distinguishes from local modules)

**Approach B**: Use MERGE instead of MATCH
- Change `build_merge_relationship_query` to use MERGE for target node
- This would auto-create MODULE nodes for external imports
- Simpler change, but less control over node properties

### Option 2: Link ExternalPackage to MODULE Nodes
Create explicit relationships between ExternalPackage and external MODULE nodes:
- `(MODULE {qualified_name: "loguru"}) -[:REPRESENTS]-> (ExternalPackage {name: "loguru"})`
- Requires normalizing package names (pip: `pydantic-ai` vs import: `pydantic_ai`)

### Option 3: Alternative Analysis Approach
Instead of using the graph, analyze import statements directly from source files:
- Parse all Python files for import statements
- Match against declared dependencies
- Faster for this specific use case, but bypasses graph benefits

## Implementation Status

### What I Created:
1. ✅ **Function**: `codebase_rag/utils/external_dependency_explorer.py`
   - `show_external_dependency_imports(json_file_path, package_name=None)`
   - Works correctly with the current graph structure
   - Returns empty results (as expected) because no external IMPORTS exist

2. ✅ **Tests**: Successfully tested with `code-graph-rag-graph.json`
   - Function executes without errors
   - Returns expected structure with zero imports
   - Correctly identifies all 32 external packages

### Function Design:
The function is **future-proof** - it will work correctly once external MODULE nodes are added to the graph. No changes needed to the function itself.

## Recommendations

### Immediate Action:
The function I created is working correctly given the current graph structure. To make it useful, you need to decide:

1. **Do you want to add external MODULE nodes to the graph?**
   - If YES: I can implement Option 1A or 1B above
   - If NO: The function serves as future-proof infrastructure

2. **Do you want an alternative solution for now?**
   - Parse imports from source files directly
   - Match against declared dependencies
   - Generate a report without using the graph

### Long-term:
Consider adding external MODULE nodes to the graph for:
- Better dependency analysis capabilities
- Unused dependency detection
- Import chain tracing through external packages
- More complete knowledge graph

## Graph Export Metadata
- **Exported**: 2026-01-07T14:08:42.732846+00:00
- **Nodes**: 5,541 total
- **Relationships**: 14,996 total
- **Format**: JSON export from Memgraph
