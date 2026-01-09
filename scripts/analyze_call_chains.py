import json
from collections import defaultdict
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

print("Interesting Call Chains (3-4 hops, cross-module):\n")

chains = []
for nid, node in nodes.items():
    if node['labels'][0] not in ('Function', 'Method'):
        continue
    q0 = get_qname(nid)
    if not is_prod(q0):
        continue

    for n1 in outgoing[nid]:
        q1 = get_qname(n1)
        if not is_prod(q1):
            continue
        for n2 in outgoing[n1]:
            q2 = get_qname(n2)
            if not is_prod(q2):
                continue
            for n3 in outgoing[n2]:
                q3 = get_qname(n3)
                if not is_prod(q3):
                    continue

                mods = {get_submodule(q) for q in [q0, q1, q2, q3]}
                mods.discard('')
                if len(mods) >= 2 and len({q0, q1, q2, q3}) == 4:
                    chains.append((q0, q1, q2, q3, mods))

seen_starts = set()
count = 0
for q0, q1, q2, q3, mods in chains:
    if short(q0) in seen_starts:
        continue
    seen_starts.add(short(q0))
    print(f"Chain {count+1}:")
    print(f"  {short(q0)}")
    print(f"    -> {short(q1)}")
    print(f"    -> {short(q2)}")
    print(f"    -> {short(q3)}")
    print(f"  Modules crossed: {mods}")
    print()
    count += 1
    if count >= 12:
        break

print("\n\nDeep chains (4+ hops):\n")

deep_chains = []
for nid, node in nodes.items():
    if node['labels'][0] not in ('Function', 'Method'):
        continue
    q0 = get_qname(nid)
    if not is_prod(q0):
        continue

    for n1 in outgoing[nid]:
        q1 = get_qname(n1)
        if not is_prod(q1):
            continue
        for n2 in outgoing[n1]:
            q2 = get_qname(n2)
            if not is_prod(q2):
                continue
            for n3 in outgoing[n2]:
                q3 = get_qname(n3)
                if not is_prod(q3):
                    continue
                for n4 in outgoing[n3]:
                    q4 = get_qname(n4)
                    if not is_prod(q4):
                        continue
                    if len({q0, q1, q2, q3, q4}) == 5:
                        deep_chains.append((q0, q1, q2, q3, q4))

seen = set()
for chain in deep_chains[:30]:
    key = short(chain[0])
    if key in seen:
        continue
    seen.add(key)
    print(f"  {short(chain[0])}")
    print(f"    -> {short(chain[1])}")
    print(f"    -> {short(chain[2])}")
    print(f"    -> {short(chain[3])}")
    print(f"    -> {short(chain[4])}")
    print()
    if len(seen) >= 5:
        break
