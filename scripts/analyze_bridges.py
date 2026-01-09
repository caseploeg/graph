import json
from collections import defaultdict
from pathlib import Path

data = json.loads(Path('code-graph-rag-graph.json').read_text())
nodes = {n['node_id']: n for n in data.get('nodes', [])}
edges = data.get('relationships', [])

incoming = defaultdict(list)
for e in edges:
    if e['type'] == 'CALLS':
        incoming[e['to_id']].append(e['from_id'])

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

print("Bridge Functions (called from multiple submodules):\n")

bridge_data = []
for nid, node in nodes.items():
    if node['labels'][0] not in ('Function', 'Method'):
        continue
    target_qn = get_qname(nid)
    if not is_prod(target_qn):
        continue

    caller_modules = set()
    callers = []
    for cid in incoming[nid]:
        caller_qn = get_qname(cid)
        if is_prod(caller_qn):
            mod = get_submodule(caller_qn)
            if mod:
                caller_modules.add(mod)
                callers.append(caller_qn)

    if len(caller_modules) >= 2:
        bridge_data.append((len(caller_modules), target_qn, caller_modules, callers))

bridge_data.sort(reverse=True)

for count, target, mods, callers in bridge_data[:15]:
    print(f"{short(target)}")
    print(f"  Called by {count} different submodules: {mods}")
    print(f"  Sample callers:")
    for c in callers[:3]:
        print(f"    - {short(c)}")
    print()

print("\n" + "=" * 60)
print("Hub Functions (many incoming + outgoing calls):\n")

outgoing = defaultdict(list)
for e in edges:
    if e['type'] == 'CALLS':
        outgoing[e['from_id']].append(e['to_id'])

hub_data = []
for nid, node in nodes.items():
    if node['labels'][0] not in ('Function', 'Method'):
        continue
    qn = get_qname(nid)
    if not is_prod(qn):
        continue

    in_count = len([c for c in incoming[nid] if is_prod(get_qname(c))])
    out_count = len([c for c in outgoing[nid] if is_prod(get_qname(c))])

    if in_count >= 3 and out_count >= 3:
        hub_data.append((in_count + out_count, in_count, out_count, qn))

hub_data.sort(reverse=True)
for total, inc, outc, qn in hub_data[:10]:
    print(f"{short(qn)}")
    print(f"  {inc} callers, {outc} callees")
    print()
