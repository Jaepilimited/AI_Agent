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
    filterModelSelector();
    showAdminButton();
    await loadConversations();
    updateTheme();
    pollSystemStatus();
    setInterval(pollSystemStatus, 30000);
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
    });

    chatInput.addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 150) + "px";
      updateSendButton();
    });

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

  async function loadConversation(id) {
    try {
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
          showFollowups(lastUserMsg, lastMsg.content);
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
    var aiContent = "";
    var detectedSource = "";
    var aiMsgEl = appendMessage("assistant", "", true);
    var contentEl = aiMsgEl.querySelector(".message-content");

    try {
      var response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelSelect.value, messages: messages, stream: true }),
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
              // Detect source tag
              var srcMatch = delta.content.match(/<!-- source:(\w+) -->/);
              if (srcMatch) {
                detectedSource = srcMatch[1];
                // Strip source tag from content
                var stripped = delta.content.replace(/<!-- source:\w+ -->/, "");
                if (stripped) aiContent += stripped;
              } else {
                aiContent += delta.content;
              }
              renderMarkdown(contentEl, aiContent);
              scrollToBottom();
            }
          } catch (e) { /* skip */ }
        }
      }
    } catch (e) {
      aiContent = "오류가 발생했습니다: " + e.message;
      contentEl.textContent = aiContent;
    }

    var typing = aiMsgEl.querySelector(".typing-indicator");
    if (typing) typing.remove();

    // Strip source tag from final content for storage
    var cleanContent = aiContent.replace(/<!-- source:\w+ -->/g, "");
    contentEl.dataset.raw = cleanContent;
    renderMarkdown(contentEl, cleanContent);
    detectAndRenderCharts(contentEl, cleanContent);
    highlightCodeBlocks(contentEl);

    // Add source badge
    if (detectedSource && detectedSource !== "direct") {
      addSourceBadge(aiMsgEl, detectedSource);
    }

    // Add assistant message to in-memory history
    currentMessages.push({ role: "assistant", content: cleanContent });
    await saveMessage("assistant", cleanContent);

    isStreaming = false;
    scrollToBottom();

    // Show follow-up suggestions
    showFollowups(lastUserQuery, aiContent);
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

  function pickFollowups(query, answer) {
    var q = (query || "").toLowerCase();
    var pool = [];

    // Detect context from query (based on actual data platforms)
    if (/쇼피|shopee/.test(q)) pool = FOLLOWUP_POOLS.shopee;
    else if (/아마존|amazon/.test(q)) pool = FOLLOWUP_POOLS.amazon;
    else if (/틱톡|tiktok/.test(q)) pool = FOLLOWUP_POOLS.tiktok;
    else if (/cs|고객|반품|배송|교환|환불|성분|문의/.test(q)) pool = FOLLOWUP_POOLS.cs;
    else if (/매출|수량|제품|순위|비교|추이|증감|국가|플랫폼/.test(q)) pool = FOLLOWUP_POOLS.sales;
    else pool = FOLLOWUP_POOLS.general;

    // Remove the query itself from suggestions
    pool = pool.filter(function (s) { return s !== query; });

    // Shuffle and pick 3
    var shuffled = pool.slice().sort(function () { return Math.random() - 0.5; });
    return shuffled.slice(0, 3);
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

  function appendMessage(role, content, streaming) {
    if (role === "user") {
      return appendUserMessage(content, null);
    }

    var div = document.createElement("div");
    div.className = "message message-" + role;

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
      el.innerHTML = marked.parse(text, { breaks: true, gfm: true });
    } catch (e) {
      el.textContent = text;
    }
  }

  function highlightCodeBlocks(container) {
    container.querySelectorAll("pre code").forEach(function (block) {
      hljs.highlightElement(block);
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
      var chartDiv = document.createElement("div");
      chartDiv.className = "chart-container";
      var canvas = document.createElement("canvas");
      chartDiv.appendChild(canvas);
      container.appendChild(chartDiv);
      new Chart(canvas.getContext("2d"), config);

      var pre = container.querySelector("pre code");
      if (pre && pre.textContent.includes('"type"') && pre.textContent.includes('"data"')) {
        pre.closest("pre").style.display = "none";
      }
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
    "Claude API": {
      label: "Claude",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
    },
    "GWS Token": {
      label: "Token",
      svg: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>'
    },
  };

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

          html +=
            '<div class="status-item' + (st !== "ok" ? " status-alert" : "") + '">' +
            '<div class="status-item-row">' +
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
        container.innerHTML = html;

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
    "skin1004-Analysis": "Claude (Analysis)",
  };

  function filterModelSelector() {
    if (!currentUser || !currentUser.allowed_models) return;
    var allowed = currentUser.allowed_models;
    var options = modelSelect.querySelectorAll("option");
    var hasSelected = false;
    for (var i = 0; i < options.length; i++) {
      if (allowed.indexOf(options[i].value) === -1) {
        options[i].style.display = "none";
        options[i].disabled = true;
        if (options[i].selected) options[i].selected = false;
      } else {
        options[i].style.display = "";
        options[i].disabled = false;
        if (!hasSelected) { options[i].selected = true; hasSelected = true; }
      }
    }
  }

  function showAdminButton() {
    if (currentUser && currentUser.role === "admin") {
      document.getElementById("admin-btn-wrap").style.display = "";
    }
  }

  // ===== Admin Drawer =====
  function openAdminDrawer() {
    loadAdminUsers();
    document.getElementById("skin-admin-overlay").className = "open";
    document.getElementById("skin-admin-drawer").className = "open";
  }

  function closeAdminDrawer() {
    document.getElementById("skin-admin-overlay").className = "closed";
    document.getElementById("skin-admin-drawer").className = "closed";
  }

  function loadAdminUsers() {
    fetch("/api/admin/users")
      .then(function (r) { return r.json(); })
      .then(function (users) {
        var container = document.getElementById("admin-user-list");
        var html = "";
        for (var i = 0; i < users.length; i++) {
          var u = users[i];
          var initial = (u.name || "U").charAt(0).toUpperCase();
          var isAdmin = u.role === "admin";
          html += '<div class="admin-user-card">';
          html += '<div class="admin-user-info">';
          html += '<div class="admin-user-avatar">' + initial + '</div>';
          html += '<div class="admin-user-detail">';
          html += '<div class="admin-user-name">' + u.name + '</div>';
          html += '<div class="admin-user-email">' + u.email + '</div>';
          html += '</div>';
          html += '<span class="admin-role-badge ' + u.role + '">' + u.role + '</span>';
          html += '</div>';
          html += '<div class="admin-model-toggles">';
          for (var modelId in MODEL_LABELS) {
            var active = u.allowed_models.indexOf(modelId) !== -1;
            var disabled = isAdmin;
            html += '<button class="admin-model-toggle' +
              (active ? " active" : "") +
              (disabled ? " disabled" : "") +
              '" data-user-id="' + u.id + '" data-model="' + modelId + '"' +
              (disabled ? " disabled" : "") +
              '>' + MODEL_LABELS[modelId] + '</button>';
          }
          html += '</div>';
          html += '</div>';
        }
        container.innerHTML = html;

        // Attach click handlers
        container.querySelectorAll(".admin-model-toggle:not(.disabled)").forEach(function (btn) {
          btn.addEventListener("click", function () {
            toggleUserModel(this.dataset.userId, this.dataset.model, this);
          });
        });
      })
      .catch(function (e) { console.error("Failed to load admin users:", e); });
  }

  function toggleUserModel(userId, modelId, btnEl) {
    // Find current models from sibling buttons
    var card = btnEl.closest(".admin-user-card");
    var toggles = card.querySelectorAll(".admin-model-toggle:not(.disabled)");
    var currentModels = [];
    toggles.forEach(function (t) {
      if (t.classList.contains("active")) currentModels.push(t.dataset.model);
    });

    var idx = currentModels.indexOf(modelId);
    if (idx !== -1) {
      // Removing — ensure at least 1 model remains
      if (currentModels.length <= 1) {
        alert("최소 1개 모델은 필요합니다.");
        return;
      }
      currentModels.splice(idx, 1);
    } else {
      currentModels.push(modelId);
    }

    fetch("/api/admin/users/" + userId + "/models", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allowed_models: currentModels }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Failed");
        return r.json();
      })
      .then(function () {
        // Toggle visual state
        if (btnEl.classList.contains("active")) {
          btnEl.classList.remove("active");
        } else {
          btnEl.classList.add("active");
        }
      })
      .catch(function (e) {
        alert("모델 권한 변경 실패: " + e.message);
      });
  }

})();
