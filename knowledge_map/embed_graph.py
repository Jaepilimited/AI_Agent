import json

with open("knowledge_map/graph.json", encoding="utf-8") as f:
    data = json.load(f)

with open("knowledge_map/graph_viewer.html", encoding="utf-8") as f:
    html = f.read()

json_str = json.dumps(data, ensure_ascii=False)
old = "fetch('graph.json')\n  .then(r => r.json())\n  .then(data => init(data));"
new = "init(" + json_str + ");"
html = html.replace(old, new)

with open("knowledge_map/graph_viewer.html", "w", encoding="utf-8") as f:
    f.write(html)

print("done", len(json_str), "chars embedded")
