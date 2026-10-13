import json
from pathlib import Path

# 1. Merge AST + semantic chunk
ast = json.loads(Path('graphify-out/.graphify_ast.json').read_text())
chunk = json.loads(Path('graphify-out/.graphify_chunk_01.json').read_text())

seen = {n['id'] for n in ast['nodes']}
merged_nodes = list(ast['nodes'])
for n in chunk['nodes']:
    if n['id'] not in seen:
        merged_nodes.append(n)
        seen.add(n['id'])

merged_edges = ast['edges'] + chunk['edges']
merged_hyperedges = chunk.get('hyperedges', [])

extract = {
    'nodes': merged_nodes,
    'edges': merged_edges,
    'hyperedges': merged_hyperedges,
    'input_tokens': chunk.get('input_tokens', 0),
    'output_tokens': chunk.get('output_tokens', 0),
}
Path('graphify-out/.graphify_extract.json').write_text(json.dumps(extract, indent=2))
print(f'Merged: {len(merged_nodes)} nodes, {len(merged_edges)} edges')

# 2. Build graph + cluster + analyze
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html

G = build_from_json(extract)
print(f'Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges')

communities = cluster(G)
cohesion = score_all(G, communities)
gods = god_nodes(G)
surprises = surprising_connections(G, communities)

# 3. Label communities based on node content
labels_raw = {cid: f'Community {cid}' for cid in communities}
analysis = {
    'communities': {str(k): v for k, v in communities.items()},
    'cohesion': {str(k): v for k, v in cohesion.items()},
    'gods': gods,
    'surprises': surprises,
}
Path('graphify-out/.graphify_analysis.json').write_text(json.dumps(analysis, indent=2))

# Print community members for labeling
for cid, members in communities.items():
    labels_in = [G.nodes[n].get('label', n) for n in list(members)[:6]]
    print(f'  Community {cid}: {labels_in}')
