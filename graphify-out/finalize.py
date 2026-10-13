import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.cluster import score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html

extract  = json.loads(Path('graphify-out/.graphify_extract.json').read_text())
analysis = json.loads(Path('graphify-out/.graphify_analysis.json').read_text())

G = build_from_json(extract)
communities = {int(k): v for k, v in analysis['communities'].items()}
cohesion    = {int(k): v for k, v in analysis['cohesion'].items()}
gods        = analysis['gods']
surprises   = analysis['surprises']
tokens      = {'input': extract.get('input_tokens', 0), 'output': extract.get('output_tokens', 0)}

labels = {
    0:  'Notion Agent Core',
    1:  'API Routes & Scheduler',
    2:  'App Startup & Lifecycle',
    3:  'Qdrant Pipeline (Crawl/Embed)',
    4:  'Qdrant Vector Store',
    5:  'Team Resources Crawl',
    6:  'Qdrant Sync (Legacy)',
    7:  'Notion Upload',
    8:  'Playwright Crawler',
    9:  'Notion Agent v6.2',
    10: 'Notion MCP (Deprecated)',
    11: 'Knowledge Map Viewer',
    12: 'Notion API Utils',
    13: 'embed_graph Script',
}

detection = {
    'total_files': 10, 'total_words': 5000, 'needs_graph': True,
    'warning': None, 'files': {'code': [], 'document': []}
}

questions = suggest_questions(G, communities, labels)
report = generate(G, communities, cohesion, labels, gods, surprises, detection, tokens,
                  '.', suggested_questions=questions)
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
Path('graphify-out/.graphify_labels.json').write_text(json.dumps({str(k): v for k, v in labels.items()}))

to_json(G, communities, 'graphify-out/graph.json')
to_html(G, communities, 'graphify-out/graph.html', community_labels=labels)

print('graph.html 생성 완료')
print(f'nodes={G.number_of_nodes()}, edges={G.number_of_edges()}, communities={len(communities)}')
print()

# God nodes
print('=== GOD NODES ===')
for g in gods[:5]:
    print(f'  {g["label"]} (degree={g["degree"]})')

print()
print('=== SURPRISING CONNECTIONS ===')
for s in surprises[:4]:
    print(f'  {s}')

print()
print('=== SUGGESTED QUESTIONS ===')
for q in questions[:4]:
    print(f'  - {q}')
