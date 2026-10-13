import sys, json
from pathlib import Path
sys.path.insert(0, str(Path('tests/frontend').resolve()))
sys.path.insert(0, str(Path('.').resolve()))
from test_architecture_overview import browser, graph, ui
bg = browser.__wrapped__(); b = next(bg)
g = graph.__wrapped__(); ug = ui.__wrapped__(b, g); p, state = next(ug)
p.locator('#btn-flow-network').click()
p.locator('[data-flow-group="gates"]').click()
p.locator('[data-flow-member]').filter(has_text='손익 권한 거절').click()
p.keyboard.press('Escape')
print(json.dumps(p.evaluate('''() => ['#btn-flow-reset','#tab-flow','#skin-admin-drawer','.flow-main','.flow-layout','body','html'].map(s=>{const e=document.querySelector(s),r=e.getBoundingClientRect();return {s,top:r.top,bottom:r.bottom,scrollTop:e.scrollTop,height:e.clientHeight,scrollHeight:e.scrollHeight,style:getComputedStyle(e).overflowY}})'''), ensure_ascii=True))
p.screenshot(path='qa_artifacts/architecture_connections_20261008/debug-after-close.png')
try: next(ug)
except StopIteration: pass
try: next(bg)
except StopIteration: pass
