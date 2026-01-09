# Graph-Code Setup Notes

This document describes the steps we took to set up Graph-Code and create a knowledge graph of this repository.

## What We Did

### 1. Created Environment Configuration

```bash
# Copied the example environment file
cp .env.example .env
```

The `.env` file is configured to use Ollama (local models) by default. You can edit it to use:
- **Google Gemini**: Add `ORCHESTRATOR_API_KEY` and `CYPHER_API_KEY` with your Google API key
- **OpenAI**: Change provider to `openai` and add your OpenAI API key

### 2. Started Docker and Memgraph

```bash
# Started Memgraph database and Memgraph Lab
docker-compose up -d

# Verified containers are running
docker-compose ps
```

**Services started:**
- `memgraph`: Graph database on port 7687
- `lab`: Memgraph Lab web interface on port 3000

### 3. Parsed This Repository

```bash
# Parsed the repository and ingested it into the graph database
uv run cgr start --repo-path /Users/caseploeg/code-graph-rag --update-graph --clean
```

**What this did:**
- Parsed all Python files in the repository
- Extracted functions, classes, methods, imports
- Resolved function call relationships
- Created nodes and relationships in Memgraph
- Used the **three-pass algorithm**:
  1. **Pass 1**: Identified packages, folders, modules
  2. **Pass 2**: Parsed all function/class definitions
  3. **Pass 3**: Resolved all CALLS relationships

**Results:**
- 157 Python files processed
- 898 functions discovered
- 7,334 CALLS relationships resolved
- 34 external dependencies tracked

### 4. Exported Graph to JSON

```bash
# Exported the entire graph to JSON for offline exploration
uv run cgr export -o code-graph-rag-graph.json
```

**Export contains:**
- 5,541 nodes
- 14,996 relationships

## Quick Reproduction

To reproduce these results, simply run:

```bash
./setup.sh
```

The script will:
1. Check prerequisites (uv, Docker)
2. Create `.env` if needed
3. Start Memgraph
4. Parse the repository
5. Export the graph to JSON

## Exploring the Results

### Option 1: Memgraph Lab (Visual Interface)

1. Open http://localhost:3000 in your browser
2. Connect to `memgraph:7687`
3. Run Cypher queries from `CYPHER.md`

**Example queries:**

```cypher
# Overview of node types
MATCH (n)
RETURN labels(n) as NodeType, count(*) as Count
ORDER BY Count DESC

# Find most-called functions
MATCH (f:Function)<-[:CALLS]-(caller)
RETURN f.name, f.qualified_name, count(caller) as CallCount
ORDER BY CallCount DESC
LIMIT 10

# Visualize a module
MATCH (m:Module {name: "main.py"})-[r]-(related)
RETURN m, r, related
LIMIT 50
```

### Option 2: JSON Export

Explore the exported graph programmatically:

```bash
# Pretty print
python -m json.tool code-graph-rag-graph.json | less

# Count nodes by type (requires jq)
jq '.nodes | group_by(.labels[0]) | map({type: .[0].labels[0], count: length})' code-graph-rag-graph.json

# Find all functions
jq '.nodes[] | select(.labels[] == "Function") | .properties.qualified_name' code-graph-rag-graph.json | head -20
```

### Option 3: Interactive Query Mode

To use natural language queries with an LLM agent:

**With Ollama (local, free):**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull models
ollama pull llama3.2
ollama pull codellama

# Run interactive mode
uv run cgr start --repo-path /Users/caseploeg/code-graph-rag
```

**With Google Gemini:**
```bash
# Edit .env and uncomment/set:
# ORCHESTRATOR_PROVIDER=google
# ORCHESTRATOR_MODEL=gemini-2.5-pro
# ORCHESTRATOR_API_KEY=your-key
# CYPHER_PROVIDER=google
# CYPHER_MODEL=gemini-2.5-flash
# CYPHER_API_KEY=your-key

# Run interactive mode
uv run cgr start --repo-path /Users/caseploeg/code-graph-rag
```

## Cleanup

When you're done exploring:

```bash
# Stop Memgraph
docker-compose down

# Remove graph data (if you want to start fresh)
docker-compose down -v
```

## Files Created

- `.env` - Environment configuration
- `code-graph-rag-graph.json` - Exported graph data (5,541 nodes, 14,996 relationships)
- `CYPHER.md` - Cypher query reference guide
- `CLAUDE.md` - Development guide for Claude Code
- `setup.sh` - Automated setup script
- `SETUP_NOTES.md` - This file

## Architecture Notes

The knowledge graph created has the following structure:

**Node Types:**
- `Project` - Root node for the repository
- `Package` - Python packages (directories with `__init__.py`)
- `Folder` - Regular directories
- `Module` - Python source files
- `Function` - Top-level functions
- `Method` - Class methods
- `Class` - Class definitions
- `ExternalPackage` - External dependencies from pyproject.toml

**Relationship Types:**
- `CONTAINS_PACKAGE`, `CONTAINS_FOLDER`, `CONTAINS_MODULE` - Structural hierarchy
- `DEFINES` - Module defines Function/Class
- `DEFINES_METHOD` - Class defines Method
- `CALLS` - Function/Method calls another Function/Method
- `INHERITS` - Class inherits from another Class
- `IMPLEMENTS` - Class implements Interface
- `IMPORTS` - Module imports another Module
- `DEPENDS_ON_EXTERNAL` - Project depends on external package

## Performance Notes

- **Parsing took:** ~2 minutes for 157 files
- **Graph size:** 5,541 nodes, 14,996 relationships
- **Export size:** ~5-10 MB JSON file
- **Memory usage:** Memgraph uses ~500MB RAM for this graph

## Next Steps

1. **Explore patterns** in `CYPHER.md` to understand the codebase structure
2. **Set up LLM** (Ollama or cloud) for natural language queries
3. **Try the MCP server** integration with Claude Desktop (see README.md)
4. **Parse other repositories** and compare their structures

## Troubleshooting

**Docker not running:**
```bash
# macOS with Docker Desktop
open -a Docker

# macOS with Colima
colima start
```

**Memgraph connection issues:**
```bash
# Check if containers are running
docker-compose ps

# View logs
docker-compose logs memgraph

# Restart Memgraph
docker-compose restart
```

**Parsing errors:**
```bash
# Check logs for details
# Common issues:
# - Missing tree-sitter grammar: Install with cgr language add-grammar <lang>
# - Syntax errors in code files: These are logged but don't stop parsing
```

## References

- [Graph-Code README](README.md) - Full project documentation
- [CLAUDE.md](CLAUDE.md) - Development guide for this repository
- [CYPHER.md](CYPHER.md) - Cypher query examples
- [Memgraph Documentation](https://memgraph.com/docs)
- [Cypher Query Language](https://neo4j.com/docs/cypher-manual/current/)
