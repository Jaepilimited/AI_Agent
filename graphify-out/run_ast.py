import json
from graphify.extract import collect_files, extract
from pathlib import Path

targets = [
    'app/agents/notion_agent.py',
    'app/agents/qdrant_agent.py',
    'app/mcp/notion_mcp.py',
    'scripts/notion_qdrant_pipeline.py',
    'scripts/upload_to_notion.py',
    'scripts/crawl_notion_pages.py',
    'scripts/qdrant_sync_notion.py',
    'app/api/routes.py',
    'app/main.py',
    'knowledge_map/embed_graph.py',
]
code_files = [Path(f) for f in targets if Path(f).exists()]
result = extract(code_files)
Path('graphify-out/.graphify_ast.json').write_text(json.dumps(result, indent=2))
nodes = len(result['nodes'])
edges = len(result['edges'])
print(f'AST: {nodes} nodes, {edges} edges')
