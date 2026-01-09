# Multi-Hop Question Generation for Code Graphs

Generate dense knowledge questions from code graph structures that require multi-hop reasoning and codebase exploration.

## Question Categories

### 1. Trace Questions (Call Chain Traversal)

Follow execution paths through multiple function calls across modules.

**Pattern**: Find paths of length 2-4 where nodes cross module boundaries.

**Examples**:
- "Trace the execution from `cli.start` to when a dependency file is identified. What intermediate functions are involved?"
- "When `realtime_updater.start_watcher` begins monitoring, what chain of calls eventually processes file changes?"
- "Follow the path from `parser_loader.load_parsers` to the creation of `LanguageQueries`. What transformations occur?"

**Graph Query Pattern**:
```cypher
MATCH path = (start:Function)-[:CALLS*2..4]->(end:Function)
WHERE start.module <> end.module
RETURN path
```

**Difficulty Factors**:
- Number of hops (2 = easy, 4+ = hard)
- Number of modules crossed
- Whether intermediate functions have side effects

---

### 2. Impact Questions (Reverse Dependency Analysis)

Understand what would break or change if a function/class is modified.

**Pattern**: Find all callers of a function, especially across module boundaries.

**Examples**:
- "If `FunctionRegistryTrieProtocol.get` returned `Optional[str]` instead of raising `KeyError`, which 13 modules would need updates?"
- "What functions depend on `GraphUpdater.run` completing successfully before they can proceed?"
- "If `safe_decode_text` changed its encoding handling, which parser components would be affected?"

**Graph Query Pattern**:
```cypher
MATCH (caller)-[:CALLS]->(target:Function {name: 'target_name'})
RETURN DISTINCT caller.module, count(caller) as impact_count
ORDER BY impact_count DESC
```

**Difficulty Factors**:
- Depth of reverse traversal
- Cross-module vs same-module impact
- Interface vs implementation changes

---

### 3. Resolution Questions (Method/Type Resolution)

Determine which implementation gets called in inheritance hierarchies.

**Pattern**: Follow INHERITS + DEFINES_METHOD + OVERRIDES edges.

**Examples**:
- "When `DefinitionProcessor._get_docstring` is called, does it use its own implementation or the one from `FunctionIngestMixin`?"
- "Which `DependencyParser` subclass's `parse` method handles `go.mod` files?"
- "If `JavaTypeInferenceEngine.build_variable_type_map` is invoked, which mixin originally defined this method?"

**Graph Query Pattern**:
```cypher
MATCH (child:Class)-[:INHERITS]->(parent:Class)-[:DEFINES_METHOD]->(method:Method)
WHERE NOT (child)-[:DEFINES_METHOD]->(:Method {name: method.name})
RETURN child.name, parent.name, method.name
```

**Difficulty Factors**:
- Single vs multiple inheritance
- Override chains (A overrides B which overrides C)
- Mixin composition order

---

### 4. Dependency Questions (Import/Usage Analysis)

Understand what a component needs to function.

**Pattern**: Follow IMPORTS, CALLS, DEPENDS_ON_EXTERNAL edges outward.

**Examples**:
- "What external packages does the `parsers` submodule depend on?"
- "Which type definitions from `types_defs` does `GraphUpdater` require?"
- "What shared utilities do both `CallProcessor` and `DefinitionProcessor` import?"

**Graph Query Pattern**:
```cypher
MATCH (m:Module {name: 'target_module'})-[:IMPORTS]->(dep)
RETURN dep.qualified_name
```

**Difficulty Factors**:
- Direct vs transitive dependencies
- Internal vs external dependencies
- Circular dependency detection

---

### 5. Bridge Questions (Architectural Connectors)

Identify functions that connect different parts of the system.

**Pattern**: Find functions called from many different modules.

**Examples**:
- "`FunctionRegistryTrieProtocol.get` is called from 13 different submodules. What architectural role does it serve?"
- "Why is `GraphUpdater.run` the convergence point for CLI, MCP, realtime updates, and tools?"
- "`parser_loader.load_parsers` is used by 4 different entry points. What does this reveal about the initialization pattern?"

**Graph Query Pattern**:
```cypher
MATCH (caller)-[:CALLS]->(bridge:Function)
WITH bridge, count(DISTINCT caller.module) as module_count
WHERE module_count >= 3
RETURN bridge.qualified_name, module_count
ORDER BY module_count DESC
```

**Difficulty Factors**:
- Number of calling modules
- Variety of calling contexts (CLI, tests, tools, etc.)
- Whether the bridge is a facade, adapter, or core utility

---

### 6. Override Questions (Behavioral Extension)

Understand how child classes modify parent behavior.

**Pattern**: Follow OVERRIDES edges and compare implementations.

**Examples**:
- "What does `PyProjectTomlParser.parse` add beyond the base `DependencyParser.parse` behavior?"
- "How does `OpenAIProvider.create_model` differ from `GoogleProvider.create_model`?"
- "When `JsTsIngestMixin._get_docstring` overrides the parent, what JS-specific logic does it add?"

**Graph Query Pattern**:
```cypher
MATCH (child_method:Method)-[:OVERRIDES]->(parent_method:Method)
RETURN child_method.qualified_name, parent_method.qualified_name
```

**Difficulty Factors**:
- Whether override adds, removes, or transforms behavior
- Chain of overrides
- Side effects in parent that child must preserve

---

## Question Difficulty Tiers

| Tier | Characteristics | Example |
|------|-----------------|---------|
| **Easy** | 1-2 hops, same module, direct relationship | "What does `cli.start` call?" |
| **Medium** | 2-3 hops, crosses 2 modules, some inference | "How does CLI trigger graph updates?" |
| **Hard** | 3-4 hops, crosses 3+ modules, requires reasoning | "Trace data flow from file watch event to graph storage" |
| **Expert** | 4+ hops, multiple inheritance, impact analysis | "If FunctionRegistryTrie changes, what's the full blast radius?" |

---

## Agent-Like Question Patterns

Questions that mirror what a code agent would ask during tasks:

### Bug Investigation
- "The test `test_parser_output` is failing. Which functions in the call chain from `parse_file` could have introduced the regression?"
- "Users report stale graph data. Trace the path from `realtime_updater` to `MemgraphIngestor` to find where updates might be lost."

### Feature Implementation
- "To add a new language handler, which base class methods must be overridden, and what existing handler is the best template?"
- "To add caching to `query_graph`, which upstream callers would benefit and which might break?"

### Refactoring
- "If I extract `_process_files` into its own class, what other methods would need to move with it to maintain cohesion?"
- "Which functions are tightly coupled to `FunctionRegistryTrie` and would need interface changes if it becomes async?"

### Code Review
- "This PR modifies `ImportProcessor.parse_imports`. What downstream functions depend on its output format?"
- "The new `RustHandler` overrides `extract_name`. Does it call `super()` like other handlers do?"

---

## Generation Algorithm

See `QUESTIONS.py` for implementation.

High-level approach:
1. Sample interesting subgraphs (high connectivity, cross-module, inheritance)
2. Identify path patterns that require multi-hop traversal
3. Template questions based on edge types and node relationships
4. Add surrounding code context for semantic depth
5. Rank by obscurity (prefer questions that aren't answerable from a single file)
