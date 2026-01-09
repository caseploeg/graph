# Cypher Query Reference for Graph-Code

This file contains useful Cypher queries for exploring the code graph in Memgraph Lab.

## Accessing Memgraph Lab

1. Open **http://localhost:3000** in your browser
2. Connect to the database:
   - Host: `memgraph` (or `localhost`)
   - Port: `7687`
3. Run queries in the query editor

## Cypher Basics

Cypher uses patterns to match graph structures:
- `()` = node
- `[]` = relationship
- `->` = directed relationship
- `-` = any direction
- `MATCH` = find patterns
- `RETURN` = what to return
- `WHERE` = filter conditions
- `LIMIT` = limit results

## Overview Queries

### Get an overview of all node types
```cypher
MATCH (n)
RETURN labels(n) as NodeType, count(*) as Count
ORDER BY Count DESC
```

### Count all relationships by type
```cypher
MATCH ()-[r]->()
RETURN type(r) as RelationshipType, count(*) as Count
ORDER BY Count DESC
```

### Get graph statistics
```cypher
MATCH (n)
WITH count(n) as NodeCount
MATCH ()-[r]->()
RETURN NodeCount, count(r) as RelationshipCount
```

## Module and Package Queries

### Find all modules in the codebase
```cypher
MATCH (m:Module)
RETURN m.name, m.qualified_name, m.path
LIMIT 50
```

### Find all packages and their modules
```cypher
MATCH (pkg:Package)-[:CONTAINS_MODULE]->(m:Module)
RETURN pkg.name as Package, collect(m.name) as Modules
LIMIT 20
```

### Explore the structure of a specific package
```cypher
MATCH path = (pkg:Package {name: "parsers"})-[:CONTAINS_MODULE*1..2]->(m:Module)
RETURN path
LIMIT 20
```

### Find all Python files in a specific package
```cypher
MATCH (pkg:Package)-[:CONTAINS_MODULE]->(m:Module)
WHERE pkg.qualified_name STARTS WITH 'code-graph-rag.codebase_rag.parsers'
RETURN m.name, m.path
ORDER BY m.name
```

## Function Queries

### Find all functions in a specific module
```cypher
MATCH (m:Module {name: "graph_updater.py"})-[:DEFINES]->(f:Function)
RETURN f.name, f.qualified_name, f.start_line, f.end_line
LIMIT 20
```

### Find all functions in the parsers package
```cypher
MATCH (f:Function)
WHERE f.qualified_name STARTS WITH 'code-graph-rag.codebase_rag.parsers'
RETURN f.name, f.qualified_name
LIMIT 50
```

### Find functions by name (fuzzy search)
```cypher
MATCH (f:Function)
WHERE f.name CONTAINS 'parse'
RETURN f.name, f.qualified_name
LIMIT 20
```

### Find the longest functions (by line count)
```cypher
MATCH (f:Function)
WHERE f.end_line IS NOT NULL AND f.start_line IS NOT NULL
WITH f, (f.end_line - f.start_line) as LineCount
RETURN f.name, f.qualified_name, LineCount
ORDER BY LineCount DESC
LIMIT 20
```

## Class Queries

### Find all classes and their methods
```cypher
MATCH (c:Class)-[:DEFINES_METHOD]->(m:Method)
RETURN c.name as ClassName, c.qualified_name, collect(m.name) as Methods
LIMIT 20
```

### Find classes with the most methods
```cypher
MATCH (c:Class)-[:DEFINES_METHOD]->(m:Method)
RETURN c.name, c.qualified_name, count(m) as MethodCount
ORDER BY MethodCount DESC
LIMIT 20
```

### Find all classes that inherit from another class
```cypher
MATCH (child:Class)-[:INHERITS]->(parent:Class)
RETURN child.name as Child, child.qualified_name, parent.name as Parent
```

### Find class inheritance hierarchies
```cypher
MATCH path = (child:Class)-[:INHERITS*1..3]->(ancestor:Class)
RETURN path
LIMIT 20
```

### Find classes that implement interfaces
```cypher
MATCH (c:Class)-[:IMPLEMENTS]->(i:Interface)
RETURN c.name as Class, i.name as Interface
```

## Function Call Queries

### Find which functions call a specific function
```cypher
MATCH (caller:Function)-[:CALLS]->(target:Function {name: "load_parsers"})
RETURN caller.qualified_name as Caller, caller.name
LIMIT 20
```

### Find what a specific function calls
```cypher
MATCH (f:Function {name: "load_parsers"})-[:CALLS]->(called:Function)
RETURN called.qualified_name as Called, called.name
LIMIT 20
```

### Find the most-called functions (top dependencies)
```cypher
MATCH (f:Function)<-[:CALLS]-(caller)
RETURN f.name, f.qualified_name, count(caller) as CallCount
ORDER BY CallCount DESC
LIMIT 20
```

### Find functions with the most outgoing calls
```cypher
MATCH (f:Function)-[:CALLS]->(called)
RETURN f.name, f.qualified_name, count(called) as FunctionsItCalls
ORDER BY FunctionsItCalls DESC
LIMIT 20
```

### Find call chains (who calls who)
```cypher
MATCH path = (f1:Function)-[:CALLS*1..3]->(f2:Function)
WHERE f1.name = 'main'
RETURN path
LIMIT 10
```

### Find circular dependencies (functions that call each other)
```cypher
MATCH (f1:Function)-[:CALLS]->(f2:Function)-[:CALLS]->(f1)
RETURN f1.qualified_name, f2.qualified_name
LIMIT 20
```

## Import and Dependency Queries

### Find all imports for a module
```cypher
MATCH (m:Module {name: "main.py"})-[:IMPORTS]->(imported:Module)
RETURN imported.name, imported.qualified_name
```

### Find which modules import a specific module
```cypher
MATCH (importer:Module)-[:IMPORTS]->(target:Module {name: "graph_service.py"})
RETURN importer.name, importer.path
```

### Find all external dependencies
```cypher
MATCH (p:Project)-[:DEPENDS_ON_EXTERNAL]->(dep:ExternalPackage)
RETURN dep.name, dep.version_spec
ORDER BY dep.name
```

### Find modules that import the most other modules
```cypher
MATCH (m:Module)-[:IMPORTS]->(imported)
RETURN m.name, m.qualified_name, count(imported) as ImportCount
ORDER BY ImportCount DESC
LIMIT 20
```

## Visualization Queries

### Visualize a module and its immediate relationships
```cypher
MATCH (m:Module {name: "main.py"})-[r]-(related)
RETURN m, r, related
LIMIT 50
```

### Visualize a package structure
```cypher
MATCH path = (pkg:Package {name: "parsers"})-[:CONTAINS_MODULE]->(m:Module)-[:DEFINES]->(f:Function)
RETURN path
LIMIT 30
```

### Visualize function call relationships in a module
```cypher
MATCH (m:Module {name: "graph_updater.py"})-[:DEFINES]->(f:Function)
MATCH (f)-[c:CALLS]->(called:Function)
RETURN f, c, called
LIMIT 50
```

### Visualize class hierarchies
```cypher
MATCH path = (c:Class)-[:INHERITS|DEFINES_METHOD*1..2]-(related)
WHERE c.name CONTAINS 'Handler'
RETURN path
LIMIT 30
```

## Advanced Queries

### Find "god classes" (classes with many methods and relationships)
```cypher
MATCH (c:Class)-[r]-(related)
RETURN c.name, c.qualified_name, count(r) as RelationshipCount
ORDER BY RelationshipCount DESC
LIMIT 10
```

### Find isolated functions (no calls in or out)
```cypher
MATCH (f:Function)
WHERE NOT (f)-[:CALLS]->() AND NOT ()-[:CALLS]->(f)
RETURN f.name, f.qualified_name
LIMIT 20
```

### Find functions that are entry points (called but don't call others)
```cypher
MATCH (f:Function)<-[:CALLS]-()
WHERE NOT (f)-[:CALLS]->()
RETURN f.name, f.qualified_name
LIMIT 20
```

### Find the "distance" between two functions (shortest path)
```cypher
MATCH path = shortestPath(
  (f1:Function {name: "main"})-[:CALLS*..10]->(f2:Function {name: "load_parsers"})
)
RETURN path
```

### Find functions in a module that call functions in another module
```cypher
MATCH (m1:Module {name: "main.py"})-[:DEFINES]->(f1:Function)
MATCH (m2:Module {name: "graph_service.py"})-[:DEFINES]->(f2:Function)
MATCH (f1)-[:CALLS]->(f2)
RETURN f1.name as Caller, f2.name as Called
```

### Find all decorators used on functions
```cypher
MATCH (f:Function)
WHERE f.decorators IS NOT NULL AND size(f.decorators) > 0
RETURN f.name, f.qualified_name, f.decorators
LIMIT 20
```

## Search Queries

### Search for nodes by name (case-insensitive)
```cypher
MATCH (n)
WHERE toLower(n.name) CONTAINS 'parser'
RETURN labels(n) as Type, n.name, n.qualified_name
LIMIT 50
```

### Find all nodes with a specific property
```cypher
MATCH (n)
WHERE n.qualified_name IS NOT NULL
RETURN labels(n)[0] as Type, n.qualified_name
LIMIT 50
```

### Find functions with specific decorators
```cypher
MATCH (f:Function)
WHERE any(decorator IN f.decorators WHERE decorator CONTAINS 'staticmethod')
RETURN f.name, f.qualified_name, f.decorators
```

## Tips for Using Memgraph Lab

1. **Visualizations**: Queries that return nodes and relationships will show a graph visualization
2. **Table view**: Use the tabs to switch between graph and table views
3. **Limit results**: Always use `LIMIT` to avoid overwhelming results (especially for visualization)
4. **Explore nodes**: Click on nodes in the visualization to see their properties
5. **Export**: You can export query results as CSV or JSON
6. **Style nodes**: Use the style editor to customize how different node types appear
7. **Query history**: Access previous queries using the up/down arrow keys

## Exploring the JSON Export

If you've exported the graph to JSON (`code-graph-rag-graph.json`), you can explore it with:

```bash
# Pretty print the structure
python -m json.tool code-graph-rag-graph.json | less

# Count nodes by type using jq
jq '.nodes | group_by(.labels[0]) | map({type: .[0].labels[0], count: length})' code-graph-rag-graph.json

# Find all functions
jq '.nodes[] | select(.labels[] == "Function") | .properties.qualified_name' code-graph-rag-graph.json | head -20

# Count relationships by type
jq '.relationships | group_by(.type) | map({type: .[0].type, count: length})' code-graph-rag-graph.json
```

## Common Graph Patterns

### Hub Pattern (highly connected nodes)
```cypher
MATCH (n)-[r]-()
RETURN labels(n)[0] as Type, n.name, count(r) as Connections
ORDER BY Connections DESC
LIMIT 20
```

### Bridge Pattern (nodes connecting different clusters)
```cypher
MATCH (a)-[:CALLS]->(bridge:Function)-[:CALLS]->(b)
WHERE a.qualified_name STARTS WITH 'code-graph-rag.codebase_rag.parsers'
  AND b.qualified_name STARTS WITH 'code-graph-rag.codebase_rag.services'
RETURN DISTINCT bridge.name, bridge.qualified_name
LIMIT 20
```

### Star Pattern (one node connected to many)
```cypher
MATCH (center:Class)-[:DEFINES_METHOD]->(method:Method)
WITH center, count(method) as MethodCount
WHERE MethodCount > 5
RETURN center.name, center.qualified_name, MethodCount
ORDER BY MethodCount DESC
```
