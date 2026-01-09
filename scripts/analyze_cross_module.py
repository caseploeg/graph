import json
from collections import defaultdict, Counter
from pathlib import Path

data = json.loads(Path('code-graph-rag-graph.json').read_text())
nodes = {n['node_id']: n for n in data.get('nodes', [])}
edges = data.get('relationships', [])

outgoing = defaultdict(list)
for e in edges:
    if e['type'] == 'CALLS':
        outgoing[e['from_id']].append(e['to_id'])

def get_qname(nid):
    if nid not in nodes:
        return None
    props = nodes[nid].get('properties', {})
    return props.get('qualified_name', props.get('name'))

def is_prod(qn):
    return qn and 'test' not in qn.lower() and 'conftest' not in qn.lower()

def short(qn):
    return qn.replace('code-graph-rag.codebase_rag.', '') if qn else ''

def get_submodule(qn):
    s = short(qn)
    parts = s.split('.')
    return parts[0] if parts else ''

cross = []
for nid, node in nodes.items():
    if node['labels'][0] not in ('Function', 'Method'):
        continue
    caller = get_qname(nid)
    if not is_prod(caller):
        continue
    for tid in outgoing[nid]:
        callee = get_qname(tid)
        if not is_prod(callee):
            continue
        cm, ce = get_submodule(caller), get_submodule(callee)
        if cm and ce and cm != ce:
            cross.append((short(caller), short(callee), cm, ce))

pairs = Counter((cm, ce) for _, _, cm, ce in cross)
print("Cross-submodule call patterns (production code):\n")
for (src, tgt), count in pairs.most_common(25):
    print(f"  {count:3d}x  {src} -> {tgt}")

print("\n\nExample calls for top patterns:\n")
seen_pairs = set()
for caller, callee, cm, ce in cross:
    key = (cm, ce)
    if key in seen_pairs:
        continue
    if pairs[key] >= 5:
        print(f"{cm} -> {ce}:")
        print(f"  {caller}")
        print(f"    calls {callee}")
        print()
        seen_pairs.add(key)
        if len(seen_pairs) >= 10:
            break
