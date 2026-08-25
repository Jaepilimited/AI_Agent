/* Personal briefing welcome renderer. Keeps Google data out of persistent browser storage. */
(function () {
  "use strict";

  var ALLOWED_HOSTS = {
    "mail.google.com": true,
    "calendar.google.com": true
  };
  var STATUS_LABELS = {
    loading: "준비 중",
    ready: "최신",
    stale: "지난 정보",
    disconnected: "연결 필요",
    empty: "결과 없음",
    error: "오류",
    disabled: "꺼짐"
  };

  function safeUrl(value) {
    try {
      var base = window.location.origin === "null" ? "https://localhost" : window.location.origin;
      var url = new URL(value, base);
      if (url.protocol !== "https:") return "";
      if (ALLOWED_HOSTS[url.hostname]) return url.href;
      if (url.hostname === "www.google.com" && url.pathname.indexOf("/calendar/") === 0) {
        return url.href;
      }
    } catch (_error) {}
    return "";
  }

  function textNode(tag, className, value) {
    var element = document.createElement(tag);
    element.className = className;
    element.textContent = value || "";
    return element;
  }

  function statusOf(section) {
    return (section && section.status) || "empty";
  }

  function safeItems(section) {
    return section && Array.isArray(section.items) ? section.items : [];
  }

  function makeCard(title, section) {
    var card = document.createElement("article");
    var statusValue = statusOf(section);
    var status = textNode("span", "personal-briefing-status", STATUS_LABELS[statusValue] || STATUS_LABELS.empty);

    card.className = "personal-briefing-card";
    card.appendChild(textNode("h3", "personal-briefing-card-title", title));
    status.dataset.status = statusValue;
    card.appendChild(status);
    return card;
  }

  function putQuestionInInput(question, options) {
    if (!question || !options.input) return;
    options.input.value = question;
    options.input.dispatchEvent(new Event("input", { bubbles: true }));
    options.input.focus();
  }

  function addItem(list, label, url, question, options) {
    var title = label || "(제목 없음)";
    var href = safeUrl(url || "");
    var interactive = Boolean(href || question);
    var item = document.createElement(href ? "a" : (interactive ? "button" : "div"));

    item.className = "personal-briefing-item" + (interactive ? "" : " personal-briefing-item-static");
    item.textContent = title;
    item.title = title;
    if (href) {
      item.href = href;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    } else if (interactive) {
      item.type = "button";
      item.addEventListener("click", function () {
        putQuestionInInput(question, options);
      });
    }
    list.appendChild(item);
    return item;
  }

  function formatKstDate(value) {
    var date = /^\d{4}-\d{2}-\d{2}$/.test(value || "")
      ? new Date(value + "T00:00:00+09:00")
      : new Date(value);
    if (Number.isNaN(date.getTime())) return "일정";
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "numeric",
      day: "numeric",
      weekday: "short"
    }).format(date);
  }

  function appendEmptyMessage(card, message) {
    card.appendChild(textNode("p", "personal-briefing-empty", message));
  }

  function renderSkeleton(root) {
    var grid = root.querySelector(".personal-briefing-grid");
    var titles = ["오늘 우선 확인", "7일 일정", "오늘 메일", "업무 지표"];

    root.hidden = false;
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "personal-briefing-grid";
      grid.id = "personal-briefing-grid";
      root.appendChild(grid);
    }
    grid.replaceChildren();
    titles.forEach(function (title) {
      var card = makeCard(title, { status: "loading" });
      card.appendChild(textNode("div", "personal-briefing-skeleton", ""));
      grid.appendChild(card);
    });
  }

  function render(root, data, options) {
    var grid;
    var priorities;
    var calendar;
    var mail;
    var business;
    var updated;
    var calendarItems;
    var mailItems;
    var lastDate = "";

    if (!data || data.enabled === false) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    grid = root.querySelector(".personal-briefing-grid");
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "personal-briefing-grid";
      grid.id = "personal-briefing-grid";
      root.appendChild(grid);
    }
    grid.replaceChildren();

    priorities = makeCard("오늘 우선 확인", {
      status: Array.isArray(data.priorities) && data.priorities.length ? "ready" : "empty"
    });
    (Array.isArray(data.priorities) ? data.priorities : []).slice(0, 3).forEach(function (item) {
      addItem(
        priorities,
        item.title,
        item.url,
        item.follow_up || (item.title ? item.title + "을 확인할 때 무엇을 먼저 보면 좋을까?" : ""),
        options
      );
    });
    if (!priorities.querySelector(".personal-briefing-item")) {
      appendEmptyMessage(priorities, "지금 확인할 항목이 없습니다.");
    }
    grid.appendChild(priorities);

    calendar = makeCard("7일 일정", data.calendar || {});
    calendarItems = safeItems(data.calendar);
    calendarItems.forEach(function (item) {
      var dateLabel = formatKstDate(item.start);
      var eventItem;
      if (dateLabel !== lastDate) {
        calendar.appendChild(textNode("h4", "personal-briefing-date", dateLabel));
        lastDate = dateLabel;
      }
      eventItem = addItem(
        calendar,
        item.title,
        item.url,
        (item.title || "일정") + " 일정 준비사항을 알려줘",
        options
      );
      if (item.ended) eventItem.classList.add("ended");
    });
    if (!calendarItems.length) {
      appendEmptyMessage(
        calendar,
        statusOf(data.calendar) === "disconnected"
          ? "Google Workspace를 연결하면 일정이 표시됩니다."
          : "앞으로 7일간 등록된 일정이 없습니다."
      );
    }
    if (data.calendar && data.calendar.truncated) {
      calendar.appendChild(textNode("p", "personal-briefing-note", "50건 이상 · Google Calendar에서 전체 보기"));
    }
    grid.appendChild(calendar);

    mail = makeCard(
      "오늘 메일 · " + ((data.mail && data.mail.count_label) || "0건") + " · 안 읽음 " + ((data.mail && data.mail.unread) || 0),
      data.mail || {}
    );
    if (data.mail && data.mail.summary) {
      mail.appendChild(textNode("p", "personal-briefing-summary", data.mail.summary));
    }
    mailItems = safeItems(data.mail);
    mailItems.forEach(function (item) {
      addItem(
        mail,
        item.subject,
        item.url,
        ((item.from_display || "보낸 사람") + "의 " + (item.subject || "메일") + "을 자세히 요약해줘"),
        options
      );
    });
    if (!mailItems.length) {
      appendEmptyMessage(
        mail,
        statusOf(data.mail) === "disconnected"
          ? "Google Workspace를 연결하면 오늘 메일이 표시됩니다."
          : "오늘 받은 메일이 없습니다."
      );
    }
    if (
      statusOf(data.mail) === "disconnected"
      || (data.mail && data.mail.error_code === "oauth_expired")
      || (data.google && data.google.connected === false)
    ) {
      var connect = textNode("button", "personal-briefing-connect", "Google 연결");
      connect.type = "button";
      connect.addEventListener("click", function () {
        if (typeof options.connect === "function") options.connect();
      });
      mail.appendChild(connect);
    }
    if (data.mail && data.mail.truncated) {
      mail.appendChild(textNode("p", "personal-briefing-note", "20건 이상 · 최근 메일만 표시"));
    }
    grid.appendChild(mail);

    business = makeCard("업무 지표", data.business || {});
    if (data.business && data.business.item) {
      var businessItem = data.business.item;
      if (businessItem.for_date) {
        business.appendChild(textNode("p", "personal-briefing-date", "기준일 " + businessItem.for_date));
      }
      if (businessItem.body) {
        business.appendChild(textNode("p", "personal-briefing-summary", businessItem.body));
      }
      addItem(business, businessItem.title || "자세히 물어보기", "", businessItem.follow_up || "", options);
    } else {
      appendEmptyMessage(
        business,
        statusOf(data.business) === "disabled"
          ? "업무 지표 자동 브리핑이 꺼져 있습니다."
          : "새로 확인할 업무 지표가 없습니다."
      );
    }
    grid.appendChild(business);

    updated = root.querySelector(".personal-briefing-updated") || document.getElementById("personal-briefing-updated");
    if (updated) {
      updated.textContent = data.generated_at ? "마지막 갱신 " + data.generated_at : "저장된 정보 없음";
    }
  }

  function markLoadFailure(root) {
    var updated = root.querySelector(".personal-briefing-updated") || document.getElementById("personal-briefing-updated");
    root.querySelectorAll(".personal-briefing-status").forEach(function (status) {
      if (status.dataset.status === "loading") {
        status.dataset.status = "error";
        status.textContent = "불러오지 못함";
      }
    });
    if (updated) updated.textContent = "브리핑을 불러오지 못했습니다.";
  }

  function mergeTimeoutEnvelope(previous, fresh) {
    var sameDay = previous && fresh && previous.for_date === fresh.for_date;
    var result;
    var sawTimeout = false;

    if (!sameDay) return fresh;
    result = Object.assign({}, fresh);
    ["calendar", "mail"].forEach(function (key) {
      var nextSection = fresh[key] || {};
      var oldSection = previous[key] || {};
      if (nextSection.error_code !== "google_timeout") return;
      sawTimeout = true;
      if (!safeItems(nextSection).length && safeItems(oldSection).length) {
        result[key] = Object.assign({}, oldSection, {
          status: "stale",
          error_code: "google_timeout"
        });
      }
    });
    if (!sawTimeout) return fresh;
    if (!fresh.generated_at && previous.generated_at) result.generated_at = previous.generated_at;
    if ((!fresh.priorities || !fresh.priorities.length) && previous.priorities) {
      result.priorities = previous.priorities;
    }
    if (
      fresh.business && fresh.business.status === "error"
      && previous.business && previous.business.item
    ) {
      result.business = previous.business;
    }
    result.google = previous.google;
    result.needs_refresh = false;
    return result;
  }

  function notifyGoogleState(options, data) {
    if (typeof options.onGoogleState !== "function" || !data || !data.google) return;
    options.onGoogleState(Boolean(data.google.connected), data.google.account || "");
  }

  function create(options) {
    var root = options.root;
    var state = null;
    var requestGeneration = 0;

    async function load() {
      var generation = ++requestGeneration;
      var response;
      var payload;
      if (!state) renderSkeleton(root);
      try {
        response = await options.fetchImpl("/api/personal-briefing");
        if (generation !== requestGeneration) return;
        if (!response.ok) throw new Error("briefing get failed");
        payload = await response.json();
        if (generation !== requestGeneration) return;
        state = payload;
      } catch (_error) {
        if (generation !== requestGeneration) return;
        if (!state) markLoadFailure(root);
        return;
      }

      notifyGoogleState(options, state);
      render(root, state, options);
      if (!state.enabled || !state.needs_refresh) return;
      try {
        var fresh = await options.fetchImpl("/api/personal-briefing/refresh", { method: "POST" });
        if (generation !== requestGeneration) return;
        if (!fresh.ok) throw new Error("briefing refresh failed");
        payload = await fresh.json();
        if (generation !== requestGeneration) return;
        state = mergeTimeoutEnvelope(state, payload);
        notifyGoogleState(options, state);
        render(root, state, options);
      } catch (_error) {
        if (generation !== requestGeneration) return;
        // Keep the GET cache visible. A stale status is supplied by the server when applicable.
        if (!state) markLoadFailure(root);
      }
    }

    function invalidate() {
      requestGeneration += 1;
      state = null;
      renderSkeleton(root);
      notifyGoogleState(options, { google: { connected: false, account: "" } });
    }

    return {
      load: load,
      show: function () {
        if (state) render(root, state, options);
      },
      refreshAfterConnect: load,
      invalidate: invalidate
    };
  }

  window.CellaPersonalBriefing = {
    create: create,
    safeUrl: safeUrl
  };
})();
