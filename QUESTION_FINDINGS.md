# Question Generation Findings

Analysis of code-graph-rag graph structure for multi-hop question generation.

## Graph Statistics

- **Nodes**: 5,541
- **Edges**: 14,996
- **Node Types**: Method (2754), Function (1412), Class (627), File (362), Module (325), ExternalPackage (32), Package (16), Folder (12), Project (1)
- **Edge Types**: CALLS (8634), DEFINES_METHOD (2754), DEFINES (2039), IMPORTS (752), CONTAINS_FILE (362), CONTAINS_MODULE (325), OVERRIDES (45), DEPENDS_ON_EXTERNAL (32), INHERITS (25), CONTAINS_PACKAGE (16)

## Pattern 1: Top Callers (High Fan-Out)

Functions that call many others - good for "entry point" questions.

| Calls | Function |
|-------|----------|
| 21 | `main._initialize_services_and_agent` |
| 15 | `DefinitionProcessor.process_file` |
| 15 | `MCPToolsRegistry.__init__` |
| 13 | `readme_sections.generate_all_sections` |
| 12 | `ImportProcessor.parse_imports` |
| 12 | `cli.start` |

**Question archetype**: "What services does `_initialize_services_and_agent` set up before the agent loop begins?"

## Pattern 2: Most Called (High Fan-In)

Functions called by many others - critical shared utilities.

| Callers | Function |
|---------|----------|
| 186 | `parser_loader.load_parsers` |
| 175 | `GraphUpdater.run` |
| 152 | `parsers.utils.safe_decode_text` |
| 148 | `FunctionRegistryTrieProtocol.get` |

**Question archetype**: "Which components depend on `FunctionRegistryTrie.get` and what happens if it returns None?"

## Pattern 3: Inheritance Hierarchies

### DependencyParser Family
- `PyProjectTomlParser` -> `DependencyParser`
- `PackageJsonParser` -> `DependencyParser`
- `GoModParser` -> `DependencyParser`
- `CargoTomlParser` -> `DependencyParser`
- `RequirementsTxtParser` -> `DependencyParser`
- `GemfileParser` -> `DependencyParser`
- `CsprojParser` -> `DependencyParser`
- `ComposerJsonParser` -> `DependencyParser`

**Question archetype**: "What method must all DependencyParser subclasses implement, and how does GoModParser's implementation differ from PyProjectTomlParser?"

### ModelProvider Family
- `OpenAIProvider` -> `ModelProvider`
- `GoogleProvider` -> `ModelProvider`
- `OllamaProvider` -> `ModelProvider`

### LanguageHandler Family
- `LuaHandler` -> `BaseLanguageHandler`
- `JavaHandler` -> `BaseLanguageHandler`
- `CppHandler` -> `BaseLanguageHandler`
- `JsTsHandler` -> `BaseLanguageHandler`
- `RustHandler` -> `BaseLanguageHandler`

**Question archetype**: "Which method does `JsTsHandler` override from `BaseLanguageHandler` to handle ES6 module exports?"

### Multiple Inheritance (Complex)
- `PythonTypeInferenceEngine` inherits from:
  - `PythonExpressionAnalyzerMixin`
  - `PythonAstAnalyzerMixin`
  - `PythonVariableAnalyzerMixin`

- `JavaTypeInferenceEngine` inherits from:
  - `JavaTypeResolverMixin`
  - `JavaMethodResolverMixin`
  - `JavaVariableAnalyzerMixin`

**Question archetype**: "When `PythonTypeInferenceEngine` resolves a variable's type, which mixin provides the `analyze_assignment` method?"

## Pattern 4: Cross-Module Dependencies

Top submodule interaction patterns (production code):

| Count | Source -> Target |
|-------|------------------|
| 87 | parsers -> types_defs |
| 33 | parsers -> services |
| 22 | services -> types_defs |
| 17 | main -> tools |
| 15 | cli -> main |
| 14 | tools -> types_defs |
| 13 | parsers -> graph_updater |
| 11 | mcp -> tools |

**Example cross-module calls:**
- `cli.start` -> `main.prompt_for_included_directories`
- `main._initialize_services_and_agent` -> `tools.file_editor.create_file_editor_tool`
- `graph_updater.GraphUpdater.__init__` -> `parsers.factory.ProcessorFactory`

**Question archetype**: "When the CLI starts the agent, which module is responsible for creating the file editor tool?"

## Pattern 5: Call Chains

### 3-Hop Chains (Cross-Module)

1. **Realtime updater flow:**
   ```
   realtime_updater.start_watcher
     -> realtime_updater._run_watcher_loop
     -> graph_updater.GraphUpdater.run
     -> graph_updater.GraphUpdater._process_files
   ```

2. **CLI to graph update:**
   ```
   cli.start
     -> graph_updater.GraphUpdater.run
     -> graph_updater.GraphUpdater._process_files
     -> graph_updater.GraphUpdater._is_dependency_file
   ```

3. **Parser loading chain:**
   ```
   parser_loader.load_parsers
     -> parser_loader._process_language
     -> parser_loader._create_language_queries
     -> types_defs.LanguageQueries
   ```

4. **Multi-module chain:**
   ```
   readme_sections.extract_dependencies
     -> unixcoder.UniXcoder.decode
     -> cli.index
     -> graph_updater.GraphUpdater.run
   ```
   (Crosses: readme_sections, unixcoder, cli, graph_updater)

**Question archetype**: "Trace the execution path from `cli.start` to when a file is determined to be a dependency file. What modules are involved?"

## Pattern 6: Bridge Functions

Functions called from multiple submodules (architectural importance):

| Modules | Function |
|---------|----------|
| 13 | `types_defs.FunctionRegistryTrieProtocol.get` |
| 8 | `graph_updater.GraphUpdater.run` |
| 6 | `unixcoder.UniXcoder.decode` |
| 6 | `types_defs.FunctionRegistryTrieProtocol.items` |
| 5 | `types_defs.FunctionRegistryTrieProtocol.keys` |
| 4 | `parser_loader.load_parsers` |

**Question archetype**: "Which 13 different submodules depend on `FunctionRegistryTrieProtocol.get`? What shared concern does this reveal?"

## Pattern 7: Hub Functions

Functions with high incoming AND outgoing connections (orchestrators):

| Function | In | Out |
|----------|-----|-----|
| `GraphUpdater.run` | 31 | 7 |
| `main._initialize_services_and_agent` | 3 | 21 |
| `GraphLoader.load` | 16 | 3 |
| `DefinitionProcessor.process_file` | 4 | 15 |
| `cli.start` | 6 | 12 |

**Question archetype**: "`GraphUpdater.run` is called by 31 different functions and itself calls 7 others. What makes it the central orchestration point for graph updates?"

## Pattern 8: Method Overrides

Notable override patterns:

### DependencyParser.parse (8 implementations)
- `PyProjectTomlParser.parse` -> `DependencyParser.parse`
- `PackageJsonParser.parse` -> `DependencyParser.parse`
- `GoModParser.parse` -> `DependencyParser.parse`
- `CargoTomlParser.parse` -> `DependencyParser.parse`
- etc.

### ModelProvider methods (4 each for 3 providers)
- `__init__`, `provider_name`, `validate_config`, `create_model`

### DefinitionProcessor overrides from mixins
- `_get_docstring` -> `FunctionIngestMixin._get_docstring`
- `_extract_decorators` -> `FunctionIngestMixin._extract_decorators`

**Question archetype**: "When `DefinitionProcessor` extracts decorators, which mixin's implementation does it override? What additional behavior does it add?"

## Derived Question Categories

Based on these patterns, questions fall into these categories:

1. **Trace Questions**: Follow execution through call chains
2. **Impact Questions**: What breaks if X changes?
3. **Resolution Questions**: Which implementation gets called?
4. **Dependency Questions**: What does X depend on?
5. **Bridge Questions**: Why is X called from so many places?
6. **Override Questions**: What does the child add/change?
