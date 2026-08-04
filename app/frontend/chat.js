/* Cella — chat.js
   Main chat SPA: SSE streaming, sidebar (date-grouped, search, collapse),
   follow-up suggestions, markdown, charts, theme
*/

(function () {
  "use strict";

  // ===== Markdown links (출처/소스 링크 포함) — 항상 새 창으로 =====
  if (window.marked && typeof marked.use === "function") {
    marked.use({
      renderer: {
        link: function (linkToken) {
          var href = linkToken.href, title = linkToken.title, tokens = linkToken.tokens;
          var text = this.parser.parseInline(tokens);
          if (!href) return text;
          var html = '<a href="' + href + '" target="_blank" rel="noopener noreferrer"';
          if (title) html += ' title="' + title + '"';
          html += ">" + text + "</a>";
          return html;
        },
      },
    });
  }

  // ===== Wave 3: Toast notification system =====
  var _toastContainer = null;
  function showToast(message, type) {
    type = type || "info";
    if (!_toastContainer) {
      _toastContainer = document.createElement("div");
      _toastContainer.className = "toast-container";
      document.body.appendChild(_toastContainer);
    }
    var t = document.createElement("div");
    t.className = "toast toast-" + type;
    var icon = type === "error" ? "⚠️" : type === "success" ? "✓" : "ℹ";
    t.innerHTML = '<span>' + icon + '</span><span>' + message + '</span>';
    _toastContainer.appendChild(t);
    setTimeout(function() { t.remove(); }, 4000);
  }

  // ===== Clipboard helper (works on HTTP too) =====
  function _copyText(text, btn) {
    function _done() {
      if (btn) {
        var orig = btn.innerHTML;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--success)"><polyline points="20 6 9 17 4 12"/></svg>';
        setTimeout(function() { btn.innerHTML = orig; }, 1500);
      }
      showToast("복사되었습니다", "success");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(_done).catch(function() {
        _fallbackCopy(text);
        _done();
      });
    } else {
      _fallbackCopy(text);
      _done();
    }
  }
  function _fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  // ── Feedback buttons (thumbs up/down) ──
  var _feedbackCache = {};  // {messageId: 1|-1}

  // Thumbs-down detail modal (created once, reused)
  var _fbModal = null;
  function _getFbModal() {
    if (_fbModal) return _fbModal;
    var overlay = document.createElement("div");
    overlay.id = "feedback-modal-overlay";
    overlay.style.cssText = [
      "display:none;position:fixed;inset:0;z-index:9999",
      "background:rgba(0,0,0,.55);backdrop-filter:blur(4px)",
      "align-items:center;justify-content:center"
    ].join(";");
    overlay.innerHTML = [
      '<div id="feedback-modal-box" style="',
        'background:var(--panel,#1e1e1e);border:1px solid var(--border,#333);',
        'border-radius:16px;padding:24px 28px;width:420px;max-width:90vw;',
        'box-shadow:0 8px 32px rgba(0,0,0,.5)">',
      '<div style="font-size:15px;font-weight:600;margin-bottom:6px">어떤 점이 부족했나요?</div>',
      '<div style="font-size:12px;color:var(--text-muted,#888);margin-bottom:14px">',
        '상세 내용을 남겨주시면 서비스 개선에 반영합니다. (선택)</div>',
      '<textarea id="feedback-modal-text" rows="4" placeholder="예: 매출 수치가 다른 것 같아요 / 답변이 너무 짧아요 ..." ',
        'style="width:100%;box-sizing:border-box;resize:vertical;',
        'background:var(--input-bg,#111);border:1px solid var(--border,#333);',
        'border-radius:8px;color:var(--text,#e8e8e8);padding:10px 12px;',
        'font-size:13px;font-family:inherit;outline:none"></textarea>',
      '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">',
        '<button id="feedback-modal-cancel" style="',
          'padding:7px 16px;border:1px solid var(--border,#333);border-radius:8px;',
          'background:transparent;color:var(--text,#e8e8e8);cursor:pointer;font-size:13px">',
          '취소</button>',
        '<button id="feedback-modal-submit" style="',
          'padding:7px 18px;border:none;border-radius:8px;',
          'background:#e89200;color:#fff;cursor:pointer;font-size:13px;font-weight:600">',
          '보내기</button>',
      '</div>',
      '</div>'
    ].join("");
    document.body.appendChild(overlay);
    // Close on overlay click
    overlay.addEventListener("click", function(e) {
      if (e.target === overlay) overlay.style.display = "none";
    });
    _fbModal = overlay;
    return overlay;
  }

  function _showFeedbackModal(messageId, thumbDown, onSubmit) {
    var modal = _getFbModal();
    var textarea = modal.querySelector("#feedback-modal-text");
    var btnSubmit = modal.querySelector("#feedback-modal-submit");
    var btnCancel = modal.querySelector("#feedback-modal-cancel");
    textarea.value = "";
    modal.style.display = "flex";
    textarea.focus();

    var submitted = false;
    function doSubmit() {
      if (submitted) return;
      submitted = true;
      modal.style.display = "none";
      onSubmit(textarea.value.trim());
    }
    function doCancel() {
      modal.style.display = "none";
      // Deactivate thumbDown visual if user cancels
      thumbDown.classList.remove("feedback-active");
      delete _feedbackCache[messageId];
    }

    // Replace listeners each time
    var newSubmit = btnSubmit.cloneNode(true);
    var newCancel = btnCancel.cloneNode(true);
    btnSubmit.parentNode.replaceChild(newSubmit, btnSubmit);
    btnCancel.parentNode.replaceChild(newCancel, btnCancel);
    newSubmit.addEventListener("click", doSubmit);
    newCancel.addEventListener("click", doCancel);
    // Enter = submit, Escape = cancel
    textarea.onkeydown = function(e) {
      if (e.key === "Escape") doCancel();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) doSubmit();
    };
  }

  function _addFeedbackButtons(actionsDiv, messageId) {
    var thumbUp = document.createElement("button");
    thumbUp.className = "msg-action-btn feedback-btn";
    thumbUp.title = "좋아요";
    thumbUp.dataset.msgId = messageId;
    thumbUp.dataset.rating = "1";
    thumbUp.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/><path d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg>';

    var thumbDown = document.createElement("button");
    thumbDown.className = "msg-action-btn feedback-btn";
    thumbDown.title = "별로예요";
    thumbDown.dataset.msgId = messageId;
    thumbDown.dataset.rating = "-1";
    thumbDown.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/><path d="M17 2h3a2 2 0 012 2v7a2 2 0 01-2 2h-3"/></svg>';

    // Restore cached state
    var cached = _feedbackCache[messageId];
    if (cached === 1) thumbUp.classList.add("feedback-active");
    if (cached === -1) thumbDown.classList.add("feedback-active");

    function _sendFeedback(rating, comment) {
      if (!currentConvoId) return;
      fetch("/api/conversations/" + currentConvoId + "/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({message_id: messageId, rating: rating, comment: comment || ""}),
      }).catch(function(e) { console.error("Feedback failed:", e); });
    }

    thumbUp.addEventListener("click", function() {
      var isActive = thumbUp.classList.contains("feedback-active");
      var newRating = isActive ? 0 : 1;
      thumbUp.classList.remove("feedback-active");
      thumbDown.classList.remove("feedback-active");
      if (!isActive) thumbUp.classList.add("feedback-active");
      if (newRating === 0) { delete _feedbackCache[messageId]; }
      else { _feedbackCache[messageId] = newRating; _sendFeedback(1, ""); }
    });

    thumbDown.addEventListener("click", function() {
      var isActive = thumbDown.classList.contains("feedback-active");
      if (isActive) {
        // Toggle off
        thumbDown.classList.remove("feedback-active");
        delete _feedbackCache[messageId];
        return;
      }
      // Activate immediately visually, then open modal for details
      thumbUp.classList.remove("feedback-active");
      thumbDown.classList.add("feedback-active");
      _feedbackCache[messageId] = -1;
      _showFeedbackModal(messageId, thumbDown, function(comment) {
        _sendFeedback(-1, comment);
      });
    });

    actionsDiv.appendChild(thumbUp);
    actionsDiv.appendChild(thumbDown);
  }

  async function _loadFeedbackForConversation(convoId) {
    try {
      var resp = await fetch("/api/conversations/" + convoId + "/feedback");
      if (resp.ok) {
        _feedbackCache = await resp.json();
      }
    } catch (e) {}
  }

  // Copy table as TSV (paste-able into Excel/Google Sheets)
  function _copyTable(table, btn) {
    var rows = table.querySelectorAll("tr");
    var tsv = [];
    for (var r = 0; r < rows.length; r++) {
      var cells = rows[r].querySelectorAll("th, td");
      var row = [];
      for (var c = 0; c < cells.length; c++) {
        row.push(cells[c].textContent.trim());
      }
      tsv.push(row.join("\t"));
    }
    _copyText(tsv.join("\n"), btn);
    if (btn) {
      btn.textContent = "복사됨!";
      setTimeout(function() { btn.textContent = "표 복사"; }, 1500);
    }
  }

  // Copy chart canvas as PNG image to clipboard
  function _copyChart(canvas, btn) {
    canvas.toBlob(function(blob) {
      if (!blob) { showToast("차트 복사 실패", "error"); return; }
      try {
        navigator.clipboard.write([
          new ClipboardItem({ "image/png": blob })
        ]).then(function() {
          showToast("차트가 복사되었습니다 (이미지)", "success");
          if (btn) {
            btn.textContent = "복사됨!";
            setTimeout(function() { btn.textContent = "차트 복사"; }, 1500);
          }
        }).catch(function() {
          // Fallback: download as file
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "chart.png";
          a.click();
          showToast("차트가 다운로드되었습니다", "info");
        });
      } catch (e) {
        // ClipboardItem not supported — download
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "chart.png";
        a.click();
        showToast("차트가 다운로드되었습니다", "info");
      }
    }, "image/png");
  }

  // ===== Keyboard Shortcuts Help =====
  function _showShortcuts() {
    if (document.getElementById("shortcuts-overlay")) return;
    var ov = document.createElement("div");
    ov.id = "shortcuts-overlay";
    ov.className = "confirm-overlay";
    ov.innerHTML =
      '<div class="confirm-dialog" style="min-width:340px;text-align:left;">' +
      '<p style="font-weight:700;font-size:16px;margin-bottom:16px;">키보드 단축키</p>' +
      '<table class="shortcuts-table">' +
      '<tr><td><kbd>Enter</kbd></td><td>메시지 전송</td></tr>' +
      '<tr><td><kbd>Shift</kbd>+<kbd>Enter</kbd></td><td>줄바꿈</td></tr>' +
      '<tr><td><kbd>Ctrl</kbd>+<kbd>Enter</kbd></td><td>메시지 전송</td></tr>' +
      '<tr><td><kbd>Esc</kbd></td><td>패널 닫기 / 생성 중지</td></tr>' +
      '<tr><td><kbd>?</kbd></td><td>단축키 도움말 (이 화면)</td></tr>' +
      '</table>' +
      '<div style="margin-top:16px;text-align:center;">' +
      '<button class="confirm-cancel" onclick="this.closest(\'.confirm-overlay\').remove()">닫기</button>' +
      '</div></div>';
    document.body.appendChild(ov);
    ov.addEventListener("click", function(e) { if (e.target === ov) ov.remove(); });
  }

  // ===== Helpers =====
  function _escHtml(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  // ===== Confirm Delete Dialog =====
  function _confirmDelete(id, title) {
    var overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.innerHTML =
      '<div class="confirm-dialog">' +
      '<p>"' + (title.length > 30 ? title.slice(0, 30) + "..." : title) + '" 대화를 삭제하시겠습니까?</p>' +
      '<div class="confirm-actions">' +
      '<button class="confirm-cancel">취소</button>' +
      '<button class="confirm-delete">삭제</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector(".confirm-cancel").addEventListener("click", function() { overlay.remove(); });
    overlay.querySelector(".confirm-delete").addEventListener("click", function() {
      overlay.remove();
      deleteConversation(id);
    });
    overlay.addEventListener("click", function(e) { if (e.target === overlay) overlay.remove(); });
  }

  // ===== State =====
  var currentUser = null;
  var conversations = [];
  var currentConvoId = null;
  var currentMessages = [];  // In-memory message history for API calls
  var isStreaming = false;
  var lastUserQuery = "";
  var currentAbortController = null;  // AbortController for active stream
  var _autoScrollActive = true;  // Auto-scroll during streaming (user scroll-up disables)

  // ===== Wave 1: Client-side Pre-routing =====
  // Mirrors top-frequency patterns from orchestrator.py for instant skeleton UI
  function _clientPreRoute(query) {
    if (!query) return "direct";
    var q = query.toLowerCase();
    // Direct lock keywords (always direct)
    var _directLock = ["회사", "뭐하는", "소개", "누가 만들", "주인", "안녕", "하이", "hello", "hi", "부동산", "주식", "투자"];
    for (var i = 0; i < _directLock.length; i++) {
      if (q.indexOf(_directLock[i]) !== -1) return "direct";
    }
    // CS keywords (product-specific)
    var _csKw = ["성분", "비건", "사용법", "사용 방법", "루틴", "스킨케어", "센텔라", "민감", "트러블", "피부", "자극", "알레르기", "세럼", "앰플", "토너", "클렌저", "선크림", "skin1004"];
    for (var i = 0; i < _csKw.length; i++) {
      if (q.indexOf(_csKw[i]) !== -1) return "cs";
    }
    // GWS keywords
    var _gwsKw = ["드라이브", "메일", "gmail", "캘린더", "일정", "내 메일", "내 드라이브"];
    for (var i = 0; i < _gwsKw.length; i++) {
      if (q.indexOf(_gwsKw[i]) !== -1) return "gws";
    }
    // Notion keywords
    var _notionKw = ["노션", "notion", "정책", "매뉴얼", "프로세스", "가이드"];
    for (var i = 0; i < _notionKw.length; i++) {
      if (q.indexOf(_notionKw[i]) !== -1) return "notion";
    }
    // Data keywords (BigQuery)
    var _dataKw = ["매출", "수량", "주문", "sales", "revenue", "쇼피", "아마존", "틱톡", "광고", "마케팅", "ROAS", "roas", "리뷰", "인플루언서", "shopify", "재고", "판매", "실적", "순위", "데이터", "조회", "차트", "그래프"];
    for (var i = 0; i < _dataKw.length; i++) {
      if (q.indexOf(_dataKw[i]) !== -1) return "bigquery";
    }
    return "direct";
  }

  // ===== Data Source Filter (Grouped) =====
  var SOURCE_GROUPS = [
    { id: "sales", label: "매출 데이터", emoji: "\uD83D\uDCCA",
      keys: ["매출", "제품", "손익"] },
    { id: "marketing", label: "마케팅 데이터", emoji: "\uD83D\uDCC8",
      keys: ["광고", "마케팅", "Shopify", "플랫폼",
             "인플루언서", "아마존검색", "메타광고",
             "아마존 리뷰", "큐텐 리뷰", "쇼피 리뷰", "스마트스토어 리뷰"] },
    { id: "notion", label: "Notion 문서", emoji: "\uD83D\uDCD3",
      keys: ["B2B1", "B2B2", "BCM", "CS", "Craver", "DB",
             "GM EAST", "GM WEST", "JBT", "KBT", "PEOPLE", "BP"],
      _dynamic: true,
      link: "https://www.notion.so/skin1004/DB-HUB-2e12b4283b008011ae32e39bf73b7f7b" },
    { id: "system", label: "시스템", emoji: "\u2699",
      keys: ["Google Workspace"] },
  ];
  var DATA_SOURCE_KEYS = [];
  function _sourceVisibleForCurrentUser(key) {
    return key !== "손익" || !currentUser || !!currentUser.can_view_fi;
  }
  function _rebuildDataSourceKeys() {
    DATA_SOURCE_KEYS = [];
    SOURCE_GROUPS.forEach(function(g) {
      g.keys.forEach(function(k) {
        if (_sourceVisibleForCurrentUser(k)) DATA_SOURCE_KEYS.push(k);
      });
    });
  }
  function _applyFiSourceVisibility() {
    if (currentUser && currentUser.can_view_fi) return;
    SOURCE_GROUPS.forEach(function(g) {
      g.keys = g.keys.filter(function(k) { return k !== "손익"; });
    });
    _rebuildDataSourceKeys();
    enabledSources = enabledSources.filter(function(k) { return k !== "손익"; });
    saveEnabledSources();
  }
  _rebuildDataSourceKeys();
  // Source key → route mapping for orchestrator
  var SOURCE_ROUTE_MAP = {
    "매출": "bigquery", "제품": "bigquery", "손익": "bigquery",
    "광고": "bigquery", "마케팅": "bigquery",
    "Shopify": "bigquery", "플랫폼": "bigquery",
    "인플루언서": "bigquery", "아마존검색": "bigquery",
    "메타광고": "bigquery",
    "아마존 리뷰": "bigquery", "큐텐 리뷰": "bigquery",
    "쇼피 리뷰": "bigquery", "스마트스토어 리뷰": "bigquery",
    "BP": "cs",
    "B2B1": "notion", "GM WEST": "notion", "CS": "notion",
    "DB": "notion", "B2B2": "notion", "PEOPLE": "notion",
    "BCM": "notion", "GM EAST": "notion", "Craver": "notion",
    "KBT": "notion", "JBT": "notion",
    "Google Workspace": "gws"
  };
  var _DB_ALIASES = {};  // @@alias → canonical key (populated by loadDbSources)
  var _sourceChipsContainer = null;
  function _ensureChipsContainer() {
    if (_sourceChipsContainer) return _sourceChipsContainer;
    _sourceChipsContainer = document.createElement("div");
    _sourceChipsContainer.id = "active-source-chips";
    _sourceChipsContainer.className = "active-source-chips";
    var inputArea = document.querySelector(".chat-input-wrapper") || (document.getElementById("chat-input") || {}).parentElement;
    if (inputArea && inputArea.parentElement) inputArea.parentElement.insertBefore(_sourceChipsContainer, inputArea);
    return _sourceChipsContainer;
  }
  function showActiveSourceChips(keys) {
    var container = _ensureChipsContainer();
    if (!container) return;
    container.innerHTML = "";
    keys = (keys || []).filter(_sourceVisibleForCurrentUser);
    if (!keys || keys.length === 0) { container.style.display = "none"; return; }
    var colorMap = { bigquery: "#4285f4", notion: "#9b59b6", cs: "#27ae60", gws: "#e89200", team: "#9b59b6" };
    keys.forEach(function(k) {
      var route = SOURCE_ROUTE_MAP[k] || "bigquery";
      var color = colorMap[route] || "#666";
      var chip = document.createElement("span");
      chip.className = "source-chip";
      chip.style.cssText = "background:" + color + "22;color:" + color + ";border:1px solid " + color + "44;";
      chip.innerHTML = "@@" + k + ' <span class="chip-x">&times;</span>';
      chip.querySelector(".chip-x").addEventListener("click", function() {
        chip.remove();
        if (container.children.length === 0) container.style.display = "none";
      });
      container.appendChild(chip);
    });
    container.style.display = "flex";
  }
  function clearActiveSourceChips() {
    if (_sourceChipsContainer) { _sourceChipsContainer.innerHTML = ""; _sourceChipsContainer.style.display = "none"; }
  }
  var enabledSources = loadEnabledSources();

  function loadEnabledSources() {
    try {
      var saved = localStorage.getItem("skin1004_enabled_sources");
      if (saved) {
        var parsed = JSON.parse(saved);
        // Migrate: if any saved key not in current set, reset to all
        var hasOld = parsed.some(function(k) { return DATA_SOURCE_KEYS.indexOf(k) < 0; });
        if (!hasOld && parsed.length > 0) return parsed;
        localStorage.removeItem("skin1004_enabled_sources");
      }
    } catch (e) {}
    // Default: all enabled
    return DATA_SOURCE_KEYS.slice();
  }
  function saveEnabledSources() {
    localStorage.setItem("skin1004_enabled_sources", JSON.stringify(enabledSources));
  }
  function toggleSource(key) {
    var idx = enabledSources.indexOf(key);
    if (idx >= 0) enabledSources.splice(idx, 1);
    else enabledSources.push(key);
    saveEnabledSources();
  }
  function getEnabledRoutes() {
    var routes = {};
    enabledSources.forEach(function(k) {
      var r = SOURCE_ROUTE_MAP[k];
      if (r) routes[r] = true;
    });
    return Object.keys(routes);
  }
  function getEnabledTableKeys() {
    return enabledSources.filter(function(k) { return SOURCE_ROUTE_MAP[k] === "bigquery"; });
  }

  // ===== Team Resource Filter (per-resource checkboxes) =====
  var enabledTeamRes = loadTeamRes();  // { "JBT": ["name1",...], "BCM": [...] } or null=all
  function loadTeamRes() {
    try {
      var s = localStorage.getItem("skin1004_team_resources");
      if (s) return JSON.parse(s);
    } catch(e) {}
    return null;  // null = all enabled (default)
  }
  function saveTeamRes() {
    if (enabledTeamRes === null) localStorage.removeItem("skin1004_team_resources");
    else localStorage.setItem("skin1004_team_resources", JSON.stringify(enabledTeamRes));
  }
  function isTeamResEnabled(team, nodeId) {
    if (!enabledTeamRes) return true;  // null = all
    var list = enabledTeamRes[team];
    if (!list) return true;  // team not filtered
    return list.indexOf(nodeId) >= 0;
  }
  function getEnabledTeamResPayload() {
    if (!enabledTeamRes) return null;
    var result = {};
    Object.keys(enabledTeamRes).forEach(function(team) {
      var ids = (enabledTeamRes[team] || []).filter(function(id) { return Number.isInteger(id); });
      if (ids.length > 0) result[team] = ids;
    });
    return Object.keys(result).length > 0 ? result : null;
  }
  var _allTeamResNames = {};  // Populated from safety/status response

  // Rebuild enabledTeamRes from DOM checkbox states
  function _rebuildTeamRes(team, container) {
    if (!enabledTeamRes) enabledTeamRes = {};
    var item = container.querySelector('[data-team-key="' + team + '"]');
    if (!item) return;
    var checkedIds = [];
    item.querySelectorAll('.tree-cb:checked').forEach(function(cb) {
      var id = parseInt(cb.getAttribute("data-id"));
      if (!isNaN(id)) checkedIds.push(id);
    });
    enabledTeamRes[team] = checkedIds;
    saveTeamRes();
  }

  // ===== Image Upload State =====
  var pendingImages = [];  // Array of { file: File, dataUrl: string }
  var MAX_IMAGE_SIZE = 10 * 1024 * 1024;  // 10MB
  var ALLOWED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

  // ===== Follow-up suggestion pools (based on actual BigQuery data) =====
  var FOLLOWUP_POOLS = {
    sales: [
      "이번 달 국가별 매출 비교해줘",
      "전월 대비 매출 증감율 보여줘",
      "올해 월별 매출 추이 차트로 보여줘",
      "Top 10 제품 매출 순위 알려줘",
      "플랫폼별 매출 비중 비교해줘",
      "B2B vs B2C 매출 비교해줘",
    ],
    shopee: [
      "쇼피 인도네시아 이번 달 매출 알려줘",
      "쇼피 필리핀 Top 5 제품은?",
      "쇼피 전체 국가별 매출 비교해줘",
      "쇼피 인도네시아 최근 3개월 추이",
      "쇼피 말레이시아 매출 현황 알려줘",
    ],
    amazon: [
      "아마존 미국 이번 달 매출 요약해줘",
      "아마존 일본 Top 10 제품 알려줘",
      "아마존 전체 국가 매출 비교",
      "아마존 미국 전월 대비 증감율",
      "아마존 캐나다 매출 현황 알려줘",
    ],
    tiktok: [
      "틱톡샵 인도네시아 매출 현황 알려줘",
      "틱톡샵 미국 이번 달 매출은?",
      "틱톡샵 필리핀 매출 비교해줘",
      "틱톡샵 국가별 매출 순위 알려줘",
    ],
    cs: [
      "센텔라 토너 사용법 알려줘",
      "Craver 반품 절차 알려줘",
      "마다가스카르 센텔라 앰플 주요 성분 알려줘",
      "지성 피부에 맞는 Craver 루틴 추천해줘",
      "교환/환불 정책 안내해줘",
      "민감성 피부용 크림 추천해줘",
    ],
    general: [
      "이번 달 전체 매출 요약해줘",
      "가장 많이 팔린 제품은?",
      "국가별 매출 순위 Top 10 알려줘",
      "일본 Q10 매출 현황 알려줘",
      "필리핀 전체 플랫폼 매출 비교해줘",
      "인도네시아 쇼피 vs 틱톡 매출 비교",
    ],
  };

  // ===== DOM refs =====
  var chatMessages = document.getElementById("chat-messages");
  var chatWelcome = document.getElementById("chat-welcome");
  var chatInput = document.getElementById("chat-input");
  var btnSend = document.getElementById("btn-send");
  var btnNewChat = document.getElementById("btn-new-chat");
  var convoList = document.getElementById("convo-list");
  var modelSelect = document.getElementById("model-select");
  var userName = document.getElementById("user-name");
  var userAvatar = document.getElementById("user-avatar");
  var btnLogout = document.getElementById("btn-logout");
  var btnMenu = document.getElementById("btn-menu");
  var sidebar = document.getElementById("sidebar");
  var mobileOverlay = document.getElementById("mobile-overlay");
  var convoSearch = document.getElementById("convo-search");
  var followupContainer = document.getElementById("followup-suggestions");
  var imagePreviewStrip = document.getElementById("image-preview-strip");
  var btnAttach = document.getElementById("btn-attach");
  var fileInput = document.getElementById("file-input");
  var chatInputArea = document.getElementById("chat-input-area");

  // ===== Init =====
  init();

  async function init() {
    try {
      var resp = await fetch("/api/auth/me");
      if (!resp.ok) { window.location.href = "/login"; return; }
      currentUser = await resp.json();
      _applyFiSourceVisibility();
      userName.textContent = currentUser.name;
      userAvatar.textContent = (currentUser.name || "U").charAt(0).toUpperCase();
      var welcomeName = document.getElementById("welcome-user-name");
      if (welcomeName) welcomeName.textContent = currentUser.name;
    } catch (e) {
      window.location.href = "/login";
      return;
    }

    setupEventListeners();
    showAdminButton();
    await loadConversations();
    updateTheme();
    pollSystemStatus();
    setInterval(pollSystemStatus, 30000);
    pollAnnouncement();
    setInterval(pollAnnouncement, 60000);
    updateSourceFilterBadge();
    checkGwsStatus();
  }

  // ===== Event Listeners =====
  function setupEventListeners() {
    btnSend.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", function (e) {
      // Enter (no shift) OR Ctrl/Cmd+Enter → send
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        sendMessage();
      }
      if (e.key === "Escape") {
        var dd = document.getElementById("slash-source-dropdown");
        if (dd && dd.style.display !== "none") {
          dd.style.display = "none";
          _slashTempSelection = [];
        }
      }
    });

    chatInput.addEventListener("input", function () {
      var self = this;
      requestAnimationFrame(function() {
        self.style.height = "auto";
        self.style.height = Math.min(self.scrollHeight, 150) + "px";
      });
      updateSendButton();
    });

    // Source select button → toggle dropdown
    var btnSourceSelect = document.getElementById("btn-source-select");
    if (btnSourceSelect) {
      btnSourceSelect.addEventListener("click", function () {
        toggleSourceDropdown();
      });
    }

    // Image attach button → trigger file input
    btnAttach.addEventListener("click", function () {
      fileInput.click();
    });

    // File input change → process selected files
    fileInput.addEventListener("change", function () {
      if (this.files) addImageFiles(this.files);
      this.value = "";  // Reset so same file can be re-selected
    });

    // ═══ @@ 데이터소스 자동완성 (그룹화 + SVG 아이콘 + 컴팩트) ═══
    var _dbSources = [];
    var _dbDropdown = null;

    var _DB_ICONS = {
      chart:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>',
      box:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>',
      megaphone:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l18-5v12L3 13v-2z"/><path d="M11.6 16.8a3 3 0 11-5.8-1.6"/></svg>',
      dollar:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>',
      users:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
      cart:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 002 1.61h9.72a2 2 0 002-1.61L23 6H6"/></svg>',
      store:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>',
      search:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
      phone:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>',
      star:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
      people:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>',
      doc:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
      flask:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3h6M10 3v7.4a2 2 0 01-.5 1.3L4 19a2 2 0 001.5 3h13a2 2 0 001.5-3l-5.5-7.3a2 2 0 01-.5-1.3V3"/></svg>',
      headset:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 18v-6a9 9 0 0118 0v6"/><path d="M21 19a2 2 0 01-2 2h-1a2 2 0 01-2-2v-3a2 2 0 012-2h3zM3 19a2 2 0 002 2h1a2 2 0 002-2v-3a2 2 0 00-2-2H3z"/></svg>',
      link:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>',
      all:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>',
    };

    // @@ alias → canonical key mapping (populated into module-level _DB_ALIASES)
    (function loadDbSources() {
      fetch("/api/datasources").then(function(r) { return r.json(); }).then(function(data) {
        _dbSources = data;
        // Build alias map: key + aliases → canonical key
        data.forEach(function(d) {
          _DB_ALIASES[d.key.toLowerCase()] = d.key;
          (d.aliases || []).forEach(function(a) { _DB_ALIASES[a.toLowerCase()] = d.key; });
        });
      }).catch(function() {});
    })();

    // ═══ Active Source Chips — uses module-level functions (see IIFE top) ═══

    function _createDbDropdown() {
      if (_dbDropdown) return;
      _dbDropdown = document.createElement("div");
      _dbDropdown.className = "db-autocomplete-dropdown";
      _dbDropdown.style.display = "none";
      var inputWrapper = chatInputArea.querySelector(".chat-input-wrapper");
      inputWrapper.style.position = "relative";
      inputWrapper.appendChild(_dbDropdown);
    }
    _createDbDropdown();

    // Grid column count for left/right navigation
    var _dbGridCols = 3;

    function _showDbDropdown(filter) {
      if (!_dbDropdown || !_dbSources.length) return;
      var f = (filter || "").toLowerCase();
      var matches = _dbSources.filter(function(s) {
        return !f || s.key.toLowerCase().indexOf(f) === 0
            || s.aliases.some(function(a) { return a.toLowerCase().indexOf(f) === 0; })
            || s.label.toLowerCase().indexOf(f) >= 0;
      });

      // Already selected keys (multi-select)
      var val = chatInput.value;
      var selectedKeys = (val.match(/@@(\S+)/g) || []).map(function(m) { return m.substring(2); });

      // Build grouped HTML
      var html = '<div class="db-ac-special">';
      html += '<div class="db-ac-chip" data-key="전체">' + _DB_ICONS.all + ' 전체</div>';
      html += '<div class="db-ac-chip" data-key="전체해제">' + _DB_ICONS.all + ' 해제</div>';
      html += '</div>';

      var groups = {};
      matches.forEach(function(s) {
        var g = s.group || "기타";
        if (!groups[g]) groups[g] = [];
        groups[g].push(s);
      });

      Object.keys(groups).forEach(function(gName) {
        html += '<div class="db-ac-group-label">' + gName + '</div>';
        html += '<div class="db-ac-grid">';
        groups[gName].forEach(function(s) {
          var icon = _DB_ICONS[s.icon] || _DB_ICONS.doc;
          var sel = selectedKeys.indexOf(s.key) >= 0 ? " selected" : "";
          html += '<div class="db-ac-item' + sel + '" data-key="' + s.key + '" title="' + s.desc + '">'
               + '<span class="db-ac-icon">' + icon + '</span>'
               + '<span class="db-ac-name">' + s.label + '</span></div>';
        });
        html += '</div>';
      });

      if (selectedKeys.length > 0) {
        html += '<div class="db-ac-hint">Tab: 추가 선택 · Enter: 확정</div>';
      } else {
        html += '<div class="db-ac-hint">Tab: 선택 · ↑↓←→: 이동</div>';
      }

      _dbDropdown.innerHTML = html;
      _dbDropdown.style.display = "block";

      _dbDropdown.querySelectorAll(".db-ac-item, .db-ac-chip").forEach(function(el) {
        el.addEventListener("mousedown", function(e) {
          e.preventDefault();
          _tabSelectDbItem(el.dataset.key);
        });
      });
      _dbActiveIdx = -1;
    }

    // Tab select: append @@key and keep popup open for more
    function _tabSelectDbItem(key) {
      var val = chatInput.value;
      // Remove the current incomplete @@ token being typed
      var lastAt = val.lastIndexOf("@@");
      var base = lastAt >= 0 ? val.substring(0, lastAt) : val;
      chatInput.value = base + "@@" + key + " ";
      chatInput.focus();
      _dbActiveIdx = -1;
      // Re-show dropdown for next selection
      setTimeout(function() { _showDbDropdown(""); }, 50);
    }

    // Enter: close popup, keep all selections
    function _confirmDbSelection() {
      _dbDropdown.style.display = "none";
      _dbActiveIdx = -1;
      chatInput.focus();
    }

    var _dbActiveIdx = -1;

    function _getDbItems() {
      if (!_dbDropdown) return [];
      return Array.from(_dbDropdown.querySelectorAll(".db-ac-item, .db-ac-chip"));
    }

    function _highlightDbItem(idx) {
      var items = _getDbItems();
      items.forEach(function(el) { el.classList.remove("active"); });
      if (idx >= 0 && idx < items.length) {
        items[idx].classList.add("active");
        items[idx].scrollIntoView({ block: "nearest" });
      }
    }

    chatInput.addEventListener("input", function() {
      var val = this.value;
      // Check if there's an incomplete @@ token at the end
      var lastAt = val.lastIndexOf("@@");
      if (lastAt >= 0) {
        var after = val.substring(lastAt + 2);
        // If no space after last @@, show dropdown with filter
        if (after.indexOf(" ") < 0) {
          _showDbDropdown(after);
        } else {
          if (_dbDropdown) _dbDropdown.style.display = "none";
        }
      } else {
        if (_dbDropdown) _dbDropdown.style.display = "none";
      }
    });

    chatInput.addEventListener("keydown", function(e) {
      if (!_dbDropdown || _dbDropdown.style.display === "none") return;
      var items = _getDbItems();
      if (!items.length) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        _dbActiveIdx = Math.min(_dbActiveIdx + _dbGridCols, items.length - 1);
        _highlightDbItem(_dbActiveIdx);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        _dbActiveIdx = Math.max(_dbActiveIdx - _dbGridCols, 0);
        _highlightDbItem(_dbActiveIdx);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        _dbActiveIdx = Math.min(_dbActiveIdx + 1, items.length - 1);
        _highlightDbItem(_dbActiveIdx);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        _dbActiveIdx = Math.max(_dbActiveIdx - 1, 0);
        _highlightDbItem(_dbActiveIdx);
      } else if (e.key === "Tab" && _dbActiveIdx >= 0) {
        e.preventDefault();
        _tabSelectDbItem(items[_dbActiveIdx].dataset.key);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (_dbActiveIdx >= 0) {
          _tabSelectDbItem(items[_dbActiveIdx].dataset.key);
        }
        _confirmDbSelection();
      } else if (e.key === "Escape") {
        _dbDropdown.style.display = "none";
        _dbActiveIdx = -1;
      }
    });

    chatInput.addEventListener("blur", function() {
      setTimeout(function() { if (_dbDropdown) { _dbDropdown.style.display = "none"; _dbActiveIdx = -1; } }, 200);
    });

    // Paste image from clipboard
    chatInput.addEventListener("paste", function (e) {
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        if (items[i].type.indexOf("image/") === 0) {
          e.preventDefault();
          var file = items[i].getAsFile();
          if (file) addImageFiles([file]);
          return;
        }
      }
    });

    // Drag and drop images
    chatInputArea.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      this.classList.add("drag-over");
    });
    chatInputArea.addEventListener("dragleave", function (e) {
      e.preventDefault();
      e.stopPropagation();
      this.classList.remove("drag-over");
    });
    chatInputArea.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      this.classList.remove("drag-over");
      if (e.dataTransfer && e.dataTransfer.files) {
        addImageFiles(e.dataTransfer.files);
      }
    });

    // New chat
    btnNewChat.addEventListener("click", function () {
      // Abort active stream — full cleanup
      if (isStreaming || currentAbortController) {
        if (currentAbortController) currentAbortController.abort();
        _stopTokenDrain();
        isStreaming = false;
        currentAbortController = null;
        _autoScrollActive = true;
        _resetSendBtn();
        var streamingMsg = chatMessages.querySelector(".message.streaming");
        if (streamingMsg) streamingMsg.classList.remove("streaming");
      }
      currentConvoId = null;
      currentMessages = [];
      showWelcome();
      highlightActiveConvo();
      hideFollowups();
      clearPendingImages();
      closeMobileSidebar();
    });

    // Logo click → home (welcome screen)
    document.getElementById("sidebar-home-link").addEventListener("click", function (e) {
      e.preventDefault();
      currentConvoId = null;
      currentMessages = [];
      showWelcome();
      highlightActiveConvo();
      hideFollowups();
      clearPendingImages();
      closeMobileSidebar();
    });

    // Change password
    var btnChangePw = document.getElementById("btn-change-pw");
    if (btnChangePw) {
      btnChangePw.addEventListener("click", function () {
        showChangePasswordModal();
      });
    }

    // Logout
    btnLogout.addEventListener("click", async function () {
      await fetch("/api/auth/logout", { method: "POST" });
      window.location.href = "/login";
    });

    // Mobile sidebar
    btnMenu.addEventListener("click", function () {
      sidebar.classList.add("open");
      mobileOverlay.classList.add("active");
    });
    mobileOverlay.addEventListener("click", closeMobileSidebar);

    // Suggestion chips (welcome screen)
    document.querySelectorAll(".suggestion-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        chatInput.value = this.dataset.q;
        chatInput.dispatchEvent(new Event("input"));
        sendMessage();
      });
    });

    // Sidebar collapse/expand
    document.getElementById("btn-collapse-sidebar").addEventListener("click", collapseSidebar);
    document.getElementById("btn-expand-sidebar").addEventListener("click", expandSidebar);

    // Search
    var _searchTimer = null;
    convoSearch.addEventListener("input", function () {
      var val = this.value.trim().toLowerCase();
      clearTimeout(_searchTimer);
      _searchTimer = setTimeout(function() {
        renderConvoList(val);
      }, 300);
    });

    // Dashboard drawer
    document.getElementById("btn-dashboard").addEventListener("click", openDashboard);
    document.getElementById("drawer-close").addEventListener("click", closeDashboard);
    document.getElementById("skin-dashboard-overlay").addEventListener("click", closeDashboard);

    // System Status drawer
    document.getElementById("btn-system-status").addEventListener("click", openStatusDrawer);
    document.getElementById("status-drawer-close").addEventListener("click", closeStatusDrawer);
    document.getElementById("skin-status-overlay").addEventListener("click", closeStatusDrawer);

    // Admin drawer
    document.getElementById("btn-admin").addEventListener("click", openAdminDrawer);
    var _wikiBtn = document.getElementById("btn-wiki");
    if (_wikiBtn) _wikiBtn.addEventListener("click", openWikiDrawer);
    var _wikiClose = document.getElementById("wiki-drawer-close");
    if (_wikiClose) _wikiClose.addEventListener("click", closeWikiDrawer);
    var _wikiOverlay = document.getElementById("skin-wiki-overlay");
    if (_wikiOverlay) _wikiOverlay.addEventListener("click", closeWikiDrawer);
    var _wikiModal = document.getElementById("wiki-entity-modal");
    if (_wikiModal) _wikiModal.addEventListener("click", function(e) {
      if (e.target === _wikiModal) _wikiModal.className = "closed";
    });
    var _wikiModalClose = document.getElementById("wiki-entity-modal-close");
    if (_wikiModalClose) _wikiModalClose.addEventListener("click", function() {
      document.getElementById("wiki-entity-modal").className = "closed";
    });
    document.getElementById("admin-drawer-close").addEventListener("click", closeAdminDrawer);
    document.getElementById("skin-admin-overlay").addEventListener("click", closeAdminDrawer);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        closeDashboard(); closeStatusDrawer(); closeAdminDrawer(); closeWikiDrawer();
        var helpOv = document.getElementById("shortcuts-overlay");
        if (helpOv) helpOv.remove();
      }
      // ? key (when not typing) → show keyboard shortcuts
      if (e.key === "?" && document.activeElement !== chatInput) {
        _showShortcuts();
      }
    });

    // Theme toggle
    document.getElementById("skin-theme-toggle").addEventListener("click", toggleTheme);

    // GWS connect
    document.getElementById("btn-gws-connect").addEventListener("click", handleGwsConnect);

    // Copy entire conversation
    document.getElementById("btn-copy-all").addEventListener("click", function() {
      var msgs = chatMessages.querySelectorAll(".message-user, .message-assistant");
      if (!msgs.length) { showToast("복사할 대화가 없습니다", "info"); return; }
      var lines = [];
      msgs.forEach(function(m) {
        var ce = m.querySelector(".message-content");
        var raw = (ce && ce.dataset.raw) || (ce && ce.textContent) || "";
        if (m.classList.contains("message-user")) {
          lines.push("Q: " + raw.trim());
        } else {
          lines.push("A: " + raw.trim());
        }
      });
      _copyText(lines.join("\n\n"), document.getElementById("btn-copy-all"));
    });
  }

  function closeMobileSidebar() {
    sidebar.classList.remove("open");
    mobileOverlay.classList.remove("active");
  }

  function collapseSidebar() {
    sidebar.style.display = "none";
    document.getElementById("btn-expand-sidebar").style.display = "flex";
    document.querySelector(".chat-topbar").classList.add("sidebar-collapsed");
  }

  function expandSidebar() {
    sidebar.style.display = "";
    document.getElementById("btn-expand-sidebar").style.display = "none";
    document.querySelector(".chat-topbar").classList.remove("sidebar-collapsed");
  }

  // ===== Conversations =====
  async function loadConversations() {
    try {
      var resp = await fetch("/api/conversations");
      conversations = await resp.json();
      renderConvoList();
    } catch (e) {
      console.error("Failed to load conversations:", e);
    }
  }

  // Pin helpers (localStorage-based, no DB change needed)
  function _getPinnedIds() {
    try { return JSON.parse(localStorage.getItem("pinned_convos") || "[]"); } catch (e) { return []; }
  }
  function _togglePin(id) {
    var pins = _getPinnedIds();
    var idx = pins.indexOf(id);
    if (idx >= 0) { pins.splice(idx, 1); } else { pins.push(id); }
    localStorage.setItem("pinned_convos", JSON.stringify(pins));
    renderConvoList();
  }

  function renderConvoList(searchFilter) {
    convoList.innerHTML = "";

    var filtered = conversations;
    if (searchFilter) {
      filtered = conversations.filter(function (c) {
        return c.title.toLowerCase().indexOf(searchFilter) !== -1;
      });
    }

    // Empty state
    if (filtered.length === 0) {
      var empty = document.createElement("div");
      empty.className = "convo-empty";
      empty.innerHTML = searchFilter
        ? '<span class="convo-empty-icon">🔍</span>검색 결과가 없습니다'
        : '<span class="convo-empty-icon">💬</span>새 대화를 시작해보세요';
      convoList.appendChild(empty);
      return;
    }

    // Render pinned conversations first
    var pinnedIds = _getPinnedIds();
    var pinned = filtered.filter(function(c) { return pinnedIds.indexOf(c.id) >= 0; });
    if (pinned.length > 0 && !searchFilter) {
      var pinHeader = document.createElement("div");
      pinHeader.className = "convo-group-header";
      pinHeader.textContent = "📌 고정됨";
      convoList.appendChild(pinHeader);
      pinned.forEach(function(c) { _renderConvoItem(c, searchFilter, true); });
    }

    // Group by date (exclude pinned from date groups)
    var groups = groupByDate(filtered);
    var groupLabels = {
      today: "오늘",
      yesterday: "어제",
      week: "지난 7일",
      month: "지난 30일",
      older: "이전",
    };

    var order = ["today", "yesterday", "week", "month", "older"];
    order.forEach(function (key) {
      var items = groups[key];
      if (!items || items.length === 0) return;

      // Group header
      var header = document.createElement("div");
      header.className = "convo-group-header";
      header.textContent = groupLabels[key];
      convoList.appendChild(header);

      // Filter out pinned from date groups (they're shown in their own section)
      var unpinned = items.filter(function(c) { return pinnedIds.indexOf(c.id) < 0; });
      if (unpinned.length === 0) return;
      unpinned.forEach(function(c) { _renderConvoItem(c, searchFilter, false); });
    });
  }

  function _renderConvoItem(c, searchFilter, isPinned) {
    var div = document.createElement("div");
    div.className = "convo-item" + (c.id === currentConvoId ? " active" : "");
    div.dataset.id = c.id;

    var icon = document.createElement("span");
    icon.className = "convo-icon";
    icon.innerHTML = isPinned
      ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="var(--accent)" stroke="var(--accent)" stroke-width="1.5"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
      : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
    div.appendChild(icon);

    var title = document.createElement("span");
    title.className = "convo-title";
    if (searchFilter) {
      var idx = c.title.toLowerCase().indexOf(searchFilter);
      if (idx >= 0) {
        title.innerHTML = _escHtml(c.title.slice(0, idx)) +
          '<mark class="search-hl">' + _escHtml(c.title.slice(idx, idx + searchFilter.length)) + '</mark>' +
          _escHtml(c.title.slice(idx + searchFilter.length));
      } else { title.textContent = c.title; }
    } else { title.textContent = c.title; }
    div.appendChild(title);

    var actions = document.createElement("div");
    actions.className = "convo-actions";

    // Pin/Unpin button
    var pinBtn = document.createElement("button");
    pinBtn.className = "convo-action-btn";
    pinBtn.title = isPinned ? "고정 해제" : "고정";
    pinBtn.innerHTML = isPinned
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="var(--accent)" stroke="var(--accent)" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    pinBtn.addEventListener("click", function(e) { e.stopPropagation(); _togglePin(c.id); });
    actions.appendChild(pinBtn);

    var editBtn = document.createElement("button");
    editBtn.className = "convo-action-btn";
    editBtn.title = "이름 변경";
    editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
    editBtn.addEventListener("click", function(e) { e.stopPropagation(); renameConversation(c.id, c.title); });
    actions.appendChild(editBtn);

    var delBtn = document.createElement("button");
    delBtn.className = "convo-action-btn convo-action-delete";
    delBtn.title = "삭제";
    delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
    delBtn.addEventListener("click", function(e) { e.stopPropagation(); _confirmDelete(c.id, c.title || "이 대화"); });
    actions.appendChild(delBtn);

    div.appendChild(actions);
    div.addEventListener("click", function() { loadConversation(c.id); closeMobileSidebar(); });
    convoList.appendChild(div);
  }

  function groupByDate(items) {
    var now = new Date();
    var todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var yesterdayStart = new Date(todayStart); yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    var weekStart = new Date(todayStart); weekStart.setDate(weekStart.getDate() - 7);
    var monthStart = new Date(todayStart); monthStart.setDate(monthStart.getDate() - 30);

    var groups = { today: [], yesterday: [], week: [], month: [], older: [] };

    items.forEach(function (c) {
      var d = new Date(c.updated_at);
      if (isNaN(d.getTime())) d = new Date();

      if (d >= todayStart) groups.today.push(c);
      else if (d >= yesterdayStart) groups.yesterday.push(c);
      else if (d >= weekStart) groups.week.push(c);
      else if (d >= monthStart) groups.month.push(c);
      else groups.older.push(c);
    });

    return groups;
  }

  function highlightActiveConvo() {
    document.querySelectorAll(".convo-item").forEach(function (el) {
      el.classList.toggle("active", el.dataset.id === currentConvoId);
    });
  }

  function _showSkeleton() {
    chatMessages.innerHTML = "";
    chatWelcome.style.display = "none";
    var skel = document.createElement("div");
    skel.className = "skeleton-container";
    skel.innerHTML =
      '<div class="skeleton-msg"><div class="skeleton-line w70"></div><div class="skeleton-line w40"></div></div>' +
      '<div class="skeleton-msg right"><div class="skeleton-line w50"></div></div>' +
      '<div class="skeleton-msg"><div class="skeleton-line w80"></div><div class="skeleton-line w60"></div><div class="skeleton-line w30"></div></div>';
    chatMessages.appendChild(skel);
  }

  async function loadConversation(id) {
    // Abort active stream if switching conversations — full cleanup
    if (isStreaming || currentAbortController) {
      if (currentAbortController) currentAbortController.abort();
      _stopTokenDrain();
      isStreaming = false;
      currentAbortController = null;
      _autoScrollActive = true;
      _resetSendBtn();
      // Remove streaming cursor from any active message
      var streamingMsg = chatMessages.querySelector(".message.streaming");
      if (streamingMsg) streamingMsg.classList.remove("streaming");
    }
    // === Cleanup: prevent memory leaks when switching conversations ===
    // Even if no stream is active, _S.el may still reference a previous
    // assistant message DOM node (set on the last _startTokenDrain and
    // never nulled after a normal stream completion). The _mdDebounce
    // timer's closure captures _S.el too — clear it to release refs.
    if (_S) {
      if (_S._mdDebounce) {
        clearTimeout(_S._mdDebounce);
        _S._mdDebounce = null;
      }
      _S.el = null;
      _S.text = "";
      _S.completedHtml = "";
      _S.lastCompleted = "";
      _S.queue = [];
    }
    // Reset pending scroll RAF so it doesn't race with the new DOM
    _scrollRafPending = false;
    _autoScrollActive = true;
    try {
      _showSkeleton();
      var resp = await fetch("/api/conversations/" + id);
      if (!resp.ok) return;
      var data = await resp.json();
      currentConvoId = id;
      currentMessages = [];

      if (data.model) modelSelect.value = data.model;

      chatMessages.innerHTML = "";
      chatWelcome.style.display = "none";

      // Load feedback data for this conversation
      await _loadFeedbackForConversation(id);

      data.messages.forEach(function (m) {
        var msgEl = appendMessage(m.role, m.content, false, m.created_at);
        currentMessages.push({ role: m.role, content: m.content });
        // Add feedback buttons to existing assistant messages
        if (m.role === "assistant" && m.id && msgEl) {
          var actions = msgEl.querySelector(".msg-actions");
          if (!actions) {
            actions = document.createElement("div");
            actions.className = "msg-actions";
            msgEl.appendChild(actions);
          }
          _addFeedbackButtons(actions, m.id);
        }
      });

      // Show follow-ups for last assistant message
      if (data.messages.length > 0) {
        var lastMsg = data.messages[data.messages.length - 1];
        if (lastMsg.role === "assistant") {
          // Follow-up chips removed
        }
      }

      scrollToBottom();
      highlightActiveConvo();
    } catch (e) {
      console.error("Failed to load conversation:", e);
    }
  }

  async function createConversation() {
    try {
      var resp = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New Chat", model: modelSelect.value }),
      });
      var convo = await resp.json();
      currentConvoId = convo.id;
      conversations.unshift(convo);
      renderConvoList();
      return convo.id;
    } catch (e) {
      console.error("Failed to create conversation:", e);
      return null;
    }
  }

  async function deleteConversation(id) {
    try {
      await fetch("/api/conversations/" + id, { method: "DELETE" });
      conversations = conversations.filter(function (c) { return c.id !== id; });
      renderConvoList();
      if (currentConvoId === id) {
        currentConvoId = null;
        currentMessages = [];
        showWelcome();
        hideFollowups();
      }
    } catch (e) {
      console.error("Failed to delete:", e);
    }
  }

  function renameConversation(id, oldTitle) {
    var overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.innerHTML =
      '<div class="confirm-dialog">' +
      '<p style="margin-bottom:12px;font-weight:600;">대화 이름 변경</p>' +
      '<input class="rename-input" type="text" value="' + (oldTitle || "").replace(/"/g, "&quot;") + '" maxlength="100" autofocus>' +
      '<div class="confirm-actions" style="margin-top:16px;">' +
      '<button class="confirm-cancel">취소</button>' +
      '<button class="confirm-delete" style="background:var(--accent);border-color:var(--accent);">저장</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    var input = overlay.querySelector(".rename-input");
    input.select();
    input.addEventListener("keydown", function(e) {
      if (e.key === "Enter") doRename();
      if (e.key === "Escape") overlay.remove();
    });
    overlay.querySelector(".confirm-cancel").addEventListener("click", function() { overlay.remove(); });
    overlay.querySelector(".confirm-delete").addEventListener("click", doRename);
    overlay.addEventListener("click", function(e) { if (e.target === overlay) overlay.remove(); });

    async function doRename() {
      var newTitle = input.value.trim();
      if (!newTitle || newTitle === oldTitle) { overlay.remove(); return; }
      overlay.remove();
      try {
        await fetch("/api/conversations/" + id, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle }),
        });
        var c = conversations.find(function (x) { return x.id === id; });
        if (c) c.title = newTitle;
        renderConvoList();
      } catch (e) {
        console.error("Failed to rename:", e);
      }
    }
  }

  async function saveMessage(role, content) {
    return saveMessageTo(currentConvoId, role, content);
  }

  // Same as saveMessage but targets an explicit conversation id instead of
  // the global currentConvoId. Needed when the user switches conversations
  // while a save from a previous conversation is still in flight — the save
  // must land on the conversation it belongs to, not whatever is "current" now.
  async function saveMessageTo(convoId, role, content) {
    if (!convoId) return null;
    try {
      var resp = await fetch("/api/conversations/" + convoId + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: role, content: content }),
      });
      var data = await resp.json();
      await loadConversations();
      return data.id || null;
    } catch (e) {
      console.error("Failed to save message:", e);
    }
  }

  // ===== Image Helpers =====
  function updateSendButton() {
    btnSend.disabled = !(chatInput.value.trim() || pendingImages.length > 0);
  }

  function addImageFiles(fileList) {
    for (var i = 0; i < fileList.length; i++) {
      var file = fileList[i];
      if (ALLOWED_IMAGE_TYPES.indexOf(file.type) === -1) {
        alert("지원되지 않는 이미지 형식입니다: " + file.name + "\nPNG, JPEG, GIF, WebP만 가능합니다.");
        continue;
      }
      if (file.size > MAX_IMAGE_SIZE) {
        alert("이미지가 너무 큽니다: " + file.name + "\n최대 10MB까지 가능합니다.");
        continue;
      }
      // Read as data URL
      (function (f) {
        var reader = new FileReader();
        reader.onload = function (e) {
          pendingImages.push({ file: f, dataUrl: e.target.result });
          renderImagePreviews();
          updateSendButton();
        };
        reader.readAsDataURL(f);
      })(file);
    }
  }

  function renderImagePreviews() {
    if (pendingImages.length === 0) {
      imagePreviewStrip.style.display = "none";
      imagePreviewStrip.innerHTML = "";
      return;
    }
    imagePreviewStrip.style.display = "flex";
    imagePreviewStrip.innerHTML = "";
    pendingImages.forEach(function (img, idx) {
      var item = document.createElement("div");
      item.className = "image-preview-item";

      var thumb = document.createElement("img");
      thumb.src = img.dataUrl;
      thumb.alt = "Preview";
      item.appendChild(thumb);

      var removeBtn = document.createElement("button");
      removeBtn.className = "image-preview-remove";
      removeBtn.innerHTML = "&times;";
      removeBtn.title = "제거";
      removeBtn.addEventListener("click", function () {
        pendingImages.splice(idx, 1);
        renderImagePreviews();
        updateSendButton();
      });
      item.appendChild(removeBtn);

      imagePreviewStrip.appendChild(item);
    });
  }

  function clearPendingImages() {
    pendingImages = [];
    renderImagePreviews();
  }

  function _resetSendBtn() {
    btnSend.classList.remove("stop-mode");
    btnSend.disabled = false;
    btnSend.title = "전송";
    btnSend.onclick = null;
  }

  // ===== Send Message =====
  // ===== Token Buffer Queue — smooth streaming like ChatGPT =====
  // All streaming state in one object to avoid closure/scope issues
  var _S = {
    queue: [],
    running: false,
    el: null,
    text: "",           // accumulated full text (replaces local aiContent during streaming)
    completedHtml: "",
    lastCompleted: "",
    _mdDebounce: null,  // markdown parse debounce timer
  };

  function _startTokenDrain(contentEl) {
    _S.el = contentEl;
    _S.running = true;
    _S.text = "";
    _S.completedHtml = "";
    _S.lastCompleted = "";
    // NOTE: do NOT reset _S.queue here — the first content chunk is pushed
    // BEFORE this is called, so clearing the queue silently drops it.
    // Queue reset happens at stream start instead (before the reader loop).
    _scheduleDrain();
  }

  function _scheduleDrain() {
    if (!_S.running) return;
    requestAnimationFrame(_drainFrame);
  }

  function _drainFrame() {
    if (!_S.running || !_S.el) return;

    // Adaptive speed: take more chars when queue is large
    var queueLen = 0;
    for (var i = 0; i < _S.queue.length; i++) queueLen += _S.queue[i].length;

    var take = queueLen > 200 ? 20 : queueLen > 50 ? 8 : 3;

    // Drain 'take' characters from queue
    var drained = 0;
    while (_S.queue.length > 0 && drained < take) {
      var front = _S.queue[0];
      var need = take - drained;
      if (front.length <= need) {
        _S.text += front;
        drained += front.length;
        _S.queue.shift();
      } else {
        _S.text += front.slice(0, need);
        _S.queue[0] = front.slice(need);
        drained += need;
      }
    }

    if (drained > 0) {
      _renderStream();
    }

    // Continue draining if there's more, or keep alive waiting for new tokens
    if (_S.queue.length > 0) {
      _scheduleDrain();
    } else if (_S.running) {
      setTimeout(_scheduleDrain, 16);
    }
  }

  function _renderStream() {
    var el = _S.el;
    if (!el) return;

    // Split into completed paragraphs (\n\n) and in-progress tail
    var splitIdx = _S.text.lastIndexOf("\n\n");
    var completedText, tailText;
    if (splitIdx >= 0) {
      completedText = _S.text.slice(0, splitIdx + 2);
      tailText = _S.text.slice(splitIdx + 2);
    } else {
      completedText = "";
      tailText = _S.text;
    }

    // Re-render completed part only when it changes (stable DOM) — debounced 50ms
    if (completedText !== _S.lastCompleted) {
      _S.lastCompleted = completedText;
      clearTimeout(_S._mdDebounce);
      _S._mdDebounce = setTimeout(function () {
        try {
          _S.completedHtml = _sanitizeHtml(marked.parse(stripFollowupBlock(completedText), { breaks: true, gfm: true }));
        } catch (e) {
          _S.completedHtml = "<p>" + completedText.replace(/</g, "&lt;") + "</p>";
        }
        // Re-render after debounced parse
        var tailH = _S.text.slice((_S.text.lastIndexOf("\n\n") >= 0 ? _S.text.lastIndexOf("\n\n") + 2 : 0));
        var tailHtm = tailH ? '<span class="stream-tail">' + tailH.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>") + '</span>' : "";
        if (_S.el) _S.el.innerHTML = _S.completedHtml + tailHtm;
      }, 50);
    }

    // Tail: escape HTML and show as raw (fast, no parse needed)
    var tailHtml = tailText ? '<span class="stream-tail">' + tailText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>") + '</span>' : "";

    el.innerHTML = _S.completedHtml + tailHtml;

    // Auto-scroll: follow streaming content unless user scrolled up
    if (_autoScrollActive) {
      scrollToBottom();
    }
  }

  function _stopTokenDrain() {
    _S.running = false;
    _S.el = null;
    _S.queue = [];
    _S.completedHtml = "";
    _S.lastCompleted = "";
    clearTimeout(_S._mdDebounce);
    _S._mdDebounce = null;
  }

  async function sendMessage() {
    var text = chatInput.value.trim();
    var hasImages = pendingImages.length > 0;
    if ((!text && !hasImages) || isStreaming) return;

    // "1번", "2번", "3번", "1", "2", "3" → 후속 질문 칩 텍스트로 대체
    var numMatch = text.match(/^(\d)번?$/);
    if (numMatch && followupContainer.style.display !== "none") {
      var chipIdx = parseInt(numMatch[1]) - 1;
      var chips = followupContainer.querySelectorAll(".followup-chip");
      if (chipIdx >= 0 && chipIdx < chips.length) {
        text = chips[chipIdx].textContent;
        chatInput.value = text;
      }
    }

    // Parse @@ source selections from input text
    var atAtMatches = text.match(/@@(\S+)/g) || [];
    var atAtKeys = atAtMatches.map(function(m) { return m.substring(2); })
      .filter(function(k) { return DATA_SOURCE_KEYS.indexOf(k) >= 0 || Object.keys(_DB_ALIASES).indexOf(k.toLowerCase()) >= 0; });
    // Resolve aliases
    atAtKeys = atAtKeys.map(function(k) { return _DB_ALIASES[k.toLowerCase()] || k; });
    // Remove @@ tokens from the displayed text
    var cleanText = text.replace(/@@\S+\s*/g, "").trim();
    if (atAtKeys.length > 0) {
      text = cleanText;
      showActiveSourceChips(atAtKeys);
    }

    lastUserQuery = text;
    var imagesToSend = pendingImages.slice();  // snapshot
    hideFollowups();

    if (!currentConvoId) {
      var id = await createConversation();
      if (!id) return;
      currentMessages = [];
    }
    // Snapshot which conversation this send belongs to. If the user switches
    // conversations while the response is still streaming/rendering, the
    // long async tail below must keep saving to THIS conversation, not
    // whatever becomes "current" later.
    var convoIdAtSend = currentConvoId;

    chatWelcome.style.display = "none";
    // Render user message with images in chat bubble
    appendUserMessage(text, imagesToSend);
    chatInput.value = "";
    chatInput.style.height = "auto";
    clearPendingImages();
    btnSend.disabled = true;
    // Determine sources: @@ explicit > slash override > null (server default: BQ+GWS+Direct)
    var _sendSources = null;  // null = server decides (BQ+GWS+Direct only)
    if (atAtKeys.length > 0) {
      _sendSources = atAtKeys;  // @@ explicitly selected
    } else if (slashOverrideSource) {
      _sendSources = slashOverrideSource;
    }
    // Clear one-time slash override after snapshot
    if (slashOverrideSource) {
      slashOverrideSource = null;
      var badge = document.getElementById("source-filter-badge");
      if (badge) badge.style.display = "none";
      updateSourceFilterBadge();
    }

    // Build content for API (multimodal if images present)
    var apiContent;
    if (imagesToSend.length > 0) {
      apiContent = [];
      imagesToSend.forEach(function (img) {
        apiContent.push({
          type: "image_url",
          image_url: { url: img.dataUrl }
        });
      });
      if (text) {
        apiContent.push({ type: "text", text: text });
      }
    } else {
      apiContent = text;
    }

    // Add user message to in-memory history
    currentMessages.push({ role: "user", content: apiContent });
    // Save only text to DB (no images in SQLite). Target convoIdAtSend
    // explicitly — the user could switch conversations during this await.
    await saveMessageTo(convoIdAtSend, "user", text || "[Image]");
    scrollToBottom();

    // Use in-memory messages for API (reliable, no DOM parsing)
    var messages = currentMessages.slice();

    // Stream response — reset ALL previous streaming state first
    _stopTokenDrain();
    isStreaming = true;
    _autoScrollActive = true;  // Re-enable auto-scroll on new message
    if (currentAbortController) currentAbortController.abort();
    currentAbortController = new AbortController();
    _S.text = "";  // Reset stream text for new message
    var detectedSource = "";
    var detectedSourceLabel = "";
    var aiMsgEl = appendMessage("assistant", "", true);
    var contentEl = aiMsgEl.querySelector(".message-content");

    // Add streaming class for cursor animation
    aiMsgEl.classList.add("streaming");
    scrollToBottom();

    // Wave 1: Client-side pre-routing — show skeleton UI immediately
    var _preRoute = _clientPreRoute(text);
    if (_preRoute !== "direct") {
      var _preLoadingMsgs = {
        bigquery: "📊 데이터 조회 중...",
        notion: "📋 Notion 문서 검색 중...",
        cs: "🧴 CS Q&A 검색 중...",
        gws: "📧 Google Workspace 확인 중...",
        multi: "📈 종합 분석 중...",
      };
      var _preTyping = contentEl.querySelector(".typing-indicator");
      if (_preTyping && _preLoadingMsgs[_preRoute]) {
        _preTyping.innerHTML = '<span class="loading-text">' + _preLoadingMsgs[_preRoute] + '</span>';
      }
    }

    // Transform send button → stop button
    btnSend.disabled = false;
    btnSend.classList.add("stop-mode");
    btnSend.title = "생성 중지";
    btnSend.onclick = function() {
      if (currentAbortController) currentAbortController.abort();
    };

    try {
      var response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelSelect.value,
          messages: messages,
          stream: true,
          brand_filter: (currentUser && currentUser.my_brand_filter) || null,
          enabled_sources: _sendSources,
          enabled_team_resources: getEnabledTeamResPayload()
        }),
        signal: currentAbortController.signal,
      });

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

      // Fresh queue per stream (leftovers from an aborted previous stream)
      _S.queue = [];

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        buffer += decoder.decode(result.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line.startsWith("data: ")) continue;
          var data = line.slice(6);
          if (data === "[DONE]") continue;

          try {
            var parsed = JSON.parse(data);
            var delta = parsed.choices && parsed.choices[0] && parsed.choices[0].delta;
            if (delta && delta.content) {
              var pushedContent = false;
              var srcMatch = delta.content.match(/<!-- source:([\w:+\s\u0080-\uFFFF]+?) -->/);
              if (srcMatch) {
                var srcParts = srcMatch[1].split(":");
                detectedSource = srcParts[0];
                if (srcParts[1]) detectedSourceLabel = srcParts[1];
                // Route-specific loading message
                var loadingMsgs = {
                  bigquery: "📊 데이터 조회 중...",
                  notion: "📋 Notion 문서 검색 중...",
                  cs: "🧴 CS Q&A 검색 중...",
                  gws: "📧 Google Workspace 확인 중...",
                  multi: "📈 종합 분석 중...",
                };
                var typingEl = aiMsgEl.querySelector(".typing-indicator");
                if (typingEl && loadingMsgs[detectedSource]) {
                  typingEl.innerHTML = '<span class="loading-text">' + loadingMsgs[detectedSource] + '</span>';
                }
                var stripped = delta.content.replace(/<!-- source:[\w:+\s\u0080-\uFFFF]+? -->/, "");
                if (stripped) { _S.queue.push(stripped); pushedContent = true; }
              } else {
                // Filter out thinking/reasoning patterns from Claude
                var text = delta.content;
                // Skip lines that look like internal thinking
                if (/^(The user|I should|I need to|Let me|I'll |I can|I don't|Actually|Wait|Hmm)/i.test(text.trim())) {
                  continue;
                }
                // Strip thinking blocks
                text = text.replace(/<thinking>[\s\S]*?<\/thinking>/g, "");
                text = text.replace(/\[thinking\][\s\S]*?\[\/thinking\]/g, "");
                if (text) { _S.queue.push(text); pushedContent = true; }
              }
              // Start token drain animation only once real content arrives —
              // keeps the route-specific loading indicator visible during the
              // silent SQL-generation/execution window instead of it being
              // removed on the same tick it was set.
              if (pushedContent) {
                var typing = aiMsgEl.querySelector(".typing-indicator");
                if (typing) typing.remove();
                if (!_S.running) _startTokenDrain(contentEl);
              }
            }
          } catch (e) { /* skip */ }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        _stopTokenDrain();  // Stop token drain to prevent freeze
        var typing = aiMsgEl.querySelector(".typing-indicator");
        if (typing) typing.remove();
        aiMsgEl.classList.remove("streaming");
        _resetSendBtn();
        isStreaming = false;
        currentAbortController = null;
        _autoScrollActive = true;
        return;
      }
      _S.text = "오류가 발생했습니다: " + e.message;
      showToast("응답 중 오류가 발생했습니다", "error");
      contentEl.innerHTML = "";
      var errCard = document.createElement("div");
      errCard.className = "error-card";
      errCard.appendChild(document.createTextNode("⚠️ " + _S.text));
      errCard.appendChild(document.createElement("br"));
      var errRetryBtn = document.createElement("button");
      errRetryBtn.className = "error-retry-btn";
      errRetryBtn.textContent = "다시 시도";
      errRetryBtn.addEventListener("click", function () {
        document.querySelector("#chat-input").value = lastUserQuery;
        document.querySelector("#btn-send").click();
      });
      errCard.appendChild(errRetryBtn);
      contentEl.appendChild(errCard);
    }

    // Flush remaining tokens from queue
    while (_S.queue.length > 0) {
      _S.text += _S.queue.shift();
    }
    _stopTokenDrain();

    var typing = aiMsgEl.querySelector(".typing-indicator");
    if (typing) typing.remove();

    // Remove streaming cursor
    aiMsgEl.classList.remove("streaming");
    _resetSendBtn();

    var cleanContent = _S.text.replace(/<!-- source:\w+ -->/g, "");

    // Auto-open Google OAuth popup if GWS auth required
    var gwsAuthMatch = cleanContent.match(/<!-- gws-auth:(https?:\/\/[^\s]+) -->/);
    if (gwsAuthMatch) {
      cleanContent = cleanContent.replace(/<!-- gws-auth:[^\s]+ -->/, "");
      setTimeout(function() { window.open(gwsAuthMatch[1], "google_auth", "width=500,height=700,left=200,top=100"); }, 500);
    }

    contentEl.dataset.raw = cleanContent;
    // Batch all 3 rendering passes in a single RAF to minimize layout thrashing
    renderMarkdown(contentEl, cleanContent);
    requestAnimationFrame(function() {
      detectAndRenderCharts(contentEl, cleanContent);
      highlightCodeBlocks(contentEl);
    });

    // Add message action buttons (copy + feedback)
    var actionsDiv = document.createElement("div");
    actionsDiv.className = "msg-actions";
    var copyBtn = document.createElement("button");
    copyBtn.className = "msg-action-btn";
    copyBtn.title = "복사";
    copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.addEventListener("click", function() {
      var msg = this.closest(".message");
      var ce = msg && msg.querySelector(".message-content");
      var text = (ce && ce.dataset.raw) || (ce && ce.textContent) || "";
      _copyText(text, this);
    });
    actionsDiv.appendChild(copyBtn);
    aiMsgEl.appendChild(actionsDiv);

    if (detectedSource && detectedSource !== "direct") {
      addSourceBadge(aiMsgEl, detectedSource, detectedSourceLabel);
    }

    if (currentConvoId !== convoIdAtSend) {
      // User switched away from this conversation while the response was
      // still streaming/rendering. The answer still belongs to convoIdAtSend
      // and must be persisted there — but it must NOT be pushed into the
      // (now different) global currentMessages, nor saved under the new
      // currentConvoId, nor drive UI state (follow-ups/scroll) for the
      // conversation the user is currently looking at.
      await saveMessageTo(convoIdAtSend, "assistant", cleanContent);
    } else {
      currentMessages.push({ role: "assistant", content: cleanContent });
      var savedMsgId = await saveMessage("assistant", cleanContent);

      // Add feedback buttons after message is saved (need message ID)
      if (savedMsgId) {
        _addFeedbackButtons(actionsDiv, savedMsgId);
      }

      clearActiveSourceChips();  // Clear @@ chips after response complete
      showFollowups(text, cleanContent);
      scrollToBottom();
    }

    isStreaming = false;
    currentAbortController = null;
  }

  // ===== Follow-up Suggestions =====
  function showFollowups(query, answer) {
    var suggestions = pickFollowups(query, answer);
    if (suggestions.length === 0) { hideFollowups(); return; }

    followupContainer.innerHTML = "";
    suggestions.forEach(function (s) {
      var btn = document.createElement("button");
      btn.className = "followup-chip";
      btn.textContent = s;
      btn.addEventListener("click", function () {
        chatInput.value = s;
        chatInput.dispatchEvent(new Event("input"));
        sendMessage();
      });
      followupContainer.appendChild(btn);
    });
    followupContainer.style.display = "flex";
  }

  function hideFollowups() {
    followupContainer.style.display = "none";
    followupContainer.innerHTML = "";
  }

  /**
   * Extract follow-up suggestions from LLM answer, fallback to hardcoded pool.
   * LLM format: > 💡 **이런 것도 물어보세요** \n > - question1 \n > - question2
   */
  function pickFollowups(query, answer) {
    // 1. Try extracting LLM-generated follow-ups from answer
    var llmFollowups = extractFollowupsFromAnswer(answer);
    if (llmFollowups.length >= 2) return llmFollowups.slice(0, 3);

    // 2. Fallback: hardcoded pool
    var q = (query || "").toLowerCase();
    var pool = [];

    if (/쇼피|shopee/.test(q)) pool = FOLLOWUP_POOLS.shopee;
    else if (/아마존|amazon/.test(q)) pool = FOLLOWUP_POOLS.amazon;
    else if (/틱톡|tiktok/.test(q)) pool = FOLLOWUP_POOLS.tiktok;
    else if (/@@cs|cs |고객|반품|배송|교환|환불|성분|문의|사용법|앰플|크림|토너|루틴|피부|제품.*(효능|성분|사용)/.test(q)) pool = FOLLOWUP_POOLS.cs;
    else if (/매출|수량|순위|비교|추이|증감|국가|플랫폼|광고|ROAS|마케팅/.test(q)) pool = FOLLOWUP_POOLS.sales;
    else pool = FOLLOWUP_POOLS.general;

    pool = pool.filter(function (s) { return s !== query; });
    var shuffled = pool.slice().sort(function () { return Math.random() - 0.5; });
    return shuffled.slice(0, 3);
  }

  /**
   * Parse LLM-generated follow-up suggestions from the answer text.
   * Matches patterns like:
   *   > 💡 **이런 것도 물어보세요**
   *   > - "2024년 미국 매출 알려줘"
   *   > - 일본 쇼피 매출 비교해줘
   */
  function extractFollowupsFromAnswer(answer) {
    if (!answer) return [];
    var suggestions = [];

    // Find the follow-up block header. Agents may emit multiple 💡 callouts
    // (e.g. tip/insight lines), so we must target the LAST 💡 line that looks
    // like a "이런 것도 물어보세요 / 질문 제안" header — not the first 💡 we see.
    var lines = answer.split("\n");
    var headerIdx = -1;
    for (var h = lines.length - 1; h >= 0; h--) {
      var ltrim = lines[h].trim();
      if (ltrim.indexOf("💡") === -1) continue;
      // Header must mention 물어보세요 / 질문 / followup — not a plain tip
      if (/물어보세요|질문|follow[- ]?up|try asking|ask these/i.test(ltrim)) {
        headerIdx = h;
        break;
      }
    }
    if (headerIdx === -1) return [];

    for (var i = headerIdx + 1; i < lines.length; i++) {
      var line = lines[i].trim();
      // Stop at empty line or new section (heading, horizontal rule)
      if (!line || line.startsWith("#") || line === "---") break;
      // Extract suggestion text from "> - text" or "- text" patterns
      var match = line.match(/^>?\s*[-*]\s*["""]?(.+?)["""]?\s*$/);
      if (match) {
        var text = match[1].trim();
        // Remove trailing quotes and markdown artifacts
        text = text.replace(/^["""]|["""]$/g, "").trim();
        // Drop placeholder leakage like "[구체적 후속 질문 1 — ...]"
        if (/^\[.*\]$/.test(text)) continue;
        if (/후속 질문|followup/i.test(text) && text.indexOf("[") !== -1) continue;
        if (text.length > 5 && text.length < 120) {
          suggestions.push(text);
        }
      }
    }
    return suggestions;
  }

  // ===== Source Badge (SVG icons matching system status) =====
  var SOURCE_BADGES = {
    bigquery: {
      label: "BigQuery",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
    },
    bigquery_fallback: {
      label: "BigQuery",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
    },
    team: {
      label: "Team",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
    },
    notion: {
      label: "Notion",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
    },
    gws: {
      label: "GWS",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
    },
    cs: {
      label: "CS Q&A",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
    },
    multi: {
      label: "Multi",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>'
    },
    direct: {
      label: "Direct",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
    },
  };

  function addSourceBadge(msgEl, source, customLabel) {
    var info = SOURCE_BADGES[source] || { label: source, svg: '' };
    var badge = document.createElement("div");
    badge.className = "source-badge";
    badge.innerHTML = info.svg + '<span>' + (customLabel || info.label) + '</span>';
    var contentEl = msgEl.querySelector(".message-content");
    if (contentEl) contentEl.insertBefore(badge, contentEl.firstChild);
  }

  // ===== Message Rendering =====
  function appendUserMessage(text, images, createdAt) {
    var div = document.createElement("div");
    div.className = "message message-user";
    div.setAttribute("role", "article");
    div.setAttribute("aria-label", "사용자 메시지");

    // User Avatar
    var avatar = document.createElement("div");
    avatar.className = "msg-avatar msg-avatar-user";
    var initial = (currentUser && currentUser.name) ? currentUser.name.charAt(0).toUpperCase() : "U";
    avatar.textContent = initial;
    div.appendChild(avatar);

    var ts = document.createElement("span");
    ts.className = "msg-timestamp";
    ts.textContent = _formatTimestamp(createdAt);
    div.appendChild(ts);

    var bubble = document.createElement("div");
    bubble.className = "message-content";
    bubble.dataset.raw = text || "[Image]";

    // Render images in chat bubble
    if (images && images.length > 0) {
      var grid = document.createElement("div");
      grid.className = "user-image-grid" + (images.length === 1 ? " single" : "");
      images.forEach(function (img) {
        var imgEl = document.createElement("img");
        imgEl.className = "user-uploaded-image";
        imgEl.src = img.dataUrl;
        imgEl.alt = "Uploaded image";
        grid.appendChild(imgEl);
      });
      bubble.appendChild(grid);
    }

    if (text) {
      var textEl = document.createElement("div");
      textEl.textContent = text;
      bubble.appendChild(textEl);
    }

    // Edit button for user messages
    var editBtn = document.createElement("button");
    editBtn.className = "msg-edit-btn";
    editBtn.title = "수정";
    editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
    editBtn.addEventListener("click", function() {
      _startEditMessage(div, bubble);
    });
    div.appendChild(editBtn);

    div.appendChild(bubble);
    chatMessages.appendChild(div);
    return div;
  }

  function _startEditMessage(msgEl, bubbleEl) {
    var rawText = bubbleEl.dataset.raw || bubbleEl.textContent || "";
    var textarea = document.createElement("textarea");
    textarea.className = "msg-edit-textarea";
    textarea.value = rawText;
    textarea.rows = Math.min(Math.max(rawText.split("\n").length, 2), 8);

    var btnRow = document.createElement("div");
    btnRow.className = "msg-edit-actions";
    var saveBtn = document.createElement("button");
    saveBtn.className = "msg-edit-save";
    saveBtn.textContent = "전송";
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "msg-edit-cancel";
    cancelBtn.textContent = "취소";

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(saveBtn);

    // Hide original content, show editor
    bubbleEl.style.display = "none";
    var editBtn = msgEl.querySelector(".msg-edit-btn");
    if (editBtn) editBtn.style.display = "none";
    msgEl.appendChild(textarea);
    msgEl.appendChild(btnRow);
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    cancelBtn.addEventListener("click", function() {
      textarea.remove();
      btnRow.remove();
      bubbleEl.style.display = "";
      if (editBtn) editBtn.style.display = "";
    });

    saveBtn.addEventListener("click", function() {
      var newText = textarea.value.trim();
      if (!newText) return;
      textarea.remove();
      btnRow.remove();
      bubbleEl.style.display = "";
      if (editBtn) editBtn.style.display = "";
      _resendEditedMessage(msgEl, newText);
    });

    textarea.addEventListener("keydown", function(e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        saveBtn.click();
      }
      if (e.key === "Escape") {
        cancelBtn.click();
      }
    });
  }

  function _resendEditedMessage(msgEl, newText) {
    // Find which user-message index msgEl is (before removal)
    var domUserMsgs = chatMessages.querySelectorAll(".message-user");
    var msgIndex = -1;
    for (var k = 0; k < domUserMsgs.length; k++) {
      if (domUserMsgs[k] === msgEl) { msgIndex = k; break; }
    }

    // Remove msgEl itself AND all messages after it.
    // sendMessage() below will append a fresh user bubble for newText,
    // so keeping msgEl would leave two identical user bubbles in the DOM.
    var siblings = Array.from(chatMessages.children);
    var idx = siblings.indexOf(msgEl);
    if (idx >= 0) {
      for (var i = siblings.length - 1; i >= idx; i--) {
        siblings[i].remove();
      }
    }

    // Truncate currentMessages at the edited user message (inclusive).
    // sendMessage() will re-push the new user turn, so we drop the old one here.
    if (msgIndex >= 0) {
      var cmIdx = -1;
      var uIdx = 0;
      for (var m = 0; m < currentMessages.length; m++) {
        if (currentMessages[m].role === "user") {
          if (uIdx === msgIndex) { cmIdx = m; break; }
          uIdx++;
        }
      }
      if (cmIdx >= 0) {
        currentMessages.splice(cmIdx);
      }
    }

    // Re-send via input
    hideFollowups();
    chatInput.value = newText;
    chatInput.dispatchEvent(new Event("input"));
    sendMessage();
  }

  function _formatTimestamp(dateStr) {
    var d = dateStr ? new Date(dateStr) : new Date();
    if (isNaN(d.getTime())) d = new Date();
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    return hh + ":" + mm;
  }

  function appendMessage(role, content, streaming, createdAt) {
    if (role === "user") {
      return appendUserMessage(content, null, createdAt);
    }

    var div = document.createElement("div");
    div.className = "message message-" + role;
    div.setAttribute("role", "article");
    div.setAttribute("aria-label", "AI 응답");

    // AI Avatar
    var avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.innerHTML = '<img src="/static/favicon.png" alt="AI" width="28" height="28">';
    div.appendChild(avatar);

    // Timestamp (visible on hover)
    var ts = document.createElement("span");
    ts.className = "msg-timestamp";
    ts.textContent = _formatTimestamp(createdAt);
    div.appendChild(ts);

    var bubble = document.createElement("div");
    bubble.className = "message-content";

    if (streaming) {
      bubble.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    } else {
      bubble.dataset.raw = content;
      renderMarkdown(bubble, content);
      detectAndRenderCharts(bubble, content);
      highlightCodeBlocks(bubble);
    }

    div.appendChild(bubble);
    chatMessages.appendChild(div);
    return div;
  }

  function _sanitizeHtml(html) {
    return window.DOMPurify ? DOMPurify.sanitize(html, { ADD_ATTR: ["target"] }) : html;
  }

  function renderMarkdown(el, text) {
    if (!text) { el.innerHTML = ""; return; }
    try {
      // Strip follow-up suggestion block from rendered content (shown as chips instead)
      var cleaned = stripFollowupBlock(text);
      el.innerHTML = _sanitizeHtml(marked.parse(cleaned, { breaks: true, gfm: true }));
      // Wave 3: Wrap tables in scroll container + copy button
      var tables = el.querySelectorAll("table");
      for (var t = 0; t < tables.length; t++) {
        if (!tables[t].parentElement.classList.contains("table-wrapper")) {
          var wrapper = document.createElement("div");
          wrapper.className = "table-wrapper";
          tables[t].parentNode.insertBefore(wrapper, tables[t]);
          wrapper.appendChild(tables[t]);
          // Add table copy button
          var tBtn = document.createElement("button");
          tBtn.className = "table-copy-btn";
          tBtn.textContent = "표 복사";
          tBtn.addEventListener("click", (function(tbl) {
            return function() { _copyTable(tbl, this); };
          })(tables[t]));
          wrapper.insertBefore(tBtn, wrapper.firstChild);
        }
      }
    } catch (e) {
      el.textContent = text;
    }
  }

  /**
   * Remove the "💡 이런 것도 물어보세요" blockquote from rendered markdown.
   * These suggestions are displayed as interactive chips below the message.
   */
  function stripFollowupBlock(text) {
    if (!text || text.indexOf("💡") === -1) return text;
    var lines = text.split("\n");
    var result = [];
    var inFollowup = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var stripped = line.trim();
      // Detect start of follow-up block
      if (stripped.indexOf("💡") !== -1 && (/물어보세요/.test(stripped) || /질문/.test(stripped))) {
        inFollowup = true;
        continue;
      }
      if (inFollowup) {
        // Continue skipping follow-up suggestion lines
        if (stripped.match(/^>?\s*[-*]\s*.+/) || stripped === ">" || stripped === "") {
          continue;
        }
        // End of follow-up block
        inFollowup = false;
      }
      result.push(line);
    }
    // Clean trailing empty lines
    while (result.length > 0 && result[result.length - 1].trim() === "") {
      result.pop();
    }
    return result.join("\n");
  }

  function highlightCodeBlocks(container) {
    container.querySelectorAll("pre code").forEach(function (block) {
      hljs.highlightElement(block);
      var pre = block.parentElement;
      if (pre && !pre.querySelector(".code-header")) {
        pre.style.position = "relative";
        // Language badge + copy button header
        var lang = (block.className.match(/language-(\w+)/) || [])[1] || "";
        var _langNames = { js: "JavaScript", ts: "TypeScript", py: "Python", sql: "SQL", html: "HTML", css: "CSS", json: "JSON", bash: "Bash", sh: "Shell", java: "Java", cpp: "C++", go: "Go", rust: "Rust", rb: "Ruby", php: "PHP" };
        var langDisplay = _langNames[lang] || (lang ? lang.charAt(0).toUpperCase() + lang.slice(1) : "Code");

        var header = document.createElement("div");
        header.className = "code-header";
        header.innerHTML = '<span class="code-lang">' + langDisplay + '</span>';
        var btn = document.createElement("button");
        btn.className = "code-copy-btn";
        btn.textContent = "Copy";
        btn.addEventListener("click", function () {
          _copyText(block.textContent, null);
          btn.textContent = "Copied!";
          btn.classList.add("copied");
          setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1500);
        });
        header.appendChild(btn);
        pre.insertBefore(header, pre.firstChild);
      }
    });
  }

  function detectAndRenderCharts(container, text) {
    var chartMatch = text.match(/```chart-config\s*\n([\s\S]*?)\n```/);
    if (!chartMatch) {
      chartMatch = text.match(/```json\s*\n(\{[\s\S]*?"type"\s*:[\s\S]*?"data"\s*:[\s\S]*?\})\s*\n```/);
    }
    if (!chartMatch) return;

    try {
      var config = JSON.parse(chartMatch[1]);
      var isDark = document.documentElement.classList.contains("dark");

      // 긴 라벨(제품명 등) 겹침 방지: 공통 접두사 계산 (원본은 툴팁에 유지)
      function _commonPrefixOf(list) {
        if (!list || list.length < 3) return "";
        for (var ci = 0; ci < list.length; ci++) {
          if (typeof list[ci] !== "string") return "";
        }
        var p = list[0];
        for (var i = 1; i < list.length && p.length; i++) {
          while (p.length && list[i].indexOf(p) !== 0) p = p.slice(0, -1);
        }
        var sp = p.lastIndexOf(" ");
        p = sp > 0 ? p.slice(0, sp + 1) : "";
        return p.length >= 6 ? p : "";
      }
      var _labelPrefix = _commonPrefixOf(config.data && config.data.labels);
      function _shortTick(raw, maxLen) {
        var s = String(raw);
        if (_labelPrefix && s.indexOf(_labelPrefix) === 0) s = s.slice(_labelPrefix.length);
        if (s.length > maxLen) s = s.slice(0, maxLen - 1) + "…";
        return s;
      }
      // 시리즈명(범례)도 공통 접두사가 길면 제거 — 전치된 제품별 멀티라인 대응
      var _dsList = (config.data && config.data.datasets) || [];
      var _dsPrefix = _commonPrefixOf(_dsList.map(function(d) { return d.label; }));
      if (_dsPrefix) {
        _dsList.forEach(function(d) {
          if (typeof d.label === "string" && d.label.indexOf(_dsPrefix) === 0) {
            d.label = d.label.slice(_dsPrefix.length);
          }
        });
      }

      // Theme-aware colors (read from CSS variables)
      var rootStyles = getComputedStyle(document.documentElement);
      var textColor = rootStyles.getPropertyValue("--text").trim() || (isDark ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.75)");
      var gridColor = rootStyles.getPropertyValue("--border").trim() || (isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)");
      var tooltipBg = isDark ? "rgba(30,30,30,0.95)" : "rgba(0,0,0,0.85)";

      // Apply theme to config
      if (config.options) {
        // Title
        if (config.options.plugins && config.options.plugins.title) {
          config.options.plugins.title.color = textColor;
        }
        // Legend
        if (config.options.plugins && config.options.plugins.legend && config.options.plugins.legend.labels) {
          config.options.plugins.legend.labels.color = textColor;
        }
        // Tooltip — no decimals, comma-formatted
        if (config.options.plugins && config.options.plugins.tooltip) {
          config.options.plugins.tooltip.backgroundColor = tooltipBg;
          config.options.plugins.tooltip.callbacks = {
            title: function(items) {
              // For horizontal bar, default title is the index. Use the label instead.
              if (items.length > 0) {
                var labels = items[0].chart.data.labels;
                if (labels && labels[items[0].dataIndex] != null) {
                  return labels[items[0].dataIndex];
                }
              }
              return items[0] ? items[0].label : "";
            },
            label: function(ctx) {
              var label = ctx.dataset.label || "";
              // For horizontal bar (indexAxis=y), value is on x-axis
              var isHoriz = ctx.chart.options.indexAxis === "y";
              var val;
              if (ctx.chart.config.type === "doughnut" || ctx.chart.config.type === "pie") {
                val = ctx.raw;
              } else if (ctx.parsed.r != null) {
                val = ctx.parsed.r;
              } else if (isHoriz) {
                val = ctx.parsed.x;
              } else {
                val = ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed.x;
              }
              var formatted;
              if (typeof val !== "number") { formatted = val; }
              else if (Math.abs(val) < 10) { formatted = parseFloat(val.toFixed(2)); }
              else if (Math.abs(val) < 1000) { formatted = parseFloat(val.toFixed(1)); }
              else { formatted = Math.round(val).toLocaleString(); }
              return label ? label + ": " + formatted : formatted;
            }
          };
        }
        // Scales
        if (config.options.scales) {
          var isHorizontalBar = config.options.indexAxis === "y";
          ["x", "y"].forEach(function(axis) {
            if (config.options.scales[axis]) {
              if (!config.options.scales[axis].ticks) config.options.scales[axis].ticks = {};
              config.options.scales[axis].ticks.color = textColor;
              // Category axis: return label text, not formatted index number
              // For horizontal_bar (indexAxis=y), Y is the category axis
              // For regular bar/line, X is the category axis
              var isCategoryAxis = (isHorizontalBar && axis === "y") || (!isHorizontalBar && axis === "x");
              if (isCategoryAxis) {
                // 가로바 y축은 24자, 회전되는 x축은 14자로 축약 (전체명은 툴팁에)
                var _maxTickLen = (isHorizontalBar && axis === "y") ? 24 : 14;
                config.options.scales[axis].ticks.callback = function(value) {
                  var labels = config.data && config.data.labels;
                  var lbl = (labels && labels[value] != null) ? labels[value] : value;
                  return _shortTick(lbl, _maxTickLen);
                };
              } else {
                // Numeric value axis: preserve decimals for small values
                config.options.scales[axis].ticks.callback = function(value) {
                  if (typeof value !== "number") return value;
                  if (Math.abs(value) < 10) return parseFloat(value.toFixed(2));
                  if (Math.abs(value) < 1000) return parseFloat(value.toFixed(1));
                  return Math.round(value).toLocaleString();
                };
              }
              if (config.options.scales[axis].grid) {
                config.options.scales[axis].grid.color = gridColor;
              }
              if (config.options.scales[axis].title) {
                config.options.scales[axis].title.color = textColor;
              }
            }
          });
        }
      }

      // Create chart container with modern styling
      var chartDiv = document.createElement("div");
      chartDiv.className = "chart-container";

      // Canvas with responsive height
      var canvas = document.createElement("canvas");
      var isHorizontal = config.options && config.options.indexAxis === "y";
      var labelCount = config.data && config.data.labels ? config.data.labels.length : 5;
      var h = isHorizontal ? Math.max(300, labelCount * 36 + 100) : 380;
      chartDiv.style.height = h + "px";
      chartDiv.appendChild(canvas);

      // Insert before the code block that contains the config
      var pres = container.querySelectorAll("pre");
      var inserted = false;
      for (var i = 0; i < pres.length; i++) {
        var code = pres[i].querySelector("code");
        if (code && code.textContent.indexOf('"type"') !== -1 && code.textContent.indexOf('"data"') !== -1) {
          pres[i].style.display = "none";
          pres[i].parentNode.insertBefore(chartDiv, pres[i]);
          inserted = true;
          break;
        }
      }
      if (!inserted) {
        container.appendChild(chartDiv);
      }

      // Build inline data-label plugin (always-visible labels, no external dep)
      var _chartType = config.type;
      var _isHoriz = isHorizontal;
      var _inlinePlugin = null;

      // 라벨 숫자 포맷: 1억 이상 → X.X억, 1만 이상 → X.X만, 그 외 콤마
      function _fmtLabelVal(val) {
        var abs = Math.abs(val);
        if (abs >= 100000000) return (val / 100000000).toFixed(1).replace(/\.0$/, "") + "억";
        if (abs >= 10000) return (val / 10000).toFixed(1).replace(/\.0$/, "") + "만";
        if (abs >= 1000) return Math.round(val).toLocaleString();
        if (abs < 10) return parseFloat(val.toFixed(2)).toString();
        return parseFloat(val.toFixed(1)).toString();
      }

      if (_chartType === "doughnut" || _chartType === "pie") {
        _inlinePlugin = {
          id: "inlineDoughnutLabels",
          afterDatasetsDraw: function(chart) {
            var ctx = chart.ctx;
            chart.data.datasets.forEach(function(dataset, dsIdx) {
              var meta = chart.getDatasetMeta(dsIdx);
              if (meta.hidden) return;
              var total = dataset.data.reduce(function(a, b) { return (a || 0) + (b || 0); }, 0);
              if (!total) return;
              meta.data.forEach(function(arc, idx) {
                var val = dataset.data[idx];
                var pct = (val / total * 100);
                if (pct < 3) return; // 너무 작은 조각은 건너뜀
                var midAngle = arc.startAngle + (arc.endAngle - arc.startAngle) / 2;
                var midRadius = (arc.innerRadius + arc.outerRadius) / 2;
                var x = arc.x + midRadius * Math.cos(midAngle);
                var y = arc.y + midRadius * Math.sin(midAngle);
                ctx.save();
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.font = "bold 12px Segoe UI, Arial, sans-serif";
                ctx.fillStyle = "#fff";
                ctx.shadowColor = "rgba(0,0,0,0.7)";
                ctx.shadowBlur = 4;
                ctx.fillText(pct.toFixed(1) + "%", x, y);
                ctx.restore();
              });
            });
          }
        };
      } else if (_chartType === "line") {
        _inlinePlugin = {
          id: "inlineLineLabels",
          afterDatasetsDraw: function(chart) {
            var ctx = chart.ctx;
            var isDarkMode = document.documentElement.classList.contains("dark");
            var labelColor = isDarkMode ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.72)";
            // 이미 그린 라벨 영역 — 겹치는 라벨은 그리지 않는다
            var drawnRects = [];
            function _collides(x1, y1, x2, y2) {
              for (var ri = 0; ri < drawnRects.length; ri++) {
                var r = drawnRects[ri];
                if (x1 < r[2] && x2 > r[0] && y1 < r[3] && y2 > r[1]) return true;
              }
              return false;
            }
            var visibleCount = chart.data.datasets.filter(function(d, i) {
              return !chart.getDatasetMeta(i).hidden;
            }).length;
            chart.data.datasets.forEach(function(dataset, dsIdx) {
              var meta = chart.getDatasetMeta(dsIdx);
              if (meta.hidden) return;
              // 포인트가 많으면 겹침 방지를 위해 일부만 표시 (마지막 포인트는 항상 표시)
              var step = Math.max(1, Math.ceil(meta.data.length / 16));
              meta.data.forEach(function(point, idx) {
                // 시리즈가 4개 이상이면 각 시리즈의 마지막 포인트만 라벨링
                if (visibleCount >= 4 && idx !== meta.data.length - 1) return;
                if (idx % step !== 0 && idx !== meta.data.length - 1) return;
                var val = dataset.data[idx];
                if (val == null) return;
                var fmt = _fmtLabelVal(val);
                ctx.save();
                ctx.font = "bold 11px Segoe UI, Arial, sans-serif";
                // 차트 좌우 경계 밖으로 라벨이 잘리지 않게 보정
                var halfW = ctx.measureText(fmt).width / 2;
                var lx = Math.min(Math.max(point.x, chart.chartArea.left + halfW), chart.chartArea.right - halfW);
                var bx1 = lx - halfW - 2, bx2 = lx + halfW + 2;
                var by2 = point.y - 4, by1 = by2 - 15;
                if (_collides(bx1, by1, bx2, by2)) { ctx.restore(); return; }
                drawnRects.push([bx1, by1, bx2, by2]);
                ctx.fillStyle = labelColor;
                ctx.textAlign = "center";
                ctx.textBaseline = "bottom";
                ctx.fillText(fmt, lx, point.y - 6);
                ctx.restore();
              });
            });
          }
        };
      } else if (_chartType === "bar") {
        _inlinePlugin = {
          id: "inlineBarLabels",
          afterDatasetsDraw: function(chart) {
            var ctx = chart.ctx;
            var isDarkMode = document.documentElement.classList.contains("dark");
            var labelColor = isDarkMode ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.72)";
            // 이미 그린 라벨 영역 — 겹치는 라벨은 그리지 않는다 (grouped bar 대응)
            var drawnRects = [];
            function _collides(x1, y1, x2, y2) {
              for (var ri = 0; ri < drawnRects.length; ri++) {
                var r = drawnRects[ri];
                if (x1 < r[2] && x2 > r[0] && y1 < r[3] && y2 > r[1]) return true;
              }
              return false;
            }
            chart.data.datasets.forEach(function(dataset, dsIdx) {
              var meta = chart.getDatasetMeta(dsIdx);
              if (meta.hidden) return;
              meta.data.forEach(function(bar, idx) {
                var val = dataset.data[idx];
                if (val == null) return;
                var fmt = _fmtLabelVal(val);
                var props = bar.getProps(["x", "y", "base", "width", "height"], true);
                ctx.save();
                ctx.font = "bold 11px Segoe UI, Arial, sans-serif";
                var w = ctx.measureText(fmt).width;
                var bx1, by1, bx2, by2;
                if (_isHoriz) {
                  bx1 = props.x + 5; bx2 = bx1 + w;
                  by1 = props.y - 7; by2 = props.y + 7;
                } else {
                  bx1 = props.x - w / 2 - 2; bx2 = props.x + w / 2 + 2;
                  by2 = props.y - 1; by1 = by2 - 15;
                }
                if (_collides(bx1, by1, bx2, by2)) { ctx.restore(); return; }
                drawnRects.push([bx1, by1, bx2, by2]);
                ctx.fillStyle = labelColor;
                if (_isHoriz) {
                  ctx.textAlign = "left";
                  ctx.textBaseline = "middle";
                  ctx.fillText(fmt, props.x + 5, props.y);
                } else {
                  ctx.textAlign = "center";
                  ctx.textBaseline = "bottom";
                  ctx.fillText(fmt, props.x, props.y - 3);
                }
                ctx.restore();
              });
            });
          }
        };
      }

      var finalConfig = _inlinePlugin
        ? Object.assign({}, config, { plugins: [_inlinePlugin] })
        : config;
      new Chart(canvas.getContext("2d"), finalConfig);

      // Add chart copy button
      var cBtn = document.createElement("button");
      cBtn.className = "chart-copy-btn";
      cBtn.textContent = "차트 복사";
      cBtn.addEventListener("click", function() { _copyChart(canvas, cBtn); });
      chartDiv.appendChild(cBtn);
    } catch (e) {
      console.warn("Chart render failed:", e);
    }
  }

  function showWelcome() {
    chatMessages.innerHTML = "";
    chatMessages.appendChild(chatWelcome);
    chatWelcome.style.display = "flex";
  }

  var _scrollRafPending = false;
  function scrollToBottom() {
    if (_scrollRafPending) return;
    _scrollRafPending = true;
    requestAnimationFrame(function() {
      chatMessages.scrollTop = chatMessages.scrollHeight;
      _scrollRafPending = false;
    });
  }

  // Scroll-to-bottom button + auto-scroll disable on user scroll-up
  var btnScrollBottom = document.getElementById("btn-scroll-bottom");
  var _lastScrollTop = 0;
  var _scrollThrottled = false;
  chatMessages.addEventListener("scroll", function () {
    if (_scrollThrottled) return;
    _scrollThrottled = true;
    requestAnimationFrame(function() {
      var distFromBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
      if (btnScrollBottom) {
        btnScrollBottom.style.display = distFromBottom > 200 ? "flex" : "none";
      }
      // Disable auto-scroll if user scrolls UP during streaming
      if (isStreaming && chatMessages.scrollTop < _lastScrollTop && distFromBottom > 100) {
        _autoScrollActive = false;
      }
      // Re-enable if user scrolls back to bottom
      if (distFromBottom < 30) {
        _autoScrollActive = true;
      }
      _lastScrollTop = chatMessages.scrollTop;
      _scrollThrottled = false;
    });
  }, { passive: true });
  if (btnScrollBottom) {
    btnScrollBottom.addEventListener("click", function () {
      _autoScrollActive = true;
      chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: "smooth" });
    });
  }

  // ===== Theme =====
  function toggleTheme() {
    var html = document.documentElement;
    if (html.classList.contains("dark")) {
      html.classList.replace("dark", "light");
      localStorage.theme = "light";
    } else {
      html.classList.replace("light", "dark");
      localStorage.theme = "dark";
    }
    updateTheme();
  }

  function updateTheme() {
    var isDark = document.documentElement.classList.contains("dark");
    var SUN = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
    var MOON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

    var btn = document.getElementById("skin-theme-toggle");
    btn.innerHTML = isDark ? SUN : MOON;
    btn.title = isDark ? "Light Mode" : "Dark Mode";

    var logoSrc = isDark ? "/static/splash-dark-new.png" : "/static/splash.png";
    var sidebarLogo = document.getElementById("sidebar-logo");
    var welcomeLogo = document.getElementById("welcome-logo");
    if (sidebarLogo) sidebarLogo.src = logoSrc;
    if (welcomeLogo) welcomeLogo.src = logoSrc;

    var hljsLink = document.getElementById("hljs-theme");
    if (hljsLink) {
      hljsLink.href = isDark
        ? "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css"
        : "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css";
    }
  }

  // ===== Dashboard Drawer =====
  function openDashboard() {
    var overlay = document.getElementById("skin-dashboard-overlay");
    var drawer = document.getElementById("skin-dashboard-drawer");
    var iframe = document.getElementById("dashboard-iframe");
    var theme = document.documentElement.classList.contains("light") ? "light" : "dark";
    var targetSrc = "/dashboard?theme=" + theme;
    // Always reload with current theme
    if (!iframe.src || !iframe.src.includes(targetSrc)) {
      iframe.src = targetSrc;
    }
    overlay.className = "open";
    drawer.className = "open";
  }

  function closeDashboard() {
    document.getElementById("skin-dashboard-overlay").className = "closed";
    document.getElementById("skin-dashboard-drawer").className = "closed";
  }

  // ===== System Status Drawer =====
  function openStatusDrawer() {
    pollSystemStatus(); // Refresh on open
    document.getElementById("skin-status-overlay").className = "open";
    document.getElementById("skin-status-drawer").className = "open";
  }

  function closeStatusDrawer() {
    document.getElementById("skin-status-overlay").className = "closed";
    document.getElementById("skin-status-drawer").className = "closed";
  }

  // ===== System Status (SVG icons) — clean names, no BQ prefix =====
  var _svgBar = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>';
  var _svgBox = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>';
  var _svgDollar = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';
  var _svgSearch = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
  var _svgUsers = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
  var _svgStar = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
  var _svgChat = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var _svgGlobe = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
  var _svgMonitor = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
  var _svgTarget = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>';
  var _svgBag = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>';
  var _svgUpload = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
  var _svgFile = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
  var _svgFolder = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  var SERVICE_ICONS = {
    // 매출
    "매출":           { label: "매출", svg: _svgBar },
    "제품":           { label: "제품", svg: _svgBox },
    "손익":           { label: "손익", svg: _svgBar },
    // 마케팅
    "광고":           { label: "광고", svg: _svgUpload },
    "마케팅":          { label: "마케팅", svg: _svgDollar },
    "Shopify":        { label: "Shopify", svg: _svgBag },
    "플랫폼":         { label: "플랫폼", svg: _svgMonitor },
    "인플루언서":      { label: "인플루언서", svg: _svgUsers },
    "아마존검색":      { label: "아마존검색", svg: _svgSearch },
    "메타광고":        { label: "메타광고", svg: _svgTarget },
    "아마존 리뷰":     { label: "아마존 리뷰", svg: _svgStar },
    "큐텐 리뷰":       { label: "큐텐 리뷰", svg: _svgStar },
    "쇼피 리뷰":       { label: "쇼피 리뷰", svg: _svgStar },
    "스마트스토어 리뷰": { label: "스마트스토어 리뷰", svg: _svgStar },
    // 팀별 자료
    "Craver":         { label: "Craver", svg: _svgGlobe },
    "DB":             { label: "DB", svg: _svgBar },
    "KBT":            { label: "KBT", svg: _svgGlobe },
    "JBT":            { label: "JBT", svg: _svgGlobe },
    "GM EAST":        { label: "GM EAST", svg: _svgGlobe },
    "GM WEST":        { label: "GM WEST", svg: _svgGlobe },
    "B2B1":           { label: "B2B1", svg: _svgGlobe },
    "B2B2":           { label: "B2B2", svg: _svgGlobe },
    "BCM":            { label: "BCM", svg: _svgBar },
    "PEOPLE":         { label: "PEOPLE", svg: _svgUsers },
    "IT":             { label: "IT", svg: _svgMonitor },
    "CS":             { label: "CS", svg: _svgChat },
    "BP":             { label: "BP (제품 Q&A)", svg: _svgChat },
    // 업무 도구
    "Notion":         { label: "Notion", svg: _svgFile },
    "CS Q&A":         { label: "CS Q&A", svg: _svgChat },
    "Google Workspace": { label: "GWS", svg: _svgFolder },
    // 시스템
    "Gemini API":     { label: "Gemini", svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>' },
    "Claude API":     { label: "Claude", svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>' },
    "GWS Token":      { label: "Token", svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' },
  };

  // ===== // Slash Command & Source Filter =====
  var slashOverrideSource = null;  // One-time override from // command
  var _slashTempSelection = [];    // Temp selection state for // multi-select

  // Quick-select presets for // command
  var SLASH_PRESETS = [
    { cmd: "매출", label: "매출 데이터", keys: ["매출", "제품"] },
    { cmd: "광고", label: "광고 데이터", keys: ["광고", "메타광고"] },
    { cmd: "리뷰", label: "리뷰 전체", keys: ["아마존 리뷰", "큐텐 리뷰", "쇼피 리뷰", "스마트스토어 리뷰"] },
    { cmd: "notion", label: "Notion", keys: ["Notion"] },
    { cmd: "cs", label: "CS Q&A", keys: ["CS Q&A"] },
    { cmd: "팀", label: "팀별 자료", _useGroup: "notion" },
    { cmd: "gws", label: "Google Workspace", keys: ["Google Workspace"] },
  ];

  function toggleSourceDropdown() {
    var dd = document.getElementById("slash-source-dropdown");
    if (!dd) return;

    // Toggle: if open, close
    if (dd.style.display === "block") {
      dd.style.display = "none";
      _slashTempSelection = [];
      return;
    }

    var filter = "";

    // Initialize temp selection from current enabledSources
    if (_slashTempSelection.length === 0) {
      _slashTempSelection = enabledSources.slice();
    }

    // Build multi-select dropdown with all sources
    var html = '<div class="slash-dd-title">다음 질문에 사용할 소스 선택</div>';

    // Quick presets row
    html += '<div class="slash-presets-row">';
    SLASH_PRESETS.forEach(function(p) {
      if (!filter || p.cmd.indexOf(filter) >= 0 || p.label.indexOf(filter) >= 0) {
        html += '<button class="slash-preset-btn" data-cmd="' + p.cmd + '">' + p.label + '</button>';
      }
    });
    html += '<button class="slash-preset-btn slash-preset-all">전체</button>';
    html += '<button class="slash-preset-btn slash-preset-none">해제</button>';
    html += '</div>';

    // Individual source checkboxes
    html += '<div class="slash-source-list">';
    DATA_SOURCE_KEYS.forEach(function(key) {
      if (!filter || key.toLowerCase().indexOf(filter) >= 0) {
        var checked = _slashTempSelection.indexOf(key) >= 0 ? ' checked' : '';
        html += '<label class="slash-source-item">' +
          '<input type="checkbox" class="slash-source-cb" data-key="' + key + '"' + checked + '>' +
          '<span>' + key + '</span></label>';
      }
    });
    html += '</div>';

    // Confirm button
    var selCount = _slashTempSelection.length;
    html += '<div class="slash-dd-footer">' +
      '<button class="slash-confirm-btn" id="slash-confirm">' + selCount + '개 소스로 질문하기</button>' +
      '<button class="slash-cancel-btn" id="slash-cancel">취소</button>' +
      '</div>';

    dd.innerHTML = html;
    dd.style.display = "block";

    // Checkbox listeners
    dd.querySelectorAll(".slash-source-cb").forEach(function(cb) {
      cb.addEventListener("change", function() {
        var key = this.getAttribute("data-key");
        var idx = _slashTempSelection.indexOf(key);
        if (this.checked && idx < 0) _slashTempSelection.push(key);
        else if (!this.checked && idx >= 0) _slashTempSelection.splice(idx, 1);
        var confirmBtn = document.getElementById("slash-confirm");
        if (confirmBtn) confirmBtn.textContent = _slashTempSelection.length + '개 소스로 질문하기';
      });
    });

    // Preset button listeners
    dd.querySelectorAll(".slash-preset-btn[data-cmd]").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var cmd = this.getAttribute("data-cmd");
        var preset = SLASH_PRESETS.find(function(p) { return p.cmd === cmd; });
        if (preset) {
          // Resolve dynamic group keys
          var pkeys = preset.keys;
          if (preset._useGroup) {
            var grp = SOURCE_GROUPS.find(function(g) { return g.id === preset._useGroup; });
            if (grp) pkeys = grp.keys;
          }
          // Toggle preset keys
          var allOn = pkeys.every(function(k) { return _slashTempSelection.indexOf(k) >= 0; });
          pkeys.forEach(function(k) {
            var idx = _slashTempSelection.indexOf(k);
            if (allOn) { if (idx >= 0) _slashTempSelection.splice(idx, 1); }
            else { if (idx < 0) _slashTempSelection.push(k); }
          });
          toggleSourceDropdown();
        }
      });
    });

    // Select all / none
    var allBtn = dd.querySelector(".slash-preset-all");
    if (allBtn) allBtn.addEventListener("click", function() {
      _slashTempSelection = DATA_SOURCE_KEYS.slice();
      toggleSourceDropdown();
    });
    var noneBtn = dd.querySelector(".slash-preset-none");
    if (noneBtn) noneBtn.addEventListener("click", function() {
      _slashTempSelection = [];
      toggleSourceDropdown();
    });

    // Confirm
    document.getElementById("slash-confirm").addEventListener("click", function() {
      slashOverrideSource = _slashTempSelection.slice();
      _slashTempSelection = [];
      chatInput.value = "";
      chatInput.focus();
      dd.style.display = "none";
      showSourceOverrideBadge(slashOverrideSource);
    });

    // Cancel
    document.getElementById("slash-cancel").addEventListener("click", function() {
      _slashTempSelection = [];
      chatInput.value = "";
      chatInput.focus();
      dd.style.display = "none";
    });
  }

  function showSourceOverrideBadge(keys) {
    var badge = document.getElementById("source-filter-badge");
    if (!badge) return;
    var label = keys.length === DATA_SOURCE_KEYS.length
      ? '전체 소스'
      : keys.length + '개 소스 선택됨';
    badge.innerHTML =
      '<span class="sfb-icon">&#9881;</span>' +
      '<span class="sfb-text">' + label + '</span>' +
      '<button class="sfb-clear" title="필터 해제">&times;</button>';
    badge.style.display = "flex";
    badge.querySelector(".sfb-clear").addEventListener("click", function() {
      slashOverrideSource = null;
      badge.style.display = "none";
      _updateSourceButton();
      updateSourceFilterBadge();
    });
    _updateSourceButton();
  }

  function _updateSourceButton() {
    var btn = document.getElementById("btn-source-select");
    if (!btn) return;
    var hasFilter = slashOverrideSource || enabledSources.length < DATA_SOURCE_KEYS.length;
    btn.classList.toggle("has-filter", !!hasFilter);
  }

  function updateSourceFilterBadge() {
    // Show persistent badge when not all sources enabled
    var badge = document.getElementById("source-filter-badge");
    if (!badge || slashOverrideSource) return;
    if (enabledSources.length < DATA_SOURCE_KEYS.length) {
      var count = enabledSources.length;
      badge.innerHTML =
        '<span class="sfb-icon">&#9881;</span>' +
        '<span class="sfb-text">' + count + '/' + DATA_SOURCE_KEYS.length + ' 소스 활성</span>' +
        '<button class="sfb-clear" title="전체 활성화">&times;</button>';
      badge.style.display = "flex";
      badge.querySelector(".sfb-clear").addEventListener("click", function() {
        enabledSources = DATA_SOURCE_KEYS.slice();
        saveEnabledSources();
        badge.style.display = "none";
        pollSystemStatus();  // Refresh checkboxes
      });
    } else {
      badge.style.display = "none";
    }
  }

  function pollAnnouncement() {
    fetch("/api/announcement")
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var banner = document.getElementById("announcement-banner");
        var text = document.getElementById("announcement-text");
        if (!banner || !text) return;
        if (data.active && data.message) {
          text.textContent = data.message;
          banner.style.display = "";
          // Push chat content down so banner doesn't overlap
          document.body.style.paddingTop = banner.offsetHeight + "px";
        } else {
          banner.style.display = "none";
          document.body.style.paddingTop = "";
        }
      })
      .catch(function() {});
  }

  function pollSystemStatus() {
    fetch("/safety/status")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.services) return;
        var container = document.getElementById("status-items");
        var inlineEl = document.getElementById("sidebar-status-inline");
        var maintenanceReason = (data.maintenance && data.maintenance.reason) || "";
        var issues = [];

        // Node type icons
        var _nodeIcons = {
          folder: "📁", sheet: "📊", page: "📋", database: "🗃️", text: "📝"
        };

        // Helper: render a single service row (non-tree services)
        function renderItem(name, svc) {
          var st = svc.status || "ok";
          var labels = { ok: "정상", updating: "업데이트 중", error: "오류" };
          var labelClass = st === "updating" ? " updating" : (st !== "ok" ? " error" : "");
          var info = SERVICE_ICONS[name] || { label: name, svg: '' };
          var detail = svc.detail || "";
          var alertMsg = "";
          if (st === "updating") alertMsg = svc.reason || maintenanceReason;
          else if (st === "error") alertMsg = detail;
          if (st === "updating") issues.push(info.label + ": 업데이트 중");
          else if (st === "error") issues.push(info.label + ": 오류");

          var isQueryable = DATA_SOURCE_KEYS.indexOf(name) >= 0;
          var isChecked = enabledSources.indexOf(name) >= 0;
          var checkboxHtml = isQueryable
            ? '<label class="status-checkbox-label"><input type="checkbox" class="status-source-cb" data-source="' + name + '"' + (isChecked ? ' checked' : '') + '></label>'
            : '';

          var detailText = (st === "ok" && detail && detail !== "loading") ? detail : "";
          var h = '<div class="status-item' + (st !== "ok" ? " status-alert" : "") + '">' +
            '<div class="status-item-row">' + checkboxHtml +
            '<span class="status-dot' + (st !== "ok" ? " error" : "") + '"></span>' +
            '<span class="status-icon">' + info.svg + '</span>' +
            '<span class="status-name">' + info.label + '</span>' +
            (detailText ? '<span class="status-detail-text">' + detailText + '</span>' : '') +
            '<span class="status-label' + labelClass + '">' + (labels[st] || st) + '</span>' +
            '</div>';
          if (alertMsg) {
            h += '<div class="status-msg-wrap"><div class="status-msg-ticker"><span>' + alertMsg + '</span></div></div>';
          }
          h += '</div>';
          return h;
        }

        // Helper: render tree node recursively (for team group)
        function renderTreeNode(node, team, depth) {
          var ntype = node.type || "text";
          var kids = node.children || [];
          var isLeaf = ntype !== "folder" && ntype !== "team";
          var hasKids = kids.length > 0;
          var icon = _nodeIcons[ntype] || "•";
          var checkedAttr = isTeamResEnabled(team, node.id) ? ' checked' : '';

          var h = '<div class="tree-node depth-' + depth + (hasKids ? ' has-kids' : '') + '" data-id="' + node.id + '">';
          h += '<div class="tree-row">';
          // Checkbox for all nodes
          h += '<input type="checkbox" class="tree-cb" data-team="' + team + '" data-id="' + node.id + '"' + checkedAttr + '>';
          if (hasKids) {
            h += '<span class="tree-toggle">▶</span>';
          } else {
            h += '<span class="tree-toggle-spacer"></span>';
          }
          h += '<span class="tree-icon">' + icon + '</span>';
          h += '<span class="tree-name">' + node.name + '</span>';
          if (hasKids) {
            var leafCount = _countLeaves(node);
            if (leafCount > 0) h += '<span class="tree-count">' + leafCount + '</span>';
          }
          h += '</div>';
          if (hasKids) {
            h += '<div class="tree-children">';
            kids.forEach(function(kid) { h += renderTreeNode(kid, team, depth + 1); });
            h += '</div>';
          }
          h += '</div>';
          return h;
        }

        function _countLeaves(node) {
          var kids = node.children || [];
          if (kids.length === 0) return (node.type !== "folder" && node.type !== "team") ? 1 : 0;
          var c = 0; kids.forEach(function(k) { c += _countLeaves(k); }); return c;
        }

        // Collect all leaf IDs for a team tree
        function _collectLeafIds(node, arr) {
          var kids = node.children || [];
          if (kids.length === 0 && node.type !== "folder" && node.type !== "team") {
            arr.push(node.id);
          }
          kids.forEach(function(k) { _collectLeafIds(k, arr); });
        }

        // Collect all node IDs (including folders) under a node
        function _collectAllIds(node, arr) {
          arr.push(node.id);
          (node.children || []).forEach(function(k) { _collectAllIds(k, arr); });
        }

        // Dynamic team keys: inject team names from API into SOURCE_GROUPS
        SOURCE_GROUPS.forEach(function(grp) {
          if (!grp._dynamic) return;
          var staticKeys = grp.keys.slice();
          var teamKeys = [];
          for (var svcName in data.services) {
            var svc = data.services[svcName];
            var isNotionTeam = (svc.tree !== undefined) || (typeof svc.detail === "string" && svc.detail.indexOf("chunks") >= 0);
            if (isNotionTeam && staticKeys.indexOf(svcName) < 0) {
              teamKeys.push(svcName);
              if (!SOURCE_ROUTE_MAP[svcName]) SOURCE_ROUTE_MAP[svcName] = "notion";
              if (!SERVICE_ICONS[svcName]) SERVICE_ICONS[svcName] = { label: svcName, svg: _svgGlobe };
            }
          }
          teamKeys.sort();
          grp.keys = teamKeys.concat(staticKeys);
          _rebuildDataSourceKeys();
          teamKeys.forEach(function(k) {
            if (enabledSources.indexOf(k) < 0) enabledSources.push(k);
          });
        });

        // Toolbar
        var html = '<div class="source-select-toolbar">' +
          '<button class="source-btn-all" id="source-select-all">전체 선택</button>' +
          '<button class="source-btn-none" id="source-deselect-all">전체 해제</button>' +
          '<span class="source-count-label" id="source-count-label">' + enabledSources.length + '/' + DATA_SOURCE_KEYS.length + '</span>' +
          '</div>';

        // Grouped rendering
        var renderedKeys = {};
        SOURCE_GROUPS.forEach(function(grp) {
          var groupEnabled = grp.keys.filter(function(k) { return enabledSources.indexOf(k) >= 0; }).length;
          var groupTotal = grp.keys.length;
          var allOn = groupEnabled === groupTotal;
          html += '<div class="status-group" data-group="' + grp.id + '">' +
            '<div class="status-group-header">' +
            '<label class="status-checkbox-label"><input type="checkbox" class="status-group-cb" data-group="' + grp.id + '"' + (allOn ? ' checked' : '') + (groupEnabled > 0 && !allOn ? ' data-indeterminate="1"' : '') + '></label>' +
            '<span class="status-group-emoji">' + grp.emoji + '</span>' +
            '<span class="status-group-label">' + grp.label + '</span>' +
            (grp.link ? '<a href="' + grp.link + '" target="_blank" class="status-group-link" onclick="event.stopPropagation()" title="Notion DB HUB 열기"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"/></svg> DB HUB</a>' : '') +
            '<span class="status-group-count">' + groupEnabled + '/' + groupTotal + '</span>' +
            '<span class="status-group-toggle">&#9660;</span>' +
            '</div>' +
            '<div class="status-group-items">';
          grp.keys.forEach(function(key) {
            var svc = data.services[key] || { status: "ok", detail: "대기" };
            if (svc.tree && svc.tree.length > 0) {
              // Team with tree structure
              var info = SERVICE_ICONS[key] || { label: key, svg: _svgGlobe };
              var isChecked = enabledSources.indexOf(key) >= 0;
              html += '<div class="status-item has-expand" data-team-key="' + key + '">' +
                '<div class="status-item-row">' +
                '<label class="status-checkbox-label"><input type="checkbox" class="status-source-cb team-select-all" data-source="' + key + '"' + (isChecked ? ' checked' : '') + '></label>' +
                '<span class="status-dot"></span>' +
                '<span class="status-icon">' + info.svg + '</span>' +
                '<span class="status-name">' + info.label + '</span>' +
                '<span class="status-detail-text">' + svc.detail + '</span>' +
                '<span class="status-label">정상</span>' +
                '<span class="status-expand-btn">▶</span>' +
                '</div>' +
                '<div class="status-sub-items tree-root">';
              svc.tree.forEach(function(child) {
                html += renderTreeNode(child, key, 1);
              });
              html += '</div></div>';
            } else {
              html += renderItem(key, svc);
            }
            renderedKeys[key] = true;
          });
          html += '</div></div>';
        });

        // Notion sync bar — append after main html
        var syncData = data.notion_sync || {};
        var syncRunning = !!syncData.running;
        var syncLastRun = syncData.last_run || null;
        var syncStats = syncData.last_stats || null;
        var syncError = syncData.error || null;

        var statsText = "";
        if (syncStats) {
          var parts = [];
          if (syncStats.new)      parts.push("신규 " + syncStats.new);
          if (syncStats.updated)  parts.push("업데이트 " + syncStats.updated);
          if (syncStats.deleted)  parts.push("삭제 " + syncStats.deleted);
          if (syncStats.errors)   parts.push("오류 " + syncStats.errors);
          if (parts.length === 0) parts.push("변동 없음");
          statsText = parts.join(" · ");
        }

        var syncBarHtml =
          '<div class="notion-sync-bar">' +
            '<div class="notion-sync-info">' +
              '<span class="notion-sync-icon">' + _svgFile + '</span>' +
              '<span class="notion-sync-title">Notion → Qdrant</span>' +
              (syncLastRun
                ? '<span class="notion-sync-time">마지막 동기화: ' + syncLastRun + '</span>'
                : '<span class="notion-sync-time">동기화 기록 없음</span>') +
              (statsText ? '<span class="notion-sync-stats">' + statsText + '</span>' : '') +
              (syncError ? '<span class="notion-sync-error">' + syncError + '</span>' : '') +
            '</div>' +
            '<button id="btn-notion-sync" class="notion-sync-btn' + (syncRunning ? ' syncing' : '') + '"' +
              (syncRunning ? ' disabled' : '') + '>' +
              (syncRunning
                ? '<span class="notion-sync-spinner"></span>동기화 중...'
                : '지금 동기화') +
            '</button>' +
          '</div>';

        container.innerHTML = html + syncBarHtml;

        // Quality alert bar — admin only, fetched separately
        if (currentUser && currentUser.role === "admin") {
          fetch("/api/admin/quality-flags")
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(qd) {
              if (!qd || !qd.flags || qd.flags.length === 0) return;
              var flagRows = qd.flags.map(function(f) {
                var issues = [];
                if (f.flag_accuracy) issues.push("정확도 " + (f.accuracy_rate != null ? (f.accuracy_rate * 100).toFixed(0) + "%" : "N/A") + " (기준 65%)");
                if (f.flag_speed)    issues.push("응답속도 " + (f.avg_response_ms / 1000).toFixed(1) + "s (기준 12s)");
                if (f.flag_context)  issues.push("컨텍스트 " + f.avg_context_len + "자 (기준 300자)");
                var routeLabel = f.route === "_all" ? "전체" : f.route;
                return '<div class="quality-flag-row">' +
                  '<span class="quality-route">' + routeLabel + '</span>' +
                  '<span class="quality-issues">' + issues.join(" · ") + '</span>' +
                  '</div>';
              }).join("");
              var bar = '<div class="quality-alert-bar"><div class="quality-alert-title">⚠️ 품질 임계치 초과 (' + qd.date + ')</div>' + flagRows + '</div>';
              container.innerHTML = html + bar + syncBarHtml;
            })
            .catch(function() {});
        }

        // Notion sync button handler
        var syncBtn = document.getElementById("btn-notion-sync");
        if (syncBtn && !syncRunning) {
          syncBtn.addEventListener("click", function() {
            syncBtn.disabled = true;
            syncBtn.classList.add("syncing");
            syncBtn.innerHTML = '<span class="notion-sync-spinner"></span>동기화 중...';
            fetch("/api/notion-sync", { method: "POST" })
              .then(function(r) { return r.json(); })
              .then(function(res) {
                if (!res.ok) {
                  syncBtn.disabled = false;
                  syncBtn.classList.remove("syncing");
                  syncBtn.innerHTML = "지금 동기화";
                  alert(res.error || "동기화 실패");
                } else {
                  // Poll until done
                  var pollTimer = setInterval(function() {
                    fetch("/api/notion-sync/status")
                      .then(function(r) { return r.json(); })
                      .then(function(st) {
                        if (!st.running) {
                          clearInterval(pollTimer);
                          pollSystemStatus();
                        }
                      });
                  }, 3000);
                }
              })
              .catch(function() {
                syncBtn.disabled = false;
                syncBtn.classList.remove("syncing");
                syncBtn.innerHTML = "지금 동기화";
              });
          });
        }

        // Set indeterminate state on group checkboxes
        container.querySelectorAll('.status-group-cb[data-indeterminate="1"]').forEach(function(cb) {
          cb.indeterminate = true;
        });

        // Group header click → toggle collapse
        container.querySelectorAll(".status-group-header").forEach(function(hdr) {
          hdr.addEventListener("click", function(e) {
            if (e.target.tagName === "INPUT") return;
            var grpEl = hdr.parentElement;
            grpEl.classList.toggle("collapsed");
          });
        });

        // Expandable team items — click row to expand
        container.querySelectorAll(".status-item.has-expand > .status-item-row").forEach(function(row) {
          row.addEventListener("click", function(e) {
            if (e.target.tagName === "INPUT") return;
            row.parentElement.classList.toggle("expanded");
          });
        });

        // Tree node toggle (expand/collapse folder)
        container.querySelectorAll(".tree-toggle").forEach(function(toggle) {
          toggle.addEventListener("click", function(e) {
            e.stopPropagation();
            var node = toggle.closest(".tree-node");
            if (node) node.classList.toggle("open");
          });
        });

        // Tree checkbox cascade
        container.querySelectorAll(".tree-cb").forEach(function(cb) {
          cb.addEventListener("change", function(e) {
            e.stopPropagation();
            var team = this.getAttribute("data-team");
            var nodeId = parseInt(this.getAttribute("data-id"));
            var checked = this.checked;
            // Cascade down: check/uncheck all children
            var parentNode = this.closest(".tree-node");
            if (parentNode) {
              parentNode.querySelectorAll(".tree-cb").forEach(function(childCb) {
                childCb.checked = checked;
              });
            }
            // Update enabledTeamRes
            _rebuildTeamRes(team, container);
          });
        });

        // Team select-all checkbox
        container.querySelectorAll(".team-select-all").forEach(function(cb) {
          cb.addEventListener("change", function(e) {
            e.stopPropagation();
            var team = this.getAttribute("data-source");
            var item = this.closest(".status-item");
            if (item) {
              item.querySelectorAll(".tree-cb").forEach(function(childCb) {
                childCb.checked = cb.checked;
              });
              _rebuildTeamRes(team, container);
            }
          });
        });

        // Group checkbox → toggle all keys in group
        container.querySelectorAll(".status-group-cb").forEach(function(cb) {
          cb.addEventListener("change", function() {
            var gid = this.getAttribute("data-group");
            var grp = SOURCE_GROUPS.find(function(g) { return g.id === gid; });
            if (!grp) return;
            grp.keys.forEach(function(k) {
              var idx = enabledSources.indexOf(k);
              if (cb.checked) { if (idx < 0) enabledSources.push(k); }
              else { if (idx >= 0) enabledSources.splice(idx, 1); }
            });
            saveEnabledSources();
            pollSystemStatus();
            updateSourceFilterBadge();
          });
        });

        // Select-all / deselect-all
        document.getElementById("source-select-all").addEventListener("click", function() {
          enabledSources = DATA_SOURCE_KEYS.slice();
          saveEnabledSources();
          pollSystemStatus();
          updateSourceFilterBadge();
        });
        document.getElementById("source-deselect-all").addEventListener("click", function() {
          enabledSources = [];
          saveEnabledSources();
          pollSystemStatus();
          updateSourceFilterBadge();
        });

        // Individual checkbox listeners
        container.querySelectorAll(".status-source-cb").forEach(function(cb) {
          cb.addEventListener("change", function() {
            toggleSource(this.getAttribute("data-source"));
            document.getElementById("source-count-label").textContent = enabledSources.length + '/' + DATA_SOURCE_KEYS.length;
            updateSourceFilterBadge();
          });
        });

        // Inline sidebar indicator
        if (inlineEl) {
          if (issues.length === 0) {
            inlineEl.innerHTML = '<div class="status-inline-ok">All OK</div>';
            inlineEl.className = "sidebar-status-inline all-ok";
          } else {
            var issueText = issues.join("  ·  ");
            inlineEl.innerHTML =
              '<div class="status-inline-alert">' +
              '<div class="status-inline-ticker"><span>' + issueText + '</span></div>' +
              '</div>';
            inlineEl.className = "sidebar-status-inline has-issues";
          }
        }
      })
      .catch(function () {});
  }

  // ===== GWS Google Account Connection =====
  var gwsConnected = false;
  var gwsGoogleEmail = "";

  function checkGwsStatus() {
    if (!currentUser || !currentUser.email) return;
    fetch("/auth/google/status?user_email=" + encodeURIComponent(currentUser.email))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        gwsConnected = data.authenticated;
        gwsGoogleEmail = data.google_email || "";
        updateGwsButton();
      })
      .catch(function () {});
  }

  function updateGwsButton() {
    var label = document.getElementById("gws-connect-label");
    var btn = document.getElementById("btn-gws-connect");
    if (gwsConnected) {
      if (gwsGoogleEmail) {
        label.textContent = gwsGoogleEmail;
      } else {
        label.textContent = "Google 연결됨";
      }
      btn.classList.add("connected");
      btn.title = gwsGoogleEmail
        ? gwsGoogleEmail + " — 클릭하여 연결 해제"
        : "Google 계정 연결 해제";
    } else {
      label.textContent = "Google 연결";
      gwsGoogleEmail = "";
      btn.classList.remove("connected");
      btn.title = "Google 계정 연결 (GWS 기능 사용)";
    }
  }

  function handleGwsConnect() {
    if (!currentUser || !currentUser.email) return;
    if (gwsConnected) {
      // Revoke
      var msg = gwsGoogleEmail
        ? gwsGoogleEmail + " 계정 연결을 해제하시겠습니까?"
        : "Google 계정 연결을 해제하시겠습니까?";
      if (!confirm(msg)) return;
      fetch("/auth/google/revoke?user_email=" + encodeURIComponent(currentUser.email), { method: "POST" })
        .then(function () { gwsConnected = false; gwsGoogleEmail = ""; updateGwsButton(); })
        .catch(function () {});
    } else {
      // Connect — open in new window
      window.open("/auth/google/login?user_email=" + encodeURIComponent(currentUser.email), "gws_auth", "width=500,height=600");
      // Poll for completion
      var pollInterval = setInterval(function () {
        fetch("/auth/google/status?user_email=" + encodeURIComponent(currentUser.email))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.authenticated) {
              gwsConnected = true;
              gwsGoogleEmail = data.google_email || "";
              updateGwsButton();
              clearInterval(pollInterval);
            }
          })
          .catch(function () {});
      }, 2000);
      // Stop polling after 5 minutes
      setTimeout(function () { clearInterval(pollInterval); }, 300000);
    }
  }

  // ===== Model Access Control =====
  var MODEL_LABELS = {
    "skin1004-Analysis": "Cella Analysis",
  };

  function showAdminButton() {
    if (currentUser && currentUser.role === "admin") {
      document.getElementById("admin-btn-wrap").style.display = "";
      var wb = document.getElementById("wiki-btn-wrap");
      if (wb) wb.style.display = "";
    }
  }

  function isAdmin() {
    return currentUser && currentUser.role === "admin";
  }

  // ===== Admin Drawer =====
  var _adminGroups = [];
  var _adminDepts = [];

  function openAdminDrawer() {
    document.getElementById("skin-admin-overlay").className = "open";
    document.getElementById("skin-admin-drawer").className = "open";
    // Hide write-actions for non-admin
    document.getElementById("btn-create-group").style.display = isAdmin() ? "" : "none";
    document.getElementById("btn-sync-ad").style.display = isAdmin() ? "" : "none";
    // Load all data in parallel
    Promise.all([
      fetch("/api/admin/ad/stats").then(function(r) { return r.json(); }),
      fetch("/api/admin/groups").then(function(r) { return r.json(); }),
      fetch("/api/admin/ad/departments").then(function(r) { return r.json(); }),
    ]).then(function(results) {
      renderAdminStats(results[0]);
      renderAdminGroups(results[1]);
      renderAdminDepts(results[2]);
    }).catch(function(e) { console.error("Admin load failed:", e); });
  }

  function closeAdminDrawer() {
    document.getElementById("skin-admin-overlay").className = "closed";
    document.getElementById("skin-admin-drawer").className = "closed";
  }

  // ===== Knowledge Wiki Drawer =====
  function openWikiDrawer() {
    document.getElementById("skin-wiki-overlay").className = "open";
    document.getElementById("skin-wiki-drawer").className = "open";
    _wikiSwitchTab("map");
    _wikiLoadStats();
    _wikiLoadMap();
  }
  function closeWikiDrawer() {
    document.getElementById("skin-wiki-overlay").className = "closed";
    document.getElementById("skin-wiki-drawer").className = "closed";
  }

  function _wikiSwitchTab(name) {
    document.querySelectorAll(".wiki-tab").forEach(function(t) {
      t.classList.toggle("active", t.getAttribute("data-tab") === name);
    });
    document.querySelectorAll(".wiki-tab-content").forEach(function(c) {
      c.classList.toggle("active", c.id === "wiki-tab-" + name);
    });
    if (name === "recent") _wikiLoadRecent();
    if (name === "graph") _wikiLoadGraph();
    if (name === "reports") _wikiLoadReports();
    if (name === "insights") _wikiLoadInsights();
  }
  document.querySelectorAll(".wiki-tab").forEach(function(tab) {
    tab.addEventListener("click", function() {
      _wikiSwitchTab(tab.getAttribute("data-tab"));
    });
  });

  function _escape(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"]/g, function(c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];
    });
  }

  function _wikiLoadStats() {
    fetch("/api/admin/wiki").then(function(r) { return r.json(); }).then(function(d) {
      var stats = d.counts_by_status || {};
      var domains = d.counts_by_domain || [];
      var total = 0;
      Object.values(stats).forEach(function(v) { total += v; });
      var bar = document.getElementById("wiki-stats-bar");
      if (!bar) return;
      var domainStr = domains.map(function(x) {
        return '<span class="wiki-chip">' + _escape(x.domain) + ' <b>' + x.cnt + '</b></span>';
      }).join(" ");
      bar.innerHTML =
        '<span class="wiki-stat-item">총 <b>' + total + '</b>건</span>' +
        '<span class="wiki-stat-item">active <b>' + (stats.active || 0) + '</b></span>' +
        '<span class="wiki-stat-item">pending <b>' + (stats.pending || 0) + '</b></span>' +
        '<span class="wiki-stat-item">archived <b>' + (stats.archived || 0) + '</b></span>' +
        '<span class="wiki-stat-sep">|</span>' + domainStr;
    }).catch(function(e) { console.error("wiki stats failed", e); });
  }

  function _wikiLoadMap() {
    var el = document.getElementById("wiki-tab-map");
    el.innerHTML = '<div class="wiki-loading">지도 불러오는 중...</div>';
    fetch("/api/admin/wiki/map").then(function(r) { return r.json(); }).then(function(d) {
      var html = '<div class="wiki-map-summary">도메인 <b>' + d.total_domains + '</b> / 엔티티 <b>' + d.total_entities + '</b> / 팩트 <b>' + d.total_facts + '</b></div>';
      var tree = d.tree || {};
      Object.keys(tree).sort().forEach(function(dom) {
        var entry = tree[dom];
        html += '<details class="wiki-domain"><summary><b>' + _escape(dom) + '</b> <span class="wiki-count">' + entry.entity_count + ' entities</span></summary>';
        var entities = entry.entities || {};
        var names = Object.keys(entities).sort();
        names.forEach(function(name) {
          var ent = entities[name];
          var periods = (ent.periods || []).slice(0, 6).map(_escape).join(", ");
          var more = ent.periods.length > 6 ? " +" + (ent.periods.length - 6) : "";
          html += '<div class="wiki-entity-row" data-entity="' + _escape(name) + '">'
            + '<div class="wiki-entity-name">' + _escape(name) + '</div>'
            + '<div class="wiki-entity-meta">'
            + '<span class="wiki-fact-count">' + ent.fact_count + '건</span>'
            + (periods ? '<span class="wiki-entity-periods">' + periods + more + '</span>' : '')
            + '</div>'
            + '</div>';
        });
        html += '</details>';
      });
      el.innerHTML = html || '<div class="wiki-empty">아직 추출된 팩트가 없습니다.</div>';
      el.querySelectorAll(".wiki-entity-row").forEach(function(row) {
        row.addEventListener("click", function() {
          _wikiShowEntity(row.getAttribute("data-entity"));
        });
      });
    }).catch(function(e) {
      el.innerHTML = '<div class="wiki-error">지도 로드 실패: ' + _escape(e) + '</div>';
    });
  }

  function _wikiLoadRecent() {
    var el = document.getElementById("wiki-tab-recent");
    el.innerHTML = '<div class="wiki-loading">최근 항목 불러오는 중...</div>';
    fetch("/api/admin/wiki").then(function(r) { return r.json(); }).then(function(d) {
      var items = d.recent || [];
      if (!items.length) { el.innerHTML = '<div class="wiki-empty">항목 없음</div>'; return; }
      var html = '';
      items.forEach(function(it) {
        html += '<div class="wiki-card" data-id="' + it.id + '">'
          + '<div class="wiki-card-head">'
          + '<span class="wiki-card-domain">' + _escape(it.domain) + '</span>'
          + '<span class="wiki-card-entity">' + _escape(it.entity) + '</span>'
          + (it.period ? '<span class="wiki-card-period">' + _escape(it.period) + '</span>' : '')
          + '</div>'
          + '<div class="wiki-card-summary">' + _escape(it.summary) + '</div>'
          + '<div class="wiki-card-foot">'
          + '<span class="wiki-card-conf">conf ' + it.confidence.toFixed(2) + '</span>'
          + '<span class="wiki-card-route">' + _escape(it.route || "") + '</span>'
          + '<button class="wiki-btn-up" data-id="' + it.id + '">👍</button>'
          + '<button class="wiki-btn-down" data-id="' + it.id + '">👎</button>'
          + '</div>'
          + '</div>';
      });
      el.innerHTML = html;
      el.querySelectorAll(".wiki-btn-up").forEach(function(b) {
        b.addEventListener("click", function() { _wikiVote(b.getAttribute("data-id"), "up"); });
      });
      el.querySelectorAll(".wiki-btn-down").forEach(function(b) {
        b.addEventListener("click", function() { _wikiVote(b.getAttribute("data-id"), "down"); });
      });
    }).catch(function(e) {
      el.innerHTML = '<div class="wiki-error">로드 실패</div>';
    });
  }

  function _wikiVote(id, vote) {
    fetch("/api/admin/wiki/" + id + "/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vote: vote }),
    }).then(function(r) { return r.json(); }).then(function(d) {
      _wikiLoadRecent();
      _wikiLoadStats();
    });
  }

  function _wikiLoadReports() {
    var el = document.getElementById("wiki-tab-reports");
    el.innerHTML = '<div class="wiki-loading">신고 내역 불러오는 중...</div>';
    Promise.all([
      fetch("/api/admin/wiki/reports").then(function(r) { return r.json(); }),
      fetch("/api/admin/wiki/insights").then(function(r) { return r.json(); }),
    ]).then(function(results) {
      var d = results[0];
      var ins = results[1];
      var contradictions = ins.contradictions || [];
      var needs = d.needs_review || [];
      var resolved = d.resolved || [];
      var html = '';

      // Contradictions section
      if (contradictions.length) {
        html += '<div class="wiki-reports-section">';
        html += '<h3 class="wiki-reports-title wiki-reports-conflict">🔥 모순 <span class="wiki-reports-count">' + contradictions.length + '</span></h3>';
        contradictions.forEach(function(c) {
          html += '<div class="wiki-card wiki-card-conflict">'
            + '<div class="wiki-card-head">'
            + '<span class="wiki-card-entity">' + _escape(c.entity || '') + '</span>'
            + (c.period ? '<span class="wiki-card-period">' + _escape(c.period) + '</span>' : '')
            + (c.metric ? '<span class="wiki-card-period">' + _escape(c.metric) + '</span>' : '')
            + '</div>'
            + '<div class="wiki-conflict-diff">'
            + '<div class="conflict-side"><b>#' + c.id + '</b> → <code>' + _escape(c.value_a || '') + '</code><br/><span class="wiki-card-summary">' + _escape(c.summary_a || '') + '</span></div>'
            + '<div class="conflict-vs">vs</div>'
            + '<div class="conflict-side"><b>#' + c.conflict_with_id + '</b> → <code>' + _escape(c.value_b || '') + '</code><br/><span class="wiki-card-summary">' + _escape(c.summary_b || '') + '</span></div>'
            + '</div>'
            + '<div class="wiki-card-foot">'
            + '<button class="wiki-btn-resolve" data-id="' + c.id + '">✅ 이쪽 맞음</button>'
            + '<button class="wiki-btn-resolve" data-id="' + c.conflict_with_id + '">✅ 저쪽 맞음</button>'
            + '<button class="wiki-btn-delete" data-id="' + c.id + '">🗑️ #' + c.id + ' 삭제</button>'
            + '<button class="wiki-btn-delete" data-id="' + c.conflict_with_id + '">🗑️ #' + c.conflict_with_id + ' 삭제</button>'
            + '</div>'
            + '</div>';
        });
        html += '</div>';
      }

      // Needs review section
      html += '<div class="wiki-reports-section">';
      html += '<h3 class="wiki-reports-title wiki-reports-needs">🔴 미해결 <span class="wiki-reports-count">' + needs.length + '</span></h3>';
      if (!needs.length) {
        html += '<div class="wiki-empty-small">검토가 필요한 팩트가 없습니다. 👍</div>';
      } else {
        needs.forEach(function(it) { html += _wikiReportCard(it, "needs"); });
      }
      html += '</div>';

      // Resolved section
      html += '<div class="wiki-reports-section">';
      html += '<h3 class="wiki-reports-title wiki-reports-resolved">✅ 해결됨 <span class="wiki-reports-count">' + resolved.length + '</span></h3>';
      if (!resolved.length) {
        html += '<div class="wiki-empty-small">아직 해결 처리된 항목이 없습니다.</div>';
      } else {
        resolved.forEach(function(it) { html += _wikiReportCard(it, "resolved"); });
      }
      html += '</div>';

      el.innerHTML = html;

      // Bind action buttons
      el.querySelectorAll(".wiki-btn-resolve").forEach(function(b) {
        b.addEventListener("click", function() {
          _wikiReportAction(b.getAttribute("data-id"), "resolve");
        });
      });
      el.querySelectorAll(".wiki-btn-restore").forEach(function(b) {
        b.addEventListener("click", function() {
          _wikiReportAction(b.getAttribute("data-id"), "restore");
        });
      });
      el.querySelectorAll(".wiki-btn-delete").forEach(function(b) {
        b.addEventListener("click", function() {
          if (!confirm("이 팩트를 영구 삭제하시겠습니까?")) return;
          _wikiDeleteFact(b.getAttribute("data-id"));
        });
      });
    }).catch(function(e) {
      el.innerHTML = '<div class="wiki-error">신고 로드 실패</div>';
    });
  }

  function _wikiReportCard(it, section) {
    var badgeCls = "wiki-review-badge-" + (it.review_status || "none");
    var badgeText = it.review_status === "needs_review" ? "미해결" :
                    (it.review_status === "resolved" ? "해결" :
                     (it.status === "archived" ? "자동 보관" : ""));
    var archivedTag = it.status === "archived" ? '<span class="wiki-card-archived">ARCHIVED</span>' : '';

    var actions = '';
    if (section === "needs") {
      actions += '<button class="wiki-btn-resolve" data-id="' + it.id + '">✅ 해결 완료</button>';
      actions += '<button class="wiki-btn-delete" data-id="' + it.id + '">🗑️ 영구 삭제</button>';
    } else {
      if (it.status === "archived") {
        actions += '<button class="wiki-btn-restore" data-id="' + it.id + '">↺ 복원</button>';
      }
      actions += '<button class="wiki-btn-delete" data-id="' + it.id + '">🗑️ 영구 삭제</button>';
    }

    return '<div class="wiki-card wiki-card-report" data-id="' + it.id + '">'
      + '<div class="wiki-card-head">'
      + '<span class="wiki-card-domain">' + _escape(it.domain) + '</span>'
      + '<span class="wiki-card-entity">' + _escape(it.entity) + '</span>'
      + (it.period ? '<span class="wiki-card-period">' + _escape(it.period) + '</span>' : '')
      + (badgeText ? '<span class="wiki-review-badge ' + badgeCls + '">' + badgeText + '</span>' : '')
      + archivedTag
      + '</div>'
      + '<div class="wiki-card-summary">' + _escape(it.summary) + '</div>'
      + '<div class="wiki-card-foot">'
      + '<span class="wiki-card-conf">conf ' + it.confidence.toFixed(2) + '</span>'
      + '<span class="wiki-card-votes">👍 ' + it.thumbs_up + ' · 👎 ' + it.thumbs_down + '</span>'
      + (it.validated_at ? '<span class="wiki-card-validated">최근 처리: ' + _escape(it.validated_at.slice(0,16).replace("T"," ")) + '</span>' : '')
      + '<span class="wiki-card-actions">' + actions + '</span>'
      + '</div>'
      + '</div>';
  }

  function _wikiReportAction(id, action) {
    fetch("/api/admin/wiki/" + id + "/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vote: action }),
    }).then(function(r) { return r.json(); }).then(function(d) {
      _wikiLoadReports();
      _wikiLoadStats();
    });
  }

  function _wikiDeleteFact(id) {
    fetch("/api/admin/wiki/" + id, { method: "DELETE" })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        _wikiLoadReports();
        _wikiLoadStats();
      });
  }

  function _wikiLoadGraph() {
    var el = document.getElementById("wiki-tab-graph");
    el.innerHTML = '<div class="wiki-loading">그래프 불러오는 중...</div>';
    fetch("/api/admin/wiki/graph?limit=200&full=true").then(function(r) { return r.json(); }).then(function(d) {
      var edges = d.edges || [];
      if (!edges.length) {
        el.innerHTML = '<div class="wiki-empty">관계 그래프가 아직 비어 있습니다.</div>';
        return;
      }
      var nodes = d.nodes || [];
      var html = '<div class="wiki-graph-summary">' + edges.length + ' edges / ' + nodes.length + ' nodes · '
        + (d.communities ? d.communities.length + ' communities' : '') + '</div>';
      html += '<div class="wiki-graph-toolbar">';
      html += '<button class="wiki-graph-toggle active" data-view="visual">🎨 시각</button>';
      html += '<button class="wiki-graph-toggle" data-view="table">📋 표</button>';
      html += '</div>';
      html += '<div class="wiki-graph-visual" id="wiki-graph-visual"></div>';
      html += '<div class="wiki-graph-tabular" id="wiki-graph-tabular" style="display:none">';
      html += '<table class="wiki-graph-table"><thead><tr><th>src</th><th>relation</th><th>dst</th><th>weight</th></tr></thead><tbody>';
      edges.forEach(function(e) {
        html += '<tr><td>' + _escape(e.src) + '</td><td><span class="wiki-rel">' + _escape(e.relation) + '</span></td><td>' + _escape(e.dst) + '</td><td>' + e.weight.toFixed(1) + '</td></tr>';
      });
      html += '</tbody></table>';
      html += '</div>';
      el.innerHTML = html;

      // Render vis.js
      _renderVisGraph(nodes, edges);

      // Toggle
      el.querySelectorAll(".wiki-graph-toggle").forEach(function(btn) {
        btn.addEventListener("click", function() {
          el.querySelectorAll(".wiki-graph-toggle").forEach(function(b) { b.classList.remove("active"); });
          btn.classList.add("active");
          var view = btn.getAttribute("data-view");
          document.getElementById("wiki-graph-visual").style.display = view === "visual" ? "block" : "none";
          document.getElementById("wiki-graph-tabular").style.display = view === "table" ? "block" : "none";
        });
      });
    }).catch(function(e) {
      el.innerHTML = '<div class="wiki-error">그래프 로드 실패</div>';
    });
  }

  function _renderVisGraph(nodes, edges) {
    if (typeof vis === "undefined") {
      document.getElementById("wiki-graph-visual").innerHTML = '<div class="wiki-empty">vis.js 로드 실패 (네트워크 확인)</div>';
      return;
    }
    // Color palette for communities
    var palette = ['#e89200','#3b82f6','#22c55e','#ef4444','#a855f7','#06b6d4','#f59e0b','#ec4899','#10b981','#6366f1','#84cc16','#f97316'];
    var visNodes = nodes.map(function(n) {
      var cid = n.community_id;
      var color = cid ? palette[(cid - 1) % palette.length] : '#666';
      return {
        id: n.id,
        label: n.id.length > 18 ? n.id.slice(0, 16) + '…' : n.id,
        title: n.id + (cid ? ' (community ' + cid + ')' : ''),
        color: { background: color, border: color },
        font: { color: '#fff', size: 11 },
        shape: 'dot',
        size: 10,
      };
    });
    var visEdges = edges.map(function(e) {
      return {
        from: e.src, to: e.dst,
        label: e.relation,
        title: e.relation + ' · weight ' + e.weight.toFixed(1),
        width: Math.min(5, 0.5 + e.weight * 0.3),
        color: { color: 'rgba(255,255,255,0.15)', highlight: '#e89200' },
        font: { size: 9, color: 'rgba(255,255,255,0.4)', strokeWidth: 0 },
        smooth: { type: 'continuous' },
      };
    });
    var container = document.getElementById("wiki-graph-visual");
    container.innerHTML = '';
    var network = new vis.Network(container, { nodes: new vis.DataSet(visNodes), edges: new vis.DataSet(visEdges) }, {
      physics: { barnesHut: { gravitationalConstant: -8000, springLength: 120 }, stabilization: { iterations: 100 } },
      interaction: { hover: true, tooltipDelay: 200 },
      nodes: { borderWidth: 2 },
    });
    network.on("click", function(params) {
      if (params.nodes && params.nodes.length) {
        _wikiShowEntity(params.nodes[0]);
      }
    });
  }

  function _wikiLoadInsights() {
    var el = document.getElementById("wiki-tab-insights");
    el.innerHTML = '<div class="wiki-loading">인사이트 계산 중...</div>';
    fetch("/api/admin/wiki/insights").then(function(r) { return r.json(); }).then(function(d) {
      var html = '';

      // God nodes
      html += '<div class="insight-section"><h3>👑 허브 엔티티 (가장 많이 연결됨)</h3>';
      if (d.god_nodes && d.god_nodes.length) {
        html += '<ul class="insight-list">';
        d.god_nodes.forEach(function(g) {
          html += '<li><a class="insight-entity" data-entity="' + _escape(g.entity) + '">' + _escape(g.entity)
            + '</a> <span class="insight-meta">degree ' + g.degree + ' · weight ' + g.weight_sum.toFixed(1) + '</span></li>';
        });
        html += '</ul>';
      } else { html += '<div class="wiki-empty-small">데이터 없음</div>'; }
      html += '</div>';

      // Communities
      html += '<div class="insight-section"><h3>🧩 커뮤니티</h3>';
      if (d.communities && d.communities.length) {
        html += '<div class="insight-communities">';
        d.communities.forEach(function(c) {
          var top = typeof c.top_entities === "string" ? JSON.parse(c.top_entities) : c.top_entities;
          html += '<div class="insight-community"><b>#' + c.id + ' ' + _escape(c.label) + '</b>'
            + ' <span class="insight-meta">size ' + c.size + ' · density ' + (c.density ? c.density.toFixed(2) : '-') + '</span>';
          if (top && top.length) {
            html += '<div class="insight-community-members">'
              + top.map(function(x) { return '<span class="insight-tag">' + _escape(x) + '</span>'; }).join(' ') + '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      } else { html += '<div class="wiki-empty-small">커뮤니티 없음 — `python scripts/build_wiki_communities.py` 필요</div>'; }
      html += '</div>';

      // Surprising
      html += '<div class="insight-section"><h3>✨ 횡단 연결 (다른 도메인)</h3>';
      if (d.surprising && d.surprising.length) {
        html += '<ul class="insight-list">';
        d.surprising.forEach(function(s) {
          html += '<li>'
            + '<a class="insight-entity" data-entity="' + _escape(s.src_entity) + '">' + _escape(s.src_entity) + '</a>'
            + ' <span class="insight-domain">' + _escape(s.src_domain) + '</span>'
            + ' <span class="insight-arrow">→</span>'
            + ' <a class="insight-entity" data-entity="' + _escape(s.dst_entity) + '">' + _escape(s.dst_entity) + '</a>'
            + ' <span class="insight-domain">' + _escape(s.dst_domain) + '</span>'
            + ' <span class="insight-meta">w ' + s.weight.toFixed(1) + '</span>'
            + '</li>';
        });
        html += '</ul>';
      } else { html += '<div class="wiki-empty-small">횡단 연결 없음</div>'; }
      html += '</div>';

      // Orphans
      html += '<div class="insight-section"><h3>🪹 고아 엔티티 (팩트 1개, 연결 0)</h3>';
      if (d.orphans && d.orphans.length) {
        html += '<ul class="insight-list">';
        d.orphans.slice(0, 15).forEach(function(o) {
          html += '<li><a class="insight-entity" data-entity="' + _escape(o.entity) + '">' + _escape(o.entity) + '</a>'
            + ' <span class="insight-domain">' + _escape(o.domain) + '</span>'
            + '<div class="insight-meta">' + _escape((o.sample_summary||'').slice(0,120)) + '</div></li>';
        });
        html += '</ul>';
      } else { html += '<div class="wiki-empty-small">모두 연결됨</div>'; }
      html += '</div>';

      // Stale
      html += '<div class="insight-section"><h3>🕒 오래된 팩트 (14일+ 경과, BQ 데이터)</h3>';
      if (d.stale && d.stale.length) {
        html += '<ul class="insight-list">';
        d.stale.forEach(function(s) {
          html += '<li><b>' + _escape(s.entity) + '</b>'
            + ' <span class="insight-domain">' + _escape(s.period||'') + '</span>'
            + '<div class="insight-meta">' + _escape((s.summary||'').slice(0,140)) + '</div></li>';
        });
        html += '</ul>';
      } else { html += '<div class="wiki-empty-small">오래된 팩트 없음</div>'; }
      html += '</div>';

      // Suggested queries
      html += '<div class="insight-section"><h3>💡 제안 질문</h3>';
      if (d.suggested_queries && d.suggested_queries.length) {
        html += '<ul class="insight-list insight-queries">';
        d.suggested_queries.forEach(function(q) {
          html += '<li>' + _escape(q) + '</li>';
        });
        html += '</ul>';
      } else { html += '<div class="wiki-empty-small">제안 없음</div>'; }
      html += '</div>';

      el.innerHTML = html;
      el.querySelectorAll(".insight-entity").forEach(function(a) {
        a.addEventListener("click", function() {
          _wikiShowEntity(a.getAttribute("data-entity"));
        });
      });
    }).catch(function(e) {
      el.innerHTML = '<div class="wiki-error">인사이트 로드 실패</div>';
    });
  }

  function _wikiShowEntity(entity) {
    var modal = document.getElementById("wiki-entity-modal");
    var content = document.getElementById("wiki-entity-modal-content");
    content.innerHTML = '<div class="wiki-loading">로딩 중...</div>';
    modal.className = "open";
    fetch("/api/admin/wiki/entity/" + encodeURIComponent(entity))
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var md = (d.page && d.page.markdown) || '_페이지가 아직 컴파일되지 않았습니다._';
        // Simple markdown to HTML (headings + list + bold + italic)
        var html = _mdRender(md);
        content.innerHTML = html;
      })
      .catch(function(e) {
        content.innerHTML = '<div class="wiki-error">엔티티 로드 실패</div>';
      });
  }
  function _mdRender(md) {
    var html = _escape(md);
    html = html.replace(/^### (.*)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.*)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.*)$/gm, '<h2>$1</h2>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    html = html.replace(/_(.+?)_/g, '<i>$1</i>');
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    html = html.replace(/^- (.*)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, function(m) { return '<ul>' + m + '</ul>'; });
    html = html.replace(/^---$/gm, '<hr>');
    html = html.replace(/\n\n/g, '</p><p>');
    return '<div class="entity-md"><p>' + html + '</p></div>';
  }

  // Tab switching
  document.querySelectorAll(".admin-tab").forEach(function(tab) {
    tab.addEventListener("click", function() {
      document.querySelectorAll(".admin-tab").forEach(function(t) { t.classList.remove("active"); });
      document.querySelectorAll(".admin-tab-content").forEach(function(c) { c.classList.remove("active"); });
      tab.classList.add("active");
      document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
      if (tab.dataset.tab === "users") loadAdminADUsers();
      if (tab.dataset.tab === "growth") loadGrowthReport();
    });
  });

  // ── Growth Report ──
  function loadGrowthReport() {
    document.getElementById("admin-growth-body").innerHTML = "<p style='padding:12px;color:var(--text-secondary)'>로딩 중…</p>";
    fetch("/api/admin/growth-report")
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { renderGrowthReport(d); })
      .catch(function() {
        document.getElementById("admin-growth-body").innerHTML = "<p style='padding:12px;color:var(--text-secondary)'>데이터 없음</p>";
      });
  }

  function renderGrowthReport(d) {
    var el = document.getElementById("admin-growth-body");
    if (!d) { el.innerHTML = "<p style='padding:12px'>데이터 없음</p>"; return; }

    var qt = d.quality_trend || {};
    var accNow = qt.accuracy_avg != null ? (qt.accuracy_avg * 100).toFixed(1) + "%" : "—";
    var accDelta = qt.accuracy_delta != null ? (qt.accuracy_delta >= 0 ? "▲" : "▼") + Math.abs((qt.accuracy_delta * 100).toFixed(1)) + "%" : "";
    var spdNow = qt.speed_avg_ms != null ? (qt.speed_avg_ms / 1000).toFixed(1) + "s" : "—";
    var spdDelta = qt.speed_delta_ms != null ? (qt.speed_delta_ms <= 0 ? "▲" : "▼") + Math.abs((qt.speed_delta_ms / 1000).toFixed(1)) + "s" : "";

    var html = '<div style="padding:12px">';
    // 성장 요약 카드
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">';
    html += _growthCard("SQL 캐시 히트", d.sql_cache_hits || 0, "이번 주 캐시 응답 수", "#27ae60");
    html += _growthCard("SQL 패턴 학습", d.sql_cache_new || 0, "새로 저장된 SQL 패턴", "#2980b9");
    html += _growthCard("👍 스킬 학습", d.skill_memory_pos || 0, "긍정 피드백 패턴", "#8e44ad");
    html += _growthCard("👎 오류 학습", d.skill_memory_neg || 0, "부정 피드백 패턴", "#c0392b");
    html += _growthCard("지식 갭 감지", d.knowledge_gaps || 0, "CS 누락 질문 수", "#e67e22");
    html += _growthCard("Wiki 노드", d.wiki_node_count || 0, "지식 그래프 총 노드", "#16a085");
    html += '</div>';

    // 품질 추이
    html += '<div style="background:var(--sidebar-bg);border-radius:8px;padding:10px 12px;margin-bottom:12px">';
    html += '<div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:8px">품질 추이 (이번 주)</div>';
    html += '<div style="display:flex;gap:24px">';
    html += '<div><div style="font-size:18px;font-weight:700">' + accNow + '</div><div style="font-size:11px;color:var(--text-secondary)">정확도 ' + (accDelta ? '<span style="color:' + (qt.accuracy_delta >= 0 ? "#27ae60" : "#c0392b") + '">' + accDelta + '</span>' : "") + '</div></div>';
    html += '<div><div style="font-size:18px;font-weight:700">' + spdNow + '</div><div style="font-size:11px;color:var(--text-secondary)">응답속도 ' + (spdDelta ? '<span style="color:' + (qt.speed_delta_ms <= 0 ? "#27ae60" : "#c0392b") + '">' + spdDelta + '</span>' : "") + '</div></div>';
    html += '</div></div>';

    // 기간
    if (d.week_start) {
      html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:12px">' + d.week_start + ' ~ ' + (d.week_end || "") + '</div>';
    }

    // CS 지식 갭 로드
    html += '<div id="growth-gaps-section"><button onclick="loadKnowledgeGaps()" style="font-size:12px;padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-secondary);cursor:pointer">CS 지식 갭 보기</button></div>';
    html += '</div>';
    el.innerHTML = html;

    document.getElementById("btn-refresh-growth").onclick = function() {
      fetch("/api/admin/growth-report/refresh", {method:"POST"})
        .then(function(r) { return r.json(); })
        .then(function(d) { renderGrowthReport(d); });
    };
  }

  function _growthCard(label, value, sub, color) {
    return '<div style="background:var(--sidebar-bg);border-radius:8px;padding:10px 12px;border-left:3px solid ' + color + '">' +
      '<div style="font-size:20px;font-weight:700;color:' + color + '">' + value + '</div>' +
      '<div style="font-size:12px;font-weight:600;margin-top:2px">' + label + '</div>' +
      '<div style="font-size:11px;color:var(--text-secondary)">' + sub + '</div>' +
      '</div>';
  }

  function loadKnowledgeGaps() {
    document.getElementById("growth-gaps-section").innerHTML = "<p style='font-size:12px;color:var(--text-secondary)'>로딩 중…</p>";
    fetch("/api/admin/knowledge-gaps")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var html = '<div style="margin-top:4px"><div style="font-size:12px;font-weight:600;margin-bottom:6px">CS 지식 갭 — 최근 30일 (' + (d.unreviewed || 0) + '건 미검토)</div>';
        if (!d.gaps || d.gaps.length === 0) {
          html += '<p style="font-size:12px;color:var(--text-secondary)">갭 없음</p>';
        } else {
          d.gaps.forEach(function(g) {
            var date = (g.created_at || "").slice(0, 10);
            html += '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border)">' +
              '<span style="font-size:11px;color:var(--text-secondary);flex-shrink:0">' + date + '</span>' +
              '<span style="font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _escape(g.question) + '</span>' +
              (g.reviewed ? '<span style="font-size:10px;color:#27ae60">✓</span>' :
                '<button onclick="markGapReviewed(' + g.id + ', this)" style="font-size:10px;padding:2px 6px;border-radius:4px;border:1px solid var(--border);background:transparent;cursor:pointer">검토</button>') +
              '</div>';
          });
        }
        html += '</div>';
        document.getElementById("growth-gaps-section").innerHTML = html;
      }).catch(function() {
        document.getElementById("growth-gaps-section").innerHTML = "<p style='font-size:12px;color:var(--text-secondary)'>로드 실패</p>";
      });
  }

  function markGapReviewed(id, btn) {
    fetch("/api/admin/knowledge-gaps/" + id + "/review", {method:"PATCH"})
      .then(function(r) { if (r.ok) { btn.parentElement.querySelector("span:last-child, button:last-child").outerHTML = '<span style="font-size:10px;color:#27ae60">✓</span>'; } });
  }

  // Stats
  function loadAdminStats() {
    fetch("/api/admin/ad/stats").then(function(r) { return r.json(); }).then(renderAdminStats).catch(function() {});
  }
  function renderAdminStats(s) {
    document.getElementById("admin-stats-bar").innerHTML =
      '<div class="admin-stat"><div class="admin-stat-num">' + s.total_ad_users + '</div><div class="admin-stat-label">AD 사용자</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.assigned_users + '</div><div class="admin-stat-label">배정됨</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.unassigned_users + '</div><div class="admin-stat-label">미배정</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.fi_allowed_users + '</div><div class="admin-stat-label">손익 허용</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.total_groups + '</div><div class="admin-stat-label">그룹</div></div>';
  }

  // Departments — hierarchical tree
  function loadAdminDepts() {
    fetch("/api/admin/ad/departments").then(function(r) { return r.json(); }).then(renderAdminDepts).catch(function() {});
  }
  function renderAdminDepts(depts) {
    _adminDepts = depts;
    var sel = document.getElementById("admin-dept-filter");
    sel.innerHTML = '<option value="">전체 부서</option>';

    var tree = {};
    depts.forEach(function(d) {
      var parts = d.department.split(" > ");
      var meaningful = parts.slice(2);
      if (!meaningful.length) meaningful = [parts[parts.length - 1]];
      for (var i = 0; i < meaningful.length; i++) {
        var key = meaningful.slice(0, i + 1).join(" > ");
        if (!tree[key]) tree[key] = {count: 0, depth: i, label: meaningful[i]};
        tree[key].count += d.cnt;
      }
    });

    var optHtml = "";
    Object.keys(tree).sort().forEach(function(key) {
      var node = tree[key];
      var indent = "";
      for (var i = 0; i < node.depth; i++) indent += "\u00A0\u00A0\u00A0";
      var prefix = node.depth > 0 ? "└ " : "";
      optHtml += '<option value="' + escapeHtml(node.label) + '">' +
        indent + prefix + escapeHtml(node.label) + ' (' + node.count + ')</option>';
    });
    sel.innerHTML += optHtml;
  }

  // Groups
  function loadAdminGroups() {
    fetch("/api/admin/groups").then(function(r) { return r.json(); }).then(renderAdminGroups).catch(function(e) { console.error("Failed to load groups:", e); });
  }
  function renderAdminGroups(groups) {
    _adminGroups = groups;
    var container = document.getElementById("admin-group-list");
    // Update group filter in users tab
    var gf = document.getElementById("admin-group-filter");
    var gfHtml = '<option value="">전체 그룹</option><option value="unassigned">미배정</option><option value="fi_allowed">손익 허용</option>';
    groups.forEach(function(g) {
      gfHtml += '<option value="' + g.id + '">' + g.name + '</option>';
    });
    gf.innerHTML = gfHtml;

    if (!groups.length) {
      container.innerHTML = '<div style="text-align:center;padding:40px 0;color:var(--text-muted)">그룹이 없습니다. 새 그룹을 만들어보세요.</div>';
      return;
    }
    var html = "";
    groups.forEach(function(g) {
      html += '<div class="admin-group-card" data-group-id="' + g.id + '">';
      html += '<div class="admin-group-header">';
      html += '<div class="admin-group-name">' + escapeHtml(g.name) + '</div>';
      html += '<div class="admin-group-meta">';
      html += '<span class="admin-group-count">' + g.member_count + '명</span>';
      html += '<div class="admin-group-actions">';
      html += '<button onclick="adminViewGroup(' + g.id + ', \'' + escapeHtml(g.name) + '\')">멤버</button>';
      if (isAdmin()) {
        html += '<button onclick="adminAssignDept(' + g.id + ', \'' + escapeHtml(g.name) + '\')">부서 배정</button>';
        html += '<button onclick="adminEditGroup(' + g.id + ')">편집</button>';
        html += '<button class="danger" onclick="adminDeleteGroup(' + g.id + ', \'' + escapeHtml(g.name) + '\')">삭제</button>';
      }
      html += '</div></div></div>';
      if (g.brand_filter) html += '<div class="admin-group-brand-filter"><span class="brand-filter-badge">Brand: ' + escapeHtml(g.brand_filter) + '</span></div>';
      if (g.description) html += '<div class="admin-group-desc">' + escapeHtml(g.description) + '</div>';
      html += '</div>';
    });
    container.innerHTML = html;
  }

  // Create group
  document.getElementById("btn-create-group").addEventListener("click", function() {
    showAdminModal("새 그룹 만들기", "", "", "", function(name, desc, brandFilter) {
      fetch("/api/admin/groups", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: name, description: desc, brand_filter: brandFilter})
      }).then(function(r) {
        if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail); });
        return r.json();
      }).then(function() {
        loadAdminGroups();
        loadAdminStats();
      }).catch(function(e) { alert("그룹 생성 실패: " + e.message); });
    });
  });

  // AD sync
  document.getElementById("btn-sync-ad").addEventListener("click", function() {
    if (!confirm("AD 사용자 목록을 동기화하시겠습니까?")) return;
    var btn = this;
    btn.textContent = "동기화 중...";
    btn.disabled = true;
    fetch("/api/admin/ad/sync", {method: "POST"})
      .then(function(r) { return r.json(); })
      .then(function(res) {
        btn.textContent = "AD 동기화";
        btn.disabled = false;
        if (res.ok) {
          alert("AD 동기화 완료!\n" + res.output.split("\n").slice(-5).join("\n"));
          loadAdminStats();
          loadAdminADUsers();
        } else {
          alert("동기화 실패: " + (res.error || "Unknown"));
        }
      }).catch(function(e) {
        btn.textContent = "AD 동기화";
        btn.disabled = false;
        alert("동기화 오류: " + e.message);
      });
  });

  // AD users list
  function loadAdminADUsers() {
    var dept = document.getElementById("admin-dept-filter").value;
    var groupFilter = document.getElementById("admin-group-filter").value;
    var search = document.getElementById("admin-search").value;

    var params = new URLSearchParams();
    if (dept) params.set("dept", dept);
    if (search) params.set("search", search);
    if (groupFilter === "unassigned") params.set("unassigned", "true");
    else if (groupFilter === "fi_allowed") params.set("fi_only", "true");
    else if (groupFilter) params.set("group_id", groupFilter);

    fetch("/api/admin/ad/users?" + params.toString())
      .then(function(r) { return r.json(); })
      .then(function(users) {
        var container = document.getElementById("admin-user-list");
        if (!users.length) {
          container.innerHTML = '<div style="text-align:center;padding:40px 0;color:var(--text-muted)">검색 결과가 없습니다.</div>';
          return;
        }
        var html = "";
        users.forEach(function(u) {
          var initial = (u.display_name || "U").charAt(0).toUpperCase();
          var deptShort = u.department ? u.department.split(" > ").slice(-1)[0] : "";
          var groupBadge = u.group_names
            ? '<span class="admin-ad-group-badge">' + escapeHtml(u.group_names) + '</span>'
            : '<span class="admin-ad-group-badge none">미배정</span>';
          var signupBadge = u.user_id
            ? ''
            : ' <span class="admin-ad-group-badge none">미가입</span>';

          html += '<div class="admin-ad-user">';
          html += '<div class="admin-ad-avatar">' + initial + '</div>';
          html += '<div class="admin-ad-info">';
          html += '<div class="admin-ad-name">' + escapeHtml(u.display_name) + ' <small style="color:var(--text-muted)">(' + escapeHtml(u.username) + ')</small>' + signupBadge + '</div>';
          html += '<div class="admin-ad-email">' + escapeHtml(u.email || "N/A") + '</div>';
          html += '<div class="admin-ad-dept">' + escapeHtml(deptShort) + '</div>';
          html += '</div>';
          html += groupBadge;
          if (isAdmin()) {
            html += '<label style="display:flex;align-items:center;gap:4px;font-size:12px;white-space:nowrap;cursor:pointer">';
            html += '<input type="checkbox" class="admin-fi-toggle" data-ad-user-id="' + u.id + '"' + (u.can_view_fi ? ' checked' : '') + '> 손익</label>';
            html += '<button class="admin-ad-assign" onclick="adminAssignUser(' + u.id + ', \'' + escapeHtml(u.display_name) + '\')">배정</button>';
          }
          html += '</div>';
        });
        container.innerHTML = html;
        container.querySelectorAll(".admin-fi-toggle").forEach(function(checkbox) {
          checkbox.addEventListener("change", function() {
            var requested = checkbox.checked;
            checkbox.disabled = true;
            fetch("/api/admin/ad/users/" + checkbox.dataset.adUserId + "/fi", {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ can_view_fi: requested })
            }).then(function(r) {
              if (!r.ok) throw new Error("권한 변경에 실패했습니다.");
              return r.json();
            }).then(function() {
              checkbox.disabled = false;
              loadAdminStats();
              if (document.getElementById("admin-group-filter").value === "fi_allowed" && !requested) {
                loadAdminADUsers();
              }
            }).catch(function(e) {
              checkbox.checked = !requested;
              checkbox.disabled = false;
              alert(e.message || "권한 변경에 실패했습니다.");
            });
          });
        });
      }).catch(function(e) { console.error("Failed to load AD users:", e); });
  }

  // Filters
  document.getElementById("admin-dept-filter").addEventListener("change", loadAdminADUsers);
  document.getElementById("admin-group-filter").addEventListener("change", loadAdminADUsers);
  var _searchTimer = null;
  document.getElementById("admin-search").addEventListener("input", function() {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(loadAdminADUsers, 300);
  });

  // Assign user to group
  window.adminAssignUser = function(userId, userName) {
    if (!_adminGroups.length) { alert("먼저 그룹을 만들어주세요."); return; }
    var options = _adminGroups.map(function(g) { return g.name; });
    var choice = prompt("'" + userName + "' 을(를) 배정할 그룹을 선택하세요:\n\n" +
      _adminGroups.map(function(g, i) { return (i+1) + ". " + g.name; }).join("\n") +
      "\n\n번호를 입력하세요:");
    if (!choice) return;
    var idx = parseInt(choice) - 1;
    if (isNaN(idx) || idx < 0 || idx >= _adminGroups.length) { alert("잘못된 번호입니다."); return; }
    var groupId = _adminGroups[idx].id;

    fetch("/api/admin/groups/" + groupId + "/members", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ad_user_ids: [userId]})
    }).then(function(r) { return r.json(); })
    .then(function() { loadAdminADUsers(); loadAdminGroups(); loadAdminStats(); })
    .catch(function(e) { alert("배정 실패: " + e.message); });
  };

  // Assign department to group (bulk)
  window.adminAssignDept = function(groupId, groupName) {
    var overlay = document.createElement("div");
    overlay.className = "admin-modal-overlay";

    // Build dept tree from cached _adminDepts — group into top-level categories
    var topDepts = {};
    _adminDepts.forEach(function(d) {
      var parts = d.department.split(" > ");
      // Top-level = depth 2 (e.g. "Craver_Accounts > Users > Brand")
      var topKey = parts.slice(0, 3).join(" > ");
      var topLabel = parts[2] || parts[parts.length - 1];
      if (!topDepts[topKey]) topDepts[topKey] = { label: topLabel, fullPath: topKey, children: [], totalCount: 0 };
      topDepts[topKey].children.push(d);
      topDepts[topKey].totalCount += d.cnt;
    });

    var topOptions = '<option value="">-- 상위 부서 선택 --</option>';
    Object.keys(topDepts).sort().forEach(function(key) {
      var t = topDepts[key];
      topOptions += '<option value="' + escapeHtml(key) + '">' + escapeHtml(t.label) + ' (' + t.totalCount + '명)</option>';
    });

    overlay.innerHTML =
      '<div class="admin-modal admin-modal-wide">' +
      '<h3>\'' + escapeHtml(groupName) + '\' 부서 일괄 배정</h3>' +
      '<select id="modal-top-dept" style="width:100%;padding:8px;margin:8px 0;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-secondary);color:var(--text-primary);font-size:14px">' + topOptions + '</select>' +
      '<div id="sub-dept-list" class="dept-user-list" style="display:none">' +
      '<div class="dept-user-header"><span id="sub-dept-count"></span>' +
      '<label style="font-size:12px;cursor:pointer"><input type="checkbox" id="sub-dept-check-all" checked> 전체 선택</label></div>' +
      '<div id="sub-dept-items" class="dept-user-items"></div></div>' +
      '<div id="dept-user-list" class="dept-user-list" style="display:none">' +
      '<div class="dept-user-header"><span id="dept-user-count"></span>' +
      '<label style="font-size:12px;cursor:pointer"><input type="checkbox" id="dept-check-all" checked> 전체 선택</label></div>' +
      '<div id="dept-user-items" class="dept-user-items"></div></div>' +
      '<div id="assign-mode-wrap" style="display:none;margin:8px 0;padding:8px 10px;border-radius:6px;background:rgba(229,62,62,0.08);border:1px solid rgba(229,62,62,0.2)">' +
      '<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px">' +
      '<input type="checkbox" id="assign-replace-mode"> ' +
      '<span><b>교체 모드</b> — 기존 멤버 전부 제거 후 선택한 사용자만 배정</span></label></div>' +
      '<div class="admin-modal-actions">' +
      '<button class="admin-btn-secondary" id="modal-cancel">취소</button>' +
      '<button class="admin-btn-primary" id="modal-load-users" style="display:none">사용자 불러오기</button>' +
      '<button class="admin-btn-primary" id="modal-ok" style="display:none">배정</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector("#modal-cancel").addEventListener("click", function() { overlay.remove(); });
    overlay.addEventListener("click", function(e) { if (e.target === overlay) overlay.remove(); });

    var topSelect = overlay.querySelector("#modal-top-dept");
    var subDeptDiv = overlay.querySelector("#sub-dept-list");
    var subDeptItems = overlay.querySelector("#sub-dept-items");
    var subDeptCount = overlay.querySelector("#sub-dept-count");
    var subCheckAll = overlay.querySelector("#sub-dept-check-all");
    var btnLoad = overlay.querySelector("#modal-load-users");
    var btnOk = overlay.querySelector("#modal-ok");
    var assignModeWrap = overlay.querySelector("#assign-mode-wrap");
    var replaceCheckbox = overlay.querySelector("#assign-replace-mode");
    var userListDiv = overlay.querySelector("#dept-user-list");
    var userItemsDiv = overlay.querySelector("#dept-user-items");
    var countSpan = overlay.querySelector("#dept-user-count");
    var checkAll = overlay.querySelector("#dept-check-all");
    var _deptUsers = [];

    // Step 1: Top dept selected → show sub-dept checkboxes
    topSelect.addEventListener("change", function() {
      var topKey = this.value;
      btnLoad.style.display = "none";
      btnOk.style.display = "none";
      userListDiv.style.display = "none";
      _deptUsers = [];

      if (!topKey || !topDepts[topKey]) { subDeptDiv.style.display = "none"; return; }

      var children = topDepts[topKey].children;
      var html = "";
      children.sort(function(a, b) { return a.department.localeCompare(b.department); });
      children.forEach(function(d) {
        var parts = d.department.split(" > ");
        var label = parts.slice(3).join(" > ") || parts[parts.length - 1];
        var indent = Math.max(0, parts.length - 4);
        var indentStr = "";
        for (var i = 0; i < indent; i++) indentStr += "\u00A0\u00A0\u00A0";
        var prefix = indent > 0 ? "└ " : "";
        html += '<label class="dept-user-item">' +
          '<input type="checkbox" checked data-dept="' + escapeHtml(d.department) + '"> ' +
          '<span class="dept-user-name">' + indentStr + prefix + escapeHtml(label) + '</span>' +
          '<span class="dept-user-dept">' + d.cnt + '명</span>' +
          '</label>';
      });
      subDeptItems.innerHTML = html;
      subDeptCount.textContent = children.length + "개 부서 선택됨";
      subCheckAll.checked = true;
      subDeptDiv.style.display = "";
      btnLoad.style.display = "";
    });

    // Sub-dept checkbox change (outside dropdown handler to avoid duplicate listeners)
    subDeptItems.addEventListener("change", function() {
      var boxes = subDeptItems.querySelectorAll('input[type="checkbox"]');
      var checked = subDeptItems.querySelectorAll('input[type="checkbox"]:checked').length;
      subDeptCount.textContent = checked + "/" + boxes.length + "개 부서 선택됨";
      subCheckAll.checked = (checked === boxes.length);
    });

    // Sub-dept check all toggle
    subCheckAll.addEventListener("change", function() {
      var boxes = subDeptItems.querySelectorAll('input[type="checkbox"]');
      var val = this.checked;
      boxes.forEach(function(cb) { cb.checked = val; });
      var total = boxes.length;
      subDeptCount.textContent = (val ? total : 0) + "/" + total + "개 부서 선택됨";
    });

    // Step 2: Load users from checked departments
    btnLoad.addEventListener("click", function() {
      var checkedDepts = [];
      subDeptItems.querySelectorAll('input[type="checkbox"]:checked').forEach(function(cb) {
        checkedDepts.push(cb.getAttribute("data-dept"));
      });
      if (!checkedDepts.length) { alert("부서를 선택하세요."); return; }

      btnLoad.textContent = "불러오는 중...";
      btnLoad.disabled = true;

      // Fetch users for the top dept (includes all sub), then filter client-side
      var topKey = topSelect.value;
      fetch("/api/admin/ad/users?dept=" + encodeURIComponent(topKey))
        .then(function(r) { return r.json(); })
        .then(function(users) {
          // Filter to only checked departments
          var deptSet = {};
          checkedDepts.forEach(function(d) { deptSet[d] = true; });
          users = users.filter(function(u) { return deptSet[u.department]; });

          _deptUsers = users;
          btnLoad.textContent = "사용자 불러오기";
          btnLoad.disabled = false;

          if (!users.length) {
            userItemsDiv.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted)">선택된 부서에 사용자가 없습니다.</div>';
            userListDiv.style.display = "";
            countSpan.textContent = "0명";
            btnOk.style.display = "none";
            return;
          }

          var html = "";
          users.forEach(function(u) {
            var deptShort = u.department ? u.department.split(" > ").slice(-1)[0] : "";
            html += '<label class="dept-user-item">' +
              '<input type="checkbox" checked data-uid="' + u.id + '"> ' +
              '<span class="dept-user-name">' + escapeHtml(u.display_name) + '</span>' +
              '<span class="dept-user-dept">' + escapeHtml(deptShort) + '</span>' +
              (u.group_names ? '<span class="dept-user-groups">' + escapeHtml(u.group_names) + '</span>' : '') +
              '</label>';
          });
          userItemsDiv.innerHTML = html;
          countSpan.textContent = users.length + "명 선택됨";
          checkAll.checked = true;
          userListDiv.style.display = "";
          btnOk.style.display = "";
          assignModeWrap.style.display = "";
        }).catch(function(e) {
          btnLoad.textContent = "사용자 불러오기";
          btnLoad.disabled = false;
          alert("사용자 목록 불러오기 실패: " + e.message);
        });
    });

    // User checkbox change (outside load handler to avoid duplicate listeners)
    userItemsDiv.addEventListener("change", function() {
      var checked = userItemsDiv.querySelectorAll('input[type="checkbox"]:checked').length;
      var total = userItemsDiv.querySelectorAll('input[type="checkbox"]').length;
      countSpan.textContent = checked + "/" + total + "명 선택됨";
      checkAll.checked = (checked === total);
    });

    // User check all toggle
    checkAll.addEventListener("change", function() {
      var boxes = userItemsDiv.querySelectorAll('input[type="checkbox"]');
      var val = this.checked;
      boxes.forEach(function(cb) { cb.checked = val; });
      countSpan.textContent = (val ? _deptUsers.length : 0) + "/" + _deptUsers.length + "명 선택됨";
    });

    // Step 3: Submit checked users
    btnOk.addEventListener("click", async function() {
      var checked = userItemsDiv.querySelectorAll('input[type="checkbox"]:checked');
      var ids = [];
      checked.forEach(function(cb) { ids.push(parseInt(cb.getAttribute("data-uid"))); });
      if (!ids.length) { alert("배정할 사용자를 선택하세요."); return; }

      var isReplace = replaceCheckbox && replaceCheckbox.checked;
      if (isReplace && !confirm("교체 모드: 기존 멤버를 모두 제거하고 " + ids.length + "명만 배정합니다.\n계속하시겠습니까?")) return;

      btnOk.textContent = isReplace ? "교체 중..." : "배정 중...";
      btnOk.disabled = true;

      try {
        // Replace mode: remove all existing members first
        if (isReplace) {
          var existRes = await fetch("/api/admin/groups/" + groupId + "/members");
          var existMembers = await existRes.json();
          if (existMembers.length) {
            var removeRes = await fetch("/api/admin/groups/" + groupId + "/members", {
              method: "DELETE",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({ad_user_ids: existMembers.map(function(m) { return m.id; })})
            });
            if (!removeRes.ok) { var err = await removeRes.json(); throw new Error(err.detail); }
          }
        }

        // Add selected users
        var addRes = await fetch("/api/admin/groups/" + groupId + "/members", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ad_user_ids: ids})
        });
        if (!addRes.ok) { var err2 = await addRes.json(); throw new Error(err2.detail); }
        var res = await addRes.json();

        overlay.remove();
        if (isReplace) {
          alert("교체 완료!\n배정: " + res.added + "명\n총 대상: " + res.total + "명");
        } else {
          alert("배정 완료!\n추가: " + res.added + "명\n이미 배정됨: " + res.skipped + "명\n총 대상: " + res.total + "명");
        }
        loadAdminGroups();
        loadAdminStats();
      } catch(e) {
        btnOk.textContent = "배정";
        btnOk.disabled = false;
        alert("배정 실패: " + e.message);
      }
    });
  };

  // View group members — modal with dept grouping, search, checkbox removal
  window.adminViewGroup = function(groupId, groupName) {
    fetch("/api/admin/groups/" + groupId + "/members")
      .then(function(r) { return r.json(); })
      .then(function(members) {
        if (!members.length) { alert("'" + groupName + "' 그룹에 멤버가 없습니다."); return; }

        // Group by department
        var deptMap = {};
        members.forEach(function(m) {
          var dept = m.department || "(부서 없음)";
          if (!deptMap[dept]) deptMap[dept] = [];
          deptMap[dept].push(m);
        });
        var sortedDepts = Object.keys(deptMap).sort();

        var overlay = document.createElement("div");
        overlay.className = "admin-modal-overlay";
        overlay.innerHTML =
          '<div class="admin-modal admin-modal-wide" style="max-width:600px">' +
          '<h3>' + escapeHtml(groupName) + ' 멤버 (' + members.length + '명)</h3>' +
          '<input type="text" id="member-search" placeholder="이름/부서 검색..." style="width:100%;padding:8px;margin:4px 0 8px;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-secondary);color:var(--text-primary);font-size:13px">' +
          '<div class="dept-user-header" style="margin-bottom:4px">' +
          '<span id="member-sel-count">0명 선택</span>' +
          '<label style="font-size:12px;cursor:pointer"><input type="checkbox" id="member-check-all"> 전체 선택</label></div>' +
          '<div id="member-dept-list" class="dept-user-list" style="max-height:400px;overflow-y:auto"></div>' +
          '<div class="admin-modal-actions">' +
          '<button class="admin-btn-secondary" id="member-cancel">닫기</button>' +
          (isAdmin() ? '<button class="admin-btn-primary danger" id="member-remove" style="display:none">선택 제거</button>' : '') +
          '</div></div>';
        document.body.appendChild(overlay);
        overlay.querySelector("#member-cancel").addEventListener("click", function() { overlay.remove(); });
        overlay.addEventListener("click", function(e) { if (e.target === overlay) overlay.remove(); });

        var listDiv = overlay.querySelector("#member-dept-list");
        var searchInput = overlay.querySelector("#member-search");
        var selCount = overlay.querySelector("#member-sel-count");
        var checkAllBox = overlay.querySelector("#member-check-all");
        var removeBtn = overlay.querySelector("#member-remove");

        function renderMembers(filter) {
          var q = (filter || "").toLowerCase();
          var html = "";
          var visibleCount = 0;
          sortedDepts.forEach(function(dept) {
            var filtered = deptMap[dept].filter(function(m) {
              if (!q) return true;
              return m.display_name.toLowerCase().indexOf(q) >= 0 || dept.toLowerCase().indexOf(q) >= 0;
            });
            if (!filtered.length) return;
            // Dept header — show short label
            var parts = dept.split(" > ");
            var shortDept = parts.slice(2).join(" > ") || dept;
            html += '<div class="member-dept-group">';
            html += '<div class="member-dept-header">' + escapeHtml(shortDept) + ' <span style="opacity:0.5">(' + filtered.length + '명)</span></div>';
            filtered.forEach(function(m) {
              html += '<label class="dept-user-item">' +
                '<input type="checkbox" data-mid="' + m.id + '"> ' +
                '<span class="dept-user-name">' + escapeHtml(m.display_name) + '</span>' +
                '</label>';
              visibleCount++;
            });
            html += '</div>';
          });
          listDiv.innerHTML = html || '<div style="padding:16px;text-align:center;color:var(--text-muted)">검색 결과 없음</div>';
          updateSelCount();
        }

        function updateSelCount() {
          var checked = listDiv.querySelectorAll('input[type="checkbox"]:checked').length;
          var total = listDiv.querySelectorAll('input[type="checkbox"]').length;
          selCount.textContent = checked + "/" + total + "명 선택";
          if (removeBtn) removeBtn.style.display = checked > 0 ? "" : "none";
          if (checkAllBox) checkAllBox.checked = checked > 0 && checked === total;
        }

        renderMembers("");

        searchInput.addEventListener("input", function() { renderMembers(this.value); });

        listDiv.addEventListener("change", function() { updateSelCount(); });

        if (checkAllBox) {
          checkAllBox.addEventListener("change", function() {
            var val = this.checked;
            listDiv.querySelectorAll('input[type="checkbox"]').forEach(function(cb) { cb.checked = val; });
            updateSelCount();
          });
        }

        if (removeBtn) {
          removeBtn.addEventListener("click", function() {
            var checked = listDiv.querySelectorAll('input[type="checkbox"]:checked');
            var ids = [];
            checked.forEach(function(cb) { ids.push(parseInt(cb.getAttribute("data-mid"))); });
            if (!ids.length) return;
            if (!confirm(ids.length + "명을 '" + groupName + "' 그룹에서 제거하시겠습니까?")) return;
            removeBtn.textContent = "제거 중...";
            removeBtn.disabled = true;
            fetch("/api/admin/groups/" + groupId + "/members", {
              method: "DELETE",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({ad_user_ids: ids})
            }).then(function(r) {
              if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail); });
              return r.json();
            }).then(function(res) {
              alert("제거 완료: " + res.removed + "명");
              overlay.remove();
              loadAdminGroups();
              loadAdminStats();
            }).catch(function(e) {
              removeBtn.textContent = "선택 제거";
              removeBtn.disabled = false;
              alert("제거 실패: " + e.message);
            });
          });
        }
      }).catch(function(e) { alert("멤버 조회 실패: " + e.message); });
  };

  // Edit group
  window.adminEditGroup = function(groupId) {
    var g = _adminGroups.find(function(x) { return x.id === groupId; }) || {};
    showAdminModal("그룹 편집", g.name || "", g.description || "", g.brand_filter || "", function(newName, newDesc, newBrandFilter) {
      fetch("/api/admin/groups/" + groupId, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: newName, description: newDesc, brand_filter: newBrandFilter})
      }).then(function(r) {
        if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail); });
        return r.json();
      }).then(function() { loadAdminGroups(); })
      .catch(function(e) { alert("그룹 수정 실패: " + e.message); });
    });
  };

  // Delete group
  window.adminDeleteGroup = function(groupId, name) {
    if (!confirm("'" + name + "' 그룹을 삭제하시겠습니까?\n멤버는 미배정 상태로 변경됩니다.")) return;
    fetch("/api/admin/groups/" + groupId, {method: "DELETE"})
      .then(function() { loadAdminGroups(); loadAdminStats(); })
      .catch(function(e) { alert("삭제 실패: " + e.message); });
  };

  // Modal helper
  function showAdminModal(title, nameVal, descVal, brandFilterVal, onSubmit) {
    var overlay = document.createElement("div");
    overlay.className = "admin-modal-overlay";
    overlay.innerHTML =
      '<div class="admin-modal">' +
      '<h3>' + title + '</h3>' +
      '<input type="text" id="modal-name" placeholder="그룹 이름" value="' + escapeHtml(nameVal) + '">' +
      '<textarea id="modal-desc" placeholder="설명 (선택)">' + escapeHtml(descVal) + '</textarea>' +
      '<div class="modal-brand-filter-section">' +
      '<label class="modal-label">브랜드 필터 <small style="color:var(--text-muted)">(쉼표 구분, 예: SK,CL,CBT)</small></label>' +
      '<input type="text" id="modal-brand-filter" placeholder="예: SK,CL,CBT 또는 UM" value="' + escapeHtml(brandFilterVal) + '">' +
      '</div>' +
      '<div class="admin-modal-actions">' +
      '<button class="admin-btn-secondary" id="modal-cancel">취소</button>' +
      '<button class="admin-btn-primary" id="modal-ok">확인</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector("#modal-name").focus();
    overlay.querySelector("#modal-cancel").addEventListener("click", function() { overlay.remove(); });
    overlay.addEventListener("click", function(e) { if (e.target === overlay) overlay.remove(); });
    overlay.querySelector("#modal-ok").addEventListener("click", function() {
      var n = overlay.querySelector("#modal-name").value.trim();
      var d = overlay.querySelector("#modal-desc").value.trim();
      var bf = overlay.querySelector("#modal-brand-filter").value.trim();
      if (!n) { alert("그룹 이름을 입력하세요."); return; }
      overlay.remove();
      onSubmit(n, d, bf);
    });
  }

  // Password change modal
  function showChangePasswordModal() {
    var overlay = document.createElement("div");
    overlay.className = "admin-modal-overlay";
    overlay.innerHTML =
      '<div class="admin-modal pw-modal">' +
      '<h3>비밀번호 변경</h3>' +
      '<label class="pw-label">현재 비밀번호</label>' +
      '<input type="password" id="pw-current" placeholder="현재 비밀번호 입력">' +
      '<label class="pw-label">새 비밀번호</label>' +
      '<input type="password" id="pw-new" placeholder="새 비밀번호 (4자 이상)">' +
      '<label class="pw-label">새 비밀번호 확인</label>' +
      '<input type="password" id="pw-confirm" placeholder="새 비밀번호 다시 입력">' +
      '<div class="pw-error" id="pw-error"></div>' +
      '<div class="admin-modal-actions">' +
      '<button class="admin-btn-secondary" id="pw-cancel">취소</button>' +
      '<button class="admin-btn-primary" id="pw-submit">변경</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    overlay.querySelector("#pw-current").focus();

    var close = function () { overlay.remove(); };
    overlay.querySelector("#pw-cancel").addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });

    overlay.querySelector("#pw-submit").addEventListener("click", async function () {
      var cur = overlay.querySelector("#pw-current").value;
      var nw = overlay.querySelector("#pw-new").value;
      var cf = overlay.querySelector("#pw-confirm").value;
      var errEl = overlay.querySelector("#pw-error");
      errEl.textContent = "";

      if (!cur) { errEl.textContent = "현재 비밀번호를 입력하세요"; return; }
      if (nw.length < 4) { errEl.textContent = "새 비밀번호는 4자 이상이어야 합니다"; return; }
      if (nw !== cf) { errEl.textContent = "새 비밀번호가 일치하지 않습니다"; return; }

      var btn = overlay.querySelector("#pw-submit");
      btn.disabled = true;
      btn.textContent = "변경 중...";

      try {
        var res = await fetch("/api/auth/change-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_password: cur, new_password: nw }),
        });
        var data = await res.json();
        if (!res.ok) {
          errEl.textContent = data.detail || "변경 실패";
          btn.disabled = false;
          btn.textContent = "변경";
          return;
        }
        overlay.querySelector(".pw-modal").innerHTML =
          '<h3>비밀번호 변경</h3>' +
          '<p class="pw-success">비밀번호가 변경되었습니다.</p>' +
          '<div class="admin-modal-actions">' +
          '<button class="admin-btn-primary" id="pw-done">확인</button>' +
          '</div>';
        overlay.querySelector("#pw-done").addEventListener("click", close);
      } catch (e) {
        errEl.textContent = "서버 연결 오류";
        btn.disabled = false;
        btn.textContent = "변경";
      }
    });
  }

  function escapeHtml(str) {
    if (!str) return "";
    var map = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"};
    return str.replace(/[&<>"']/g, function(c) { return map[c]; });
  }

})();
