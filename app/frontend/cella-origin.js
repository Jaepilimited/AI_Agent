/* 셀라의 기원 — 아바타를 누르면 소개와 뮤직비디오가 열린다.
 *
 * ⛔ `cella-pet.js` 를 고치지 않는다. 그 파일은 다른 세션이 작업 중이고,
 *    같은 작업트리를 쓰는 이 저장소에서 남의 진행 중 파일을 건드리면
 *    배포에 그 중간 상태가 함께 실린다 (2026-09-03 실측 사고).
 *    여기서는 이미 만들어진 마크업에 **얹기만** 한다.
 * ⚠️ 그래서 마크업이 바뀌면 이 기능은 **조용히 사라진다** — 그건 의도한
 *    성질이다. 부서지는 것보다 없는 편이 낫다 (부가 기능이다).
 *
 * ⛔ 말풍선을 가로채지 않는다. 그 자리는 "질문 이렇게 쓰세요" 를 가르치는
 *    프롬프트 가이드다. 거기에 영상 권유를 섞으면 둘이 서로를 방해하고,
 *    자주 뜨면 가이드까지 함께 안 읽힌다 (알림에서 이미 겪은 실패다).
 *    → 상시 입구는 **아바타 클릭**이고, 말풍선 초대는 **처음 한 번뿐**이다.
 */
(function () {
  'use strict';

  var SEEN_KEY = 'skin1004-cella-origin-invited-v1';
  var VIDEO_SRC = '/static/media/cella-origin-720p.mp4';
  var POSTER_SRC = '/static/media/cella-origin-poster.jpg';

  var overlay = null;
  var video = null;
  var lastFocus = null;
  var invite = null;

  function seen() {
    try { return localStorage.getItem(SEEN_KEY) === '1'; } catch (_) { return false; }
  }
  function markSeen() {
    try { localStorage.setItem(SEEN_KEY, '1'); } catch (_) { /* 저장은 선택이다 */ }
    dismissInvite();
  }

  // ── 소개 + 영상 ───────────────────────────────────────────────────────
  function buildOverlay() {
    overlay = document.createElement('div');
    overlay.className = 'cella-origin-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '셀라 소개');

    var card = document.createElement('div');
    card.className = 'cella-origin-card';

    var head = document.createElement('div');
    head.className = 'cella-origin-head';
    var title = document.createElement('strong');
    title.textContent = '셀라의 기원';
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'cella-origin-close';
    close.setAttribute('aria-label', '닫기');
    close.innerHTML = '&times;';
    close.addEventListener('click', closeOverlay);
    head.appendChild(title);
    head.appendChild(close);

    video = document.createElement('video');
    video.className = 'cella-origin-video';
    video.src = VIDEO_SRC;
    video.poster = POSTER_SRC;
    video.controls = true;
    video.playsInline = true;
    // ⚠️ `preload="none"` — 40MB 다. 열지도 않은 사람에게 받게 하지 않는다
    video.preload = 'none';

    var note = document.createElement('p');
    note.className = 'cella-origin-note';
    note.textContent = '셀라는 Craver 구성원의 질문에 답하려고 만들어진 사내 AI 입니다.';

    card.appendChild(head);
    card.appendChild(video);
    card.appendChild(note);
    overlay.appendChild(card);

    // 바깥을 누르면 닫는다 (카드 안쪽 클릭은 통과시키지 않는다)
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeOverlay();
    });
    document.body.appendChild(overlay);
  }

  function openOverlay() {
    if (!overlay) buildOverlay();
    lastFocus = document.activeElement;
    overlay.hidden = false;
    markSeen();
    // 클릭이라는 사용자 동작 안에서 부르므로 소리 있는 재생이 허용된다.
    // ⚠️ 그래도 브라우저가 막을 수 있다 — 막히면 controls 로 직접 누르면 된다
    var started = video.play();
    if (started && typeof started.catch === 'function') {
      started.catch(function () { /* 사용자가 재생 버튼을 누르면 된다 */ });
    }
    overlay.querySelector('.cella-origin-close').focus();
  }

  function closeOverlay() {
    if (!overlay || overlay.hidden) return;
    overlay.hidden = true;
    // ⛔ 닫을 때 반드시 멈춘다 — 안 그러면 화면은 사라졌는데 **소리가 계속 난다**
    try { video.pause(); } catch (_) { /* ignore */ }
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // ── 아바타를 누를 수 있게 만든다 ──────────────────────────────────────
  function attachAvatar(avatar) {
    if (avatar.querySelector('.cella-origin-hotspot')) return;
    // ⚠️ `.cella-static-avatar` 는 `aria-hidden="true"` 다 — 그것을 그대로
    //    누르게 하면 키보드·스크린리더에서 닿지 않는다. 진짜 버튼을 얹는다
    var hot = document.createElement('button');
    hot.type = 'button';
    hot.className = 'cella-origin-hotspot';
    hot.setAttribute('aria-label', '셀라 소개 보기');
    hot.title = '셀라 소개';
    hot.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      openOverlay();
    });
    avatar.appendChild(hot);
  }

  // ── 처음 한 번의 초대 ─────────────────────────────────────────────────
  function dismissInvite() {
    if (invite && invite.parentNode) invite.parentNode.removeChild(invite);
    invite = null;
  }

  function showInvite(layer) {
    if (invite || seen()) return;
    // ⛔ 첫 화면에서만 띄운다. 대화 중에 끼어들면 그건 방해다
    var welcome = document.getElementById('chat-welcome');
    if (!welcome || welcome.style.display === 'none' || !welcome.offsetParent) return;

    invite = document.createElement('div');
    invite.className = 'cella-origin-invite';
    var text = document.createElement('span');
    text.textContent = '나 어떻게 태어났는지 볼래?';
    var open = document.createElement('button');
    open.type = 'button';
    open.className = 'cella-origin-invite-open';
    open.textContent = '보기';
    open.addEventListener('click', openOverlay);
    var no = document.createElement('button');
    no.type = 'button';
    no.className = 'cella-origin-invite-close';
    no.setAttribute('aria-label', '나중에');
    no.innerHTML = '&times;';
    // ⛔ 닫아도 본 것으로 친다 — 거절한 사람에게 또 물으면 그게 소음이다
    no.addEventListener('click', markSeen);
    invite.appendChild(text);
    invite.appendChild(open);
    invite.appendChild(no);
    // ⛔ 레이어가 아니라 `.cella-guide` 에 붙인다 — 레이어에 붙이면 화면
    //    좌상단에 떨어진다. 그리고 말풍선 카드(238px)는 아바타 **위**에
    //    있으므로, 초대는 아바타 **왼쪽** 빈자리에 세워 겹치지 않게 한다
    (layer.querySelector('.cella-guide') || layer).appendChild(invite);
  }

  // ── cella-pet.js 가 마크업을 만들 때까지 기다린다 ─────────────────────
  function tryAttach() {
    var layer = document.getElementById('cella-pet-layer');
    if (!layer) return false;
    var avatar = layer.querySelector('.cella-static-avatar');
    if (!avatar) return false;
    attachAvatar(avatar);
    showInvite(layer);
    return true;
  }

  function start() {
    if (tryAttach()) return;
    // ⚠️ 순서를 가정하지 않는다. 몇 초 안에 안 생기면 조용히 포기한다 —
    //    관찰자를 영원히 켜 두면 그것대로 새는 자원이다
    var observer = new MutationObserver(function () {
      if (tryAttach()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(function () { observer.disconnect(); }, 10000);
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeOverlay();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
