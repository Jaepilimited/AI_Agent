# -*- coding: utf-8 -*-
"""보고서 상단 공유 막대 — **응답 시점에** 붙인다.

⚠️ 저장된 HTML 에 구워 넣지 않는다. 파일은 한 번 저장되고 여러 사람이 열기 때문에,
   구워 넣으면 **보는 사람마다 달라야 할 것이 만든 사람 기준으로 굳는다** (공유받은
   사람에게 공유 버튼이 보이는 식). 그래서 `read_report` 가 매 응답마다 끼워 넣는다.

같은 이유로 이 막대는 인쇄에서 빠진다 (`@media print`). PDF 로 뽑았을 때 남의 이름과
버튼이 문서에 박히면 안 된다.
"""
from __future__ import annotations

import html

_CSS = """
<style>
#shr{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:10px;
  padding:10px 20px;background:#f7f9fc;border-bottom:1px solid #e5e7eb;
  font:14px/1.5 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;color:#16181d}
#shr .who{color:#6b7280;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
#shr button{font:inherit;padding:5px 12px;border:1px solid #d1d5db;border-radius:6px;
  background:#fff;color:#16181d;cursor:pointer}
#shr button:hover{background:#f3f4f6}
#shr button.pri{background:#1f6feb;border-color:#1f6feb;color:#fff}
#shr button.pri:hover{background:#1a5fd0}
#shrp{position:sticky;top:45px;z-index:29;display:none;padding:16px 20px 18px;
  background:#fff;border-bottom:1px solid #e5e7eb;
  font:14px/1.6 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
#shrp.on{display:block}
#shrp .in{max-width:1080px;margin:0 auto}
#shrp input{width:100%;max-width:420px;padding:8px 11px;border:1px solid #d1d5db;
  border-radius:6px;font:inherit}
#shrp ul{list-style:none;margin:10px 0 0;padding:0;max-width:560px}
#shrp li{display:flex;align-items:center;gap:10px;padding:7px 0;
  border-bottom:1px solid #f1f3f5}
#shrp li .nm{font-weight:600}
#shrp li .dp{color:#6b7280;font-size:13px;flex:1;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
#shrp .hd{color:#6b7280;font-size:13px;margin:14px 0 0}
#shrp .warn{color:#b45309;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;
  padding:8px 12px;margin:12px 0 0;font-size:13px;max-width:560px}
#shrp .msg{color:#6b7280;font-size:13px;margin:8px 0 0}
@media print{#shr,#shrp{display:none!important}}
</style>
"""

_OWNER_JS = """
<script>
(function(){
  var RID=%(rid)d, panel=document.getElementById('shrp'),
      q=document.getElementById('shrq'), found=document.getElementById('shrf'),
      cur=document.getElementById('shrc'),
      msg=document.getElementById('shrm'), t=null;
  function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;
    return d.innerHTML}
  function row(u, act, lbl){
    return '<li><span class="nm">'+esc(u.name)+'</span>'+
      '<span class="dp">'+esc(u.department||u.email||'')+'</span>'+
      '<button data-act="'+act+'" data-id="'+u.user_id_or_id+'">'+lbl+'</button></li>'
  }
  function paint(list){
    document.getElementById('shrlbl').textContent =
      list.length ? ('공유 '+list.length+'명') : '공유';
    cur.innerHTML = list.length
      ? list.map(function(u){u.user_id_or_id=u.user_id; return row(u,'del','해제')}).join('')
      : '<li class="dp">아직 아무에게도 공유하지 않았습니다.</li>';
  }
  function api(url, opt){
    return fetch(url, Object.assign({credentials:'same-origin'}, opt||{}))
      .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json() })
  }
  document.getElementById('shrb').onclick=function(){
    panel.classList.toggle('on');
    if(panel.classList.contains('on')){
      api('/api/reports/'+RID+'/shares').then(function(d){paint(d.shares||[])})
        .catch(function(){msg.textContent='공유 목록을 불러오지 못했습니다.'});
      q.focus();
    }
  };
  q.oninput=function(){
    clearTimeout(t); var v=q.value.trim();
    if(v.length<2){found.innerHTML='';return}
    t=setTimeout(function(){
      api('/api/reports/share-targets?q='+encodeURIComponent(v)).then(function(d){
        var us=d.users||[];
        found.innerHTML = us.length
          ? us.map(function(u){u.user_id_or_id=u.id; return row(u,'add','공유')}).join('')
          : '<li class="dp">일치하는 가입 사용자가 없습니다. 아직 로그인한 적 없는 '+
            '사람에게는 공유할 수 없습니다.</li>';
      }).catch(function(){})
    }, 220);
  };
  document.addEventListener('click', function(e){
    var b=e.target.closest('#shrp button[data-act]'); if(!b) return;
    var id=b.dataset.id, act=b.dataset.act; b.disabled=true;
    var p = act==='add'
      ? api('/api/reports/'+RID+'/shares', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({user_ids:[parseInt(id,10)]})})
      : api('/api/reports/'+RID+'/shares/'+id, {method:'DELETE'});
    p.then(function(d){ paint(d.shares||[]); msg.textContent='';
        if(act==='add'){ q.value=''; found.innerHTML='' } })
     .catch(function(){ b.disabled=false; msg.textContent='처리하지 못했습니다.' });
  });
  document.getElementById('shrl').onclick=function(){
    var u=location.href, done=function(){
      var b=document.getElementById('shrl'), o=b.textContent;
      b.textContent='복사됨'; setTimeout(function(){b.textContent=o},1500)};
    if(navigator.clipboard) navigator.clipboard.writeText(u).then(done, function(){});
    else { var i=document.createElement('input'); i.value=u; document.body.appendChild(i);
      i.select(); document.execCommand('copy'); i.remove(); done() }
  };
})();
</script>
"""


def bar(report_id: int, is_owner: bool, owner_name: str = "") -> str:
    """보고서 위에 얹을 조각. 소유자에게만 공유 조작이 보인다."""
    if not is_owner:
        who = html.escape(owner_name or "다른 사용자")
        return _CSS + (
            '<div id="shr"><span class="who">'
            f'<b>{who}</b> 님이 공유한 보고서입니다 — 원가·마진이 들어 있으니 '
            '외부로 전달하지 마세요.</span></div>'
        )

    return _CSS + (
        '<div id="shr">'
        '<button id="shrb" class="pri"><span id="shrlbl">공유</span></button>'
        '<button id="shrl">링크 복사</button>'
        '<span class="who">지목한 사람만 열 수 있습니다. 링크만으로는 열리지 않습니다.</span>'
        '</div>'
        '<div id="shrp"><div class="in">'
        '<input id="shrq" type="text" placeholder="이름·이메일·부서로 검색 (2글자 이상)" '
        'autocomplete="off">'
        '<ul id="shrf"></ul>'
        '<p class="hd">공유 중</p><ul id="shrc"></ul>'
        '<p class="msg" id="shrm"></p>'
        '<p class="warn">이 보고서에는 원가·마진·거래처별 FOC율이 들어 있습니다. '
        '꼭 봐야 하는 사람에게만 공유하세요.</p>'
        '</div></div>'
    ) + (_OWNER_JS % {"rid": int(report_id)})


def inject(html_text: str, report_id: int, is_owner: bool, owner_name: str = "") -> str:
    """`<body>` 바로 뒤에 끼운다. 못 찾으면 원문을 그대로 둔다 (보고서가 우선이다)."""
    frag = bar(report_id, is_owner, owner_name)
    i = html_text.find("<body")
    if i == -1:
        return frag + html_text
    j = html_text.find(">", i)
    if j == -1:
        return frag + html_text
    return html_text[: j + 1] + frag + html_text[j + 1:]


# 링크 복사 안내를 채팅 답변에도 쓴다 (같은 말을 두 군데서 짓지 않기 위해)
CHAT_HINT = "본인만 열람합니다 — 보고서 위 `공유` 버튼으로 사내 구성원을 지목할 수 있습니다."

__all__ = ["bar", "inject", "CHAT_HINT"]
