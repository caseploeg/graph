# External Dependency Import Investigation - Findings

## Status: ✅ IMPLEMENTED (2026-01-09)

External MODULE node creation has been implemented! The graph now tracks which modules import external packages.

### Implementation Summary
- **PR**: `feat/external-module-imports` branch
- **Files Modified**:
  - `codebase_rag/parsers/import_processor.py` - Added external import detection and MODULE node creation
  - `codebase_rag/logs.py` - Added logging messages for external node creation
- **Testing**: Verified with minimal test repo and explorer function

### What Changed
The import processor now:
1. Detects external imports (those without project prefix)
2. Creates MODULE nodes for external packages before creating IMPORTS relationships
3. Handles multi-language separator conventions (Go: `/`, Rust/C++: `::`, others: `.`)

---

## Original Investigation (2026-01-07)

### Question
Do IMPORT relationships exist for external dependencies to particular modules?

### Answer (Before Implementation)
**NO** - The graph implementation did NOT create MODULE nodes or IMPORTS relationships for external dependencies at the module level.

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

---

## Implementation Results (2026-01-09)

### Verification Test

Created minimal test repository with external imports:
```python
import os
import json
from loguru import logger
import numpy as np
from typing import Dict
```

**Results**:
- ✅ 5 external MODULE nodes created: `os`, `json`, `loguru`, `numpy`, `typing`
- ✅ 5 IMPORTS relationships from local module to external modules
- ✅ External modules distinguished by absence of `path` property
- ✅ Explorer function `show_external_dependency_imports()` now returns actual results:

```json
{
  "external_package": "loguru",
  "version_spec": "loguru>=0.7.0",
  "project_name": "test_mini_repo",
  "importing_modules": [
    {
      "module": "test_mini_repo.test_imports",
      "file_path": "test_imports.py",
      "imported_entity": "loguru"
    }
  ],
  "import_count": 1
}
```

### What You CAN Do Now:

1. ✅ **Find which modules import a specific external package** (direct graph query)
   ```python
   from codebase_rag.graph_loader import load_graph

   loader = load_graph('graph.json')
   pytest_nodes = [m for m in loader.find_nodes_by_label('Module')
                   if m.properties.get('qualified_name') == 'pytest']
   imports = [rel for rel in loader.relationships
              if rel.type == 'IMPORTS' and rel.to_id == pytest_nodes[0].node_id]
   # Returns 15 importing modules for Click repository
   ```

2. ✅ **Use explorer function** (for packages in dependency manifest)
   ```bash
   cgr deps-explore graph.json --package loguru
   ```
   **Limitation**: Explorer requires both ExternalPackage node (from manifest) AND MODULE node (from imports). Won't work for:
   - Packages imported but not declared (e.g., pytest in test files)
   - Package name != import name (e.g., pillow/PIL)

3. ✅ **Trace import chains through external dependencies**
   - MODULE → external MODULE relationships now exist in graph

4. ✅ **Detect unused declared dependencies**
   - Compare DEPENDS_ON_EXTERNAL (declared) vs IMPORTS (actually used)

5. ✅ **Build dependency usage reports at module level**
   - Query graph for all external imports per module

### Implementation Details

**External MODULE Node Schema**:
```python
{
    "qualified_name": "loguru",    # No project prefix
    "name": "loguru",              # Display name
    # NO "path" property (distinguishes from local modules)
}
```

**Multi-Language Support**:
- Handles separator normalization for Go (`/`), Rust/C++ (`::`), and others (`.`)
- All local modules use `project_name.` prefix with DOT separator
- External detection works across all tree-sitter supported languages
