/* SKIN1004 AI — chat.js
   Main chat SPA: SSE streaming, sidebar (date-grouped, search, collapse),
   follow-up suggestions, markdown, charts, theme
*/

(function () {
  "use strict";

  // ===== State =====
  var currentUser = null;
  var conversations = [];
  var currentConvoId = null;
  var currentMessages = [];  // In-memory message history for API calls
  var isStreaming = false;
  var lastUserQuery = "";
  var currentAbortController = null;  // AbortController for active stream

  // ===== Data Source Filter =====
  // Queryable data sources — shown with checkboxes in System Status
  var DATA_SOURCE_KEYS = [
    "BigQuery 매출", "BigQuery 제품",
    "BQ 광고데이터", "BQ 마케팅비용", "BQ Shopify", "BQ 플랫폼",
    "BQ 인플루언서", "BQ 아마존검색",
    "BQ 아마존리뷰", "BQ 큐텐리뷰", "BQ 쇼피리뷰", "BQ 스마트스토어", "BQ 메타광고",
    "Notion 문서", "CS Q&A", "Google Workspace"
  ];
  // Source key → route mapping for orchestrator
  var SOURCE_ROUTE_MAP = {
    "BigQuery 매출": "bigquery", "BigQuery 제품": "bigquery",
    "BQ 광고데이터": "bigquery", "BQ 마케팅비용": "bigquery",
    "BQ Shopify": "bigquery", "BQ 플랫폼": "bigquery",
    "BQ 인플루언서": "bigquery", "BQ 아마존검색": "bigquery",
    "BQ 아마존리뷰": "bigquery", "BQ 큐텐리뷰": "bigquery",
    "BQ 쇼피리뷰": "bigquery", "BQ 스마트스토어": "bigquery",
    "BQ 메타광고": "bigquery",
    "Notion 문서": "notion", "CS Q&A": "cs", "Google Workspace": "gws"
  };
  var enabledSources = loadEnabledSources();

  function loadEnabledSources() {
    try {
      var saved = localStorage.getItem("skin1004_enabled_sources");
      if (saved) return JSON.parse(saved);
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
    // Return BQ table keys only (for SQL agent table filtering)
    return enabledSources.filter(function(k) { return SOURCE_ROUTE_MAP[k] === "bigquery"; });
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
      "SKIN1004 반품 절차 알려줘",
      "배송 지연 시 고객 응대 방법은?",
      "COMMONLABS 제품 성분 문의 답변",
      "교환/환불 정책 안내해줘",
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
    updateSourceFilterBadge();
    checkGwsStatus();
  }

  // ===== Event Listeners =====
  function setupEventListeners() {
    btnSend.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
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
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 150) + "px";
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
    convoSearch.addEventListener("input", function () {
      renderConvoList(this.value.trim().toLowerCase());
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
    document.getElementById("admin-drawer-close").addEventListener("click", closeAdminDrawer);
    document.getElementById("skin-admin-overlay").addEventListener("click", closeAdminDrawer);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeDashboard(); closeStatusDrawer(); closeAdminDrawer(); }
    });

    // Theme toggle
    document.getElementById("skin-theme-toggle").addEventListener("click", toggleTheme);

    // GWS connect
    document.getElementById("btn-gws-connect").addEventListener("click", handleGwsConnect);
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

    // Group by date
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

      items.forEach(function (c) {
        var div = document.createElement("div");
        div.className = "convo-item" + (c.id === currentConvoId ? " active" : "");
        div.dataset.id = c.id;

        // Chat icon
        var icon = document.createElement("span");
        icon.className = "convo-icon";
        icon.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
        div.appendChild(icon);

        var title = document.createElement("span");
        title.className = "convo-title";
        title.textContent = c.title;
        div.appendChild(title);

        // Actions (edit, delete) — show on hover
        var actions = document.createElement("div");
        actions.className = "convo-actions";

        var editBtn = document.createElement("button");
        editBtn.className = "convo-action-btn";
        editBtn.title = "이름 변경";
        editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
        editBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          renameConversation(c.id, c.title);
        });
        actions.appendChild(editBtn);

        var delBtn = document.createElement("button");
        delBtn.className = "convo-action-btn convo-action-delete";
        delBtn.title = "삭제";
        delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
        delBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          deleteConversation(c.id);
        });
        actions.appendChild(delBtn);

        div.appendChild(actions);

        div.addEventListener("click", function () {
          loadConversation(c.id);
          closeMobileSidebar();
        });

        convoList.appendChild(div);
      });
    });
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
    // Abort active stream if switching conversations
    if (isStreaming && currentAbortController) {
      currentAbortController.abort();
      isStreaming = false;
      currentAbortController = null;
    }
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
      data.messages.forEach(function (m) {
        appendMessage(m.role, m.content, false);
        currentMessages.push({ role: m.role, content: m.content });
      });

      // Show follow-ups for last assistant message
      if (data.messages.length > 0) {
        var lastMsg = data.messages[data.messages.length - 1];
        var lastUserMsg = "";
        for (var i = data.messages.length - 1; i >= 0; i--) {
          if (data.messages[i].role === "user") { lastUserMsg = data.messages[i].content; break; }
        }
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

  async function renameConversation(id, oldTitle) {
    var newTitle = prompt("대화 이름 변경:", oldTitle);
    if (!newTitle || newTitle === oldTitle) return;
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

  async function saveMessage(role, content) {
    if (!currentConvoId) return;
    try {
      await fetch("/api/conversations/" + currentConvoId + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: role, content: content }),
      });
      await loadConversations();
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

  // ===== Send Message =====
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

    lastUserQuery = text;
    var imagesToSend = pendingImages.slice();  // snapshot
    hideFollowups();

    if (!currentConvoId) {
      var id = await createConversation();
      if (!id) return;
      currentMessages = [];
    }

    chatWelcome.style.display = "none";
    // Render user message with images in chat bubble
    appendUserMessage(text, imagesToSend);
    chatInput.value = "";
    chatInput.style.height = "auto";
    clearPendingImages();
    btnSend.disabled = true;
    // Snapshot slash override BEFORE clearing (used in fetch body below)
    var _sendSources = slashOverrideSource || enabledSources.slice();
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
    // Save only text to DB (no images in SQLite)
    await saveMessage("user", text || "[Image]");
    scrollToBottom();

    // Use in-memory messages for API (reliable, no DOM parsing)
    var messages = currentMessages.slice();

    // Stream response
    isStreaming = true;
    if (currentAbortController) currentAbortController.abort();
    currentAbortController = new AbortController();
    var aiContent = "";
    var detectedSource = "";
    var aiMsgEl = appendMessage("assistant", "", true);
    var contentEl = aiMsgEl.querySelector(".message-content");

    try {
      var response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelSelect.value,
          messages: messages,
          stream: true,
          brand_filter: (currentUser && currentUser.my_brand_filter) || null,
          enabled_sources: _sendSources
        }),
        signal: currentAbortController.signal,
      });

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

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
              var srcMatch = delta.content.match(/<!-- source:(\w+) -->/);
              if (srcMatch) {
                detectedSource = srcMatch[1];
                var stripped = delta.content.replace(/<!-- source:\w+ -->/, "");
                if (stripped) aiContent += stripped;
              } else {
                aiContent += delta.content;
              }
              // Throttled markdown render: max once per 300ms
              var now = Date.now();
              if (!contentEl._lastRender || now - contentEl._lastRender > 300) {
                renderMarkdown(contentEl, aiContent);
                contentEl._lastRender = now;
              } else if (!contentEl._pendingRender) {
                contentEl._pendingRender = setTimeout(function() {
                  renderMarkdown(contentEl, aiContent);
                  contentEl._lastRender = Date.now();
                  contentEl._pendingRender = null;
                }, 300);
              }
              scrollToBottom();
            }
          } catch (e) { /* skip */ }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        var typing = aiMsgEl.querySelector(".typing-indicator");
        if (typing) typing.remove();
        isStreaming = false;
        currentAbortController = null;
        return;
      }
      aiContent = "오류가 발생했습니다: " + e.message;
      contentEl.textContent = aiContent;
    }

    if (contentEl._pendingRender) {
      clearTimeout(contentEl._pendingRender);
      contentEl._pendingRender = null;
    }

    var typing = aiMsgEl.querySelector(".typing-indicator");
    if (typing) typing.remove();

    var cleanContent = aiContent.replace(/<!-- source:\w+ -->/g, "");
    contentEl.dataset.raw = cleanContent;
    renderMarkdown(contentEl, cleanContent);
    detectAndRenderCharts(contentEl, cleanContent);
    highlightCodeBlocks(contentEl);

    if (detectedSource && detectedSource !== "direct") {
      addSourceBadge(aiMsgEl, detectedSource);
    }

    currentMessages.push({ role: "assistant", content: cleanContent });
    await saveMessage("assistant", cleanContent);

    isStreaming = false;
    currentAbortController = null;
    scrollToBottom();
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
    else if (/cs|고객|반품|배송|교환|환불|성분|문의/.test(q)) pool = FOLLOWUP_POOLS.cs;
    else if (/매출|수량|제품|순위|비교|추이|증감|국가|플랫폼/.test(q)) pool = FOLLOWUP_POOLS.sales;
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

    // Find the follow-up block (after 💡)
    var blockStart = answer.indexOf("💡");
    if (blockStart === -1) return [];

    var blockText = answer.substring(blockStart);
    // Match lines starting with > - or just - after the 💡 marker
    var lines = blockText.split("\n");
    for (var i = 1; i < lines.length; i++) {
      var line = lines[i].trim();
      // Stop at empty line or new section (heading, horizontal rule)
      if (!line || line.startsWith("#") || line === "---") break;
      // Extract suggestion text from "> - text" or "- text" patterns
      var match = line.match(/^>?\s*[-*]\s*["""]?(.+?)["""]?\s*$/);
      if (match) {
        var text = match[1].trim();
        // Remove trailing quotes and markdown artifacts
        text = text.replace(/^["""]|["""]$/g, "").trim();
        if (text.length > 5 && text.length < 100) {
          suggestions.push(text);
        }
      }
    }
    return suggestions;
  }

  // ===== Source Badge (SVG icons matching system status) =====
  var SOURCE_BADGES = {
    bigquery: {
      label: "BQ 매출",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
    },
    bigquery_fallback: {
      label: "BQ 매출",
      svg: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
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

  function addSourceBadge(msgEl, source) {
    var info = SOURCE_BADGES[source] || { label: source, svg: '' };
    var badge = document.createElement("div");
    badge.className = "source-badge";
    badge.innerHTML = info.svg + '<span>' + info.label + '</span>';
    var contentEl = msgEl.querySelector(".message-content");
    if (contentEl) contentEl.insertBefore(badge, contentEl.firstChild);
  }

  // ===== Message Rendering =====
  function appendUserMessage(text, images) {
    var div = document.createElement("div");
    div.className = "message message-user";

    var ts = document.createElement("span");
    ts.className = "msg-timestamp";
    ts.textContent = _formatTimestamp();
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

    div.appendChild(bubble);
    chatMessages.appendChild(div);
    return div;
  }

  function _formatTimestamp() {
    var now = new Date();
    var hh = String(now.getHours()).padStart(2, "0");
    var mm = String(now.getMinutes()).padStart(2, "0");
    return hh + ":" + mm;
  }

  function appendMessage(role, content, streaming) {
    if (role === "user") {
      return appendUserMessage(content, null);
    }

    var div = document.createElement("div");
    div.className = "message message-" + role;

    // Timestamp (visible on hover)
    var ts = document.createElement("span");
    ts.className = "msg-timestamp";
    ts.textContent = _formatTimestamp();
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

  function renderMarkdown(el, text) {
    if (!text) { el.innerHTML = ""; return; }
    try {
      // Strip follow-up suggestion block from rendered content (shown as chips instead)
      var cleaned = stripFollowupBlock(text);
      el.innerHTML = marked.parse(cleaned, { breaks: true, gfm: true });
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
      // Add copy button to code blocks (enterprise feature)
      var pre = block.parentElement;
      if (pre && !pre.querySelector(".code-copy-btn")) {
        var btn = document.createElement("button");
        btn.className = "code-copy-btn";
        btn.textContent = "Copy";
        btn.addEventListener("click", function () {
          navigator.clipboard.writeText(block.textContent).then(function () {
            btn.textContent = "Copied!";
            btn.classList.add("copied");
            setTimeout(function () {
              btn.textContent = "Copy";
              btn.classList.remove("copied");
            }, 1500);
          });
        });
        pre.style.position = "relative";
        pre.appendChild(btn);
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

      // Theme-aware colors
      var textColor = isDark ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.75)";
      var gridColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
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
              var formatted = typeof val === "number" ? Math.round(val).toLocaleString() : val;
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
                config.options.scales[axis].ticks.callback = function(value, index) {
                  var labels = config.data && config.data.labels;
                  if (labels && labels[index] != null) return labels[index];
                  return value;
                };
              } else {
                // Numeric value axis: no decimals, comma-formatted
                config.options.scales[axis].ticks.callback = function(value) {
                  return typeof value === "number" ? Math.round(value).toLocaleString() : value;
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
      chartDiv.style.cssText = "position:relative;width:100%;max-width:720px;margin:16px auto;padding:16px;border-radius:12px;background:" + (isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.02)") + ";border:1px solid " + (isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)") + ";";

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

      new Chart(canvas.getContext("2d"), config);
    } catch (e) {
      console.warn("Chart render failed:", e);
    }
  }

  function showWelcome() {
    chatMessages.innerHTML = "";
    chatMessages.appendChild(chatWelcome);
    chatWelcome.style.display = "flex";
  }

  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // Scroll-to-bottom button (appears when scrolled up)
  var btnScrollBottom = document.getElementById("btn-scroll-bottom");
  if (btnScrollBottom) {
    chatMessages.addEventListener("scroll", function () {
      var distFromBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
      btnScrollBottom.style.display = distFromBottom > 200 ? "flex" : "none";
    });
    btnScrollBottom.addEventListener("click", function () {
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

  // ===== System Status (SVG icons) =====
  var SERVICE_ICONS = {
    "BigQuery 매출": {
      label: "BQ 매출",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>'
    },
    "BigQuery 제품": {
      label: "BQ 제품",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>'
    },
    // Marketing DB tables
    "BQ 광고데이터": {
        label: "광고",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>'
    },
    "BQ 마케팅비용": {
        label: "마케팅",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>'
    },
    "BQ Shopify": {
        label: "Shopify",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>'
    },
    "BQ 플랫폼": {
        label: "플랫폼",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>'
    },
    "BQ 인플루언서": {
        label: "인플루언서",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>'
    },
    "BQ 아마존검색": {
        label: "AZ검색",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
    },
    "BQ 아마존리뷰": {
        label: "AZ리뷰",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    },
    "BQ 큐텐리뷰": {
        label: "큐텐리뷰",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    },
    "BQ 쇼피리뷰": {
        label: "쇼피리뷰",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    },
    "BQ 스마트스토어": {
        label: "스마트스토어",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>'
    },
    "BQ 메타광고": {
        label: "메타광고",
        svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>'
    },
    "Notion 문서": {
      label: "Notion",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'
    },
    "CS Q&A": {
      label: "CS Q&A",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
    },
    "Google Workspace": {
      label: "GWS",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
    },
    "Gemini API": {
      label: "Gemini",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
    },
    "GWS Token": {
      label: "Token",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>'
    },
  };

  // ===== // Slash Command & Source Filter =====
  var slashOverrideSource = null;  // One-time override from // command
  var _slashTempSelection = [];    // Temp selection state for // multi-select

  // Quick-select presets for // command
  var SLASH_PRESETS = [
    { cmd: "매출", label: "매출 데이터", keys: ["BigQuery 매출"] },
    { cmd: "제품", label: "제품 데이터", keys: ["BigQuery 제품"] },
    { cmd: "광고", label: "광고 데이터", keys: ["BQ 광고데이터", "BQ 메타광고"] },
    { cmd: "리뷰", label: "리뷰 전체", keys: ["BQ 아마존리뷰", "BQ 큐텐리뷰", "BQ 쇼피리뷰", "BQ 스마트스토어"] },
    { cmd: "notion", label: "Notion 문서", keys: ["Notion 문서"] },
    { cmd: "cs", label: "CS Q&A", keys: ["CS Q&A"] },
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
          // Toggle preset keys
          var allOn = preset.keys.every(function(k) { return _slashTempSelection.indexOf(k) >= 0; });
          preset.keys.forEach(function(k) {
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

  function pollSystemStatus() {
    fetch("/safety/status")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.services) return;
        var container = document.getElementById("status-items");
        var inlineEl = document.getElementById("sidebar-status-inline");
        var maintenanceReason = (data.maintenance && data.maintenance.reason) || "";
        var html = "";
        var issues = [];  // Collect issues for inline indicator

        for (var name in data.services) {
          var svc = data.services[name];
          var st = svc.status || "ok";
          var labels = { ok: "정상", updating: "업데이트 중", error: "오류" };
          var labelClass = st === "updating" ? " updating" : (st !== "ok" ? " error" : "");
          var info = SERVICE_ICONS[name] || { label: name, svg: '' };
          var detail = svc.detail || "";
          var alertMsg = "";
          if (st === "updating") alertMsg = maintenanceReason;
          else if (st === "error") alertMsg = detail;

          // Collect issues for inline sidebar indicator
          if (st === "updating") {
            issues.push(info.label + ": 업데이트 중");
          } else if (st === "error") {
            issues.push(info.label + ": 오류");
          }

          // Checkbox for queryable data sources
          var isQueryable = DATA_SOURCE_KEYS.indexOf(name) >= 0;
          var isChecked = enabledSources.indexOf(name) >= 0;
          var checkboxHtml = isQueryable
            ? '<label class="status-checkbox-label"><input type="checkbox" class="status-source-cb" data-source="' + name + '"' + (isChecked ? ' checked' : '') + '></label>'
            : '';

          html +=
            '<div class="status-item' + (st !== "ok" ? " status-alert" : "") + '">' +
            '<div class="status-item-row">' +
            checkboxHtml +
            '<span class="status-dot' + (st !== "ok" ? " error" : "") + '"></span>' +
            '<span class="status-icon">' + info.svg + '</span>' +
            '<span class="status-name">' + info.label + '</span>' +
            '<span class="status-label' + labelClass + '">' + (labels[st] || st) + '</span>' +
            '</div>';
          if (alertMsg) {
            html +=
              '<div class="status-msg-wrap">' +
              '<div class="status-msg-ticker"><span>' + alertMsg + '</span></div>' +
              '</div>';
          }
          html += '</div>';
        }
        // Add select-all / deselect-all toolbar before items
        var toolbar = '<div class="source-select-toolbar">' +
          '<button class="source-btn-all" id="source-select-all">전체 선택</button>' +
          '<button class="source-btn-none" id="source-deselect-all">전체 해제</button>' +
          '<span class="source-count-label" id="source-count-label">' + enabledSources.length + '/' + DATA_SOURCE_KEYS.length + '</span>' +
          '</div>';
        container.innerHTML = toolbar + html;

        // Attach select-all / deselect-all listeners
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

        // Attach checkbox listeners (event delegation)
        container.querySelectorAll(".status-source-cb").forEach(function(cb) {
          cb.addEventListener("change", function() {
            toggleSource(this.getAttribute("data-source"));
            document.getElementById("source-count-label").textContent = enabledSources.length + '/' + DATA_SOURCE_KEYS.length;
            updateSourceFilterBadge();
          });
        });

        // Update inline sidebar status indicator
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
    "skin1004-Analysis": "SKIN1004 Analysis",
  };

  function showAdminButton() {
    if (currentUser && currentUser.role === "admin") {
      document.getElementById("admin-btn-wrap").style.display = "";
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

  // Tab switching
  document.querySelectorAll(".admin-tab").forEach(function(tab) {
    tab.addEventListener("click", function() {
      document.querySelectorAll(".admin-tab").forEach(function(t) { t.classList.remove("active"); });
      document.querySelectorAll(".admin-tab-content").forEach(function(c) { c.classList.remove("active"); });
      tab.classList.add("active");
      document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
      if (tab.dataset.tab === "users") loadAdminADUsers();
    });
  });

  // Stats
  function loadAdminStats() {
    fetch("/api/admin/ad/stats").then(function(r) { return r.json(); }).then(renderAdminStats).catch(function() {});
  }
  function renderAdminStats(s) {
    document.getElementById("admin-stats-bar").innerHTML =
      '<div class="admin-stat"><div class="admin-stat-num">' + s.total_ad_users + '</div><div class="admin-stat-label">AD 사용자</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.assigned_users + '</div><div class="admin-stat-label">배정됨</div></div>' +
      '<div class="admin-stat"><div class="admin-stat-num">' + s.unassigned_users + '</div><div class="admin-stat-label">미배정</div></div>' +
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
    var gfHtml = '<option value="">전체 그룹</option><option value="unassigned">미배정</option>';
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

          html += '<div class="admin-ad-user">';
          html += '<div class="admin-ad-avatar">' + initial + '</div>';
          html += '<div class="admin-ad-info">';
          html += '<div class="admin-ad-name">' + escapeHtml(u.display_name) + ' <small style="color:var(--text-muted)">(' + escapeHtml(u.username) + ')</small></div>';
          html += '<div class="admin-ad-email">' + escapeHtml(u.email || "N/A") + '</div>';
          html += '<div class="admin-ad-dept">' + escapeHtml(deptShort) + '</div>';
          html += '</div>';
          html += groupBadge;
          if (isAdmin()) {
            html += '<button class="admin-ad-assign" onclick="adminAssignUser(' + u.id + ', \'' + escapeHtml(u.display_name) + '\')">배정</button>';
          }
          html += '</div>';
        });
        container.innerHTML = html;
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
