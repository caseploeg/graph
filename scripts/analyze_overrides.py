import json
from collections import defaultdict
from pathlib import Path

data = json.loads(Path('code-graph-rag-graph.json').read_text())
nodes = {n['node_id']: n for n in data.get('nodes', [])}
edges = data.get('relationships', [])

def get_qname(nid):
    if nid not in nodes:
        return None
    props = nodes[nid].get('properties', {})
    return props.get('qualified_name', props.get('name'))

def short(qn):
    return qn.replace('code-graph-rag.codebase_rag.', '') if qn else ''

def is_prod(qn):
    return qn and 'test' not in qn.lower() and 'conftest' not in qn.lower()

print("Method Overrides:\n")
overrides = []
for e in edges:
    if e['type'] == 'OVERRIDES':
        child = get_qname(e['from_id'])
        parent = get_qname(e['to_id'])
        if is_prod(child) and is_prod(parent):
            overrides.append((child, parent))

for child, parent in overrides:
    print(f"  {short(child)}")
    print(f"    overrides -> {short(parent)}")
    print()

print("\n" + "=" * 60)
print("Inheritance with method details:\n")

inherits_map = defaultdict(list)
for e in edges:
    if e['type'] == 'INHERITS':
        child = get_qname(e['from_id'])
        parent = get_qname(e['to_id'])
        if is_prod(child):
            inherits_map[e['from_id']].append((child, parent, e['to_id']))

defines_method = defaultdict(list)
for e in edges:
    if e['type'] == 'DEFINES_METHOD':
        cls_qn = get_qname(e['from_id'])
        method_qn = get_qname(e['to_id'])
        if is_prod(cls_qn):
            defines_method[e['from_id']].append(method_qn)

for class_id, parents in inherits_map.items():
    if not parents:
        continue
    child_qn = parents[0][0]
    child_methods = defines_method.get(class_id, [])

    print(f"{short(child_qn)}")
    print(f"  Methods: {[short(m).split('.')[-1] for m in child_methods[:5]]}")
    for _, parent_qn, parent_id in parents:
        parent_methods = defines_method.get(parent_id, [])
        print(f"  Inherits: {short(parent_qn)}")
        print(f"    Parent methods: {[short(m).split('.')[-1] for m in parent_methods[:5]]}")
    print()

print("\n" + "=" * 60)
print("External Dependencies:\n")

for e in edges:
    if e['type'] == 'DEPENDS_ON_EXTERNAL':
        src = get_qname(e['from_id'])
        pkg = get_qname(e['to_id'])
        if is_prod(src):
            print(f"  {short(src)} -> {pkg}")
