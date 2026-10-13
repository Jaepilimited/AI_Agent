from pathlib import Path

p = Path('app/frontend/chat.js')
s = p.read_text(encoding='utf-8')
start = s.index('  function renderFlowCanvas() {')
end = s.index('  function hideFlowDetail() {', start)
assert 3500 < end-start < 4500, end-start
s = s[:start] + '''  function renderFlowCanvas() {
    if (!_flowGraph) return;
    var container = document.getElementById("flow-canvas");
    if (!container) return;
    ["btn-flow-fit", "btn-flow-zoom-in", "btn-flow-zoom-out"].forEach(function(id) { document.getElementById(id).hidden = _flowCollapsed; });
    document.getElementById("flow-raw-legend").hidden = true;
    document.getElementById("btn-flow-core").setAttribute("aria-pressed", String(_flowCollapsed));
    document.getElementById("btn-flow-collapse").setAttribute("aria-pressed", String(!_flowCollapsed));
    document.getElementById("flow-search").closest("label").hidden = _flowCollapsed;
    container.classList.add("flow-summary-canvas");
    if (_flowCollapsed) renderFlowMap(container);
    else renderFlowAll(container);
    renderFlowSources(container);
  }

''' + s[end:]
start = s.index('  function _flowNodeColor(group) {')
end = s.index('  // Presentation groups retain', start)
assert 1200 < end-start < 1500, end-start
s = s[:start] + s[end:]
s = s.replace('  var _FLOW_HIDDEN_GROUPS = { sub: 1, branch: 1 };\n', '')
s = s.replace('    if (_flowNetwork) { _flowNetwork.destroy(); _flowNetwork = null; }', '    _flowAllLayout = null;')
s = s.replace('    if (!_flowNetwork) return;\n    _flowNetwork.moveTo({ scale: _flowNetwork.getScale() * factor, animation: { duration: 150 } });', '    setFlowAllZoom(_flowZoomLevel * factor);')
s = s.replace('hideFlowDetail(); if (_flowNetwork) _flowNetwork.unselectAll();', 'hideFlowDetail(); highlightFlowAll(null);')
s = s.replace('      if (_flowNetwork) _flowNetwork.fit({ animation: { duration: 200 } });', '      fitFlowAll();')
s = s.replace('document.getElementById("flow-search").addEventListener("input", function() { hideFlowDetail(); renderFlowOverview(); });', 'document.getElementById("flow-search").addEventListener("input", function() { hideFlowDetail(); if (_flowView === "network" && !_flowCollapsed) searchFlowAll(); else renderFlowOverview(); });')
start = s.index('  // ⚠️ 2026-08-25: "아키텍처가 안 보인다"')
end = s.index('  var _flowZoomLevel', start)
assert 350 < end-start < 900
s = s[:start] + '  // Both connection views use source-derived DOM nodes and SVG edges.\n' + s[end:]
p.write_text(s, encoding='utf-8')
