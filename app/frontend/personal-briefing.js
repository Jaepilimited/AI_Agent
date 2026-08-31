/* Personal briefing welcome renderer. Keeps Google data out of persistent browser storage. */
(function () {
  "use strict";

  var ALLOWED_HOSTS = {
    "mail.google.com": true,
    "calendar.google.com": true,
    "meet.google.com": true
  };
  /* 접힘 상태는 이 탭에서만 기억한다. ⛔ 브라우저 저장소를 쓰지 않는다 —
     이 파일은 구글 데이터를 다루므로 영속 저장 경로를 아예 두지 않는 것이 규칙이다
     (tests/frontend/test_personal_briefing_welcome.py 가 영속 저장소 호출을 막는다). */
  var docCollapsed = false;
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

  function makeCard(title, section, count) {
    var card = document.createElement("article");
    var statusValue = statusOf(section);
    var heading = textNode("h3", "personal-briefing-card-title", "");
    var status = textNode("span", "personal-briefing-status",
      STATUS_LABELS[statusValue] || STATUS_LABELS.empty);
    var tail = textNode("span", "personal-briefing-card-tail", "");

    card.className = "personal-briefing-card";
    heading.appendChild(textNode("span", "personal-briefing-card-name", title));
    status.dataset.status = statusValue;
    tail.appendChild(status);
    if (typeof count === "number") {
      tail.appendChild(textNode("span", "personal-briefing-count", String(count)));
    }
    heading.appendChild(tail);
    card.appendChild(heading);
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

  /* 8/27(목) — 문서의 기한 표기와 같은 형식이다. 한 화면에 두 어법을 두지 않는다. */
  function formatKstDate(value) {
    var date = /^\d{4}-\d{2}-\d{2}$/.test(value || "")
      ? new Date(value + "T00:00:00+09:00")
      : new Date(value);
    var parts;
    if (Number.isNaN(date.getTime())) return "일정";
    parts = new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", month: "numeric", day: "numeric", weekday: "short"
    }).formatToParts(date).reduce(function (acc, part) {
      acc[part.type] = part.value;
      return acc;
    }, {});
    return parts.month + "/" + parts.day + "(" + parts.weekday + ")";
  }

  function formatKstTime(item) {
    var value = (item && item.start) || "";
    var date;
    if ((item && item.all_day) || /^\d{4}-\d{2}-\d{2}$/.test(value)) return "종일";
    date = new Date(value);
    if (Number.isNaN(date.getTime())) return "시간 미정";
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).format(date);
  }

  function appendEmptyMessage(card, message) {
    card.appendChild(textNode("p", "personal-briefing-empty", message));
  }

  function isSameKstDay(value, day) {
    if (!value || !day) return false;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value === day;
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return false;
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit"
    }).format(date) === day;
  }

  /* 브리핑 문서. 서버가 준 값만 textContent 로 그린다 — HTML 주입 경로를 두지 않는다. */
  function docRoot(root) {
    var node = root.querySelector(".briefing-doc");
    if (!node) {
      node = document.createElement("article");
      node.className = "briefing-doc";
      root.insertBefore(node, root.querySelector(".personal-briefing-grid"));
    }
    return node;
  }

  /* 헤더 수치. ⛔ 이모지를 붙이지 않는다 — 라벨과 숫자만으로 읽힌다.
     이모지 헤딩은 이 화면이 "생성된 것"처럼 보이게 만드는 가장 큰 요인이었다. */
  function statLine(doc) {
    var stats = [
      { label: "일정", value: (doc.meetings || []).length },
      { label: "메일", value: doc.mail_total || 0 }
    ];
    if (doc.urgent) stats.push({ label: "긴급", value: doc.urgent, hot: true });
    if ((doc.deadlines || []).length) stats.push({ label: "기한", value: doc.deadlines.length });
    return stats;
  }

  function statNode(stats) {
    var wrap = textNode("span", "briefing-doc-stats", "");
    stats.forEach(function (stat) {
      var item = textNode("span", "briefing-doc-stat" + (stat.hot ? " hot" : ""), "");
      item.appendChild(textNode("span", "briefing-doc-stat-label", stat.label));
      item.appendChild(textNode("span", "briefing-doc-stat-value", String(stat.value)));
      wrap.appendChild(item);
    });
    return wrap;
  }

  function docLine(parent, className, value) {
    if (!value) return null;
    return parent.appendChild(textNode("p", className, value));
  }

  function docSection(body, title, count, extra) {
    var section = document.createElement("section");
    var heading = textNode("h4", "briefing-doc-section-title", "");

    section.className = "briefing-doc-section";
    heading.appendChild(textNode("span", "briefing-doc-section-name", title));
    if (extra) heading.appendChild(textNode("span", "briefing-doc-badge", extra));
    if (typeof count === "number") {
      heading.appendChild(textNode("span", "briefing-doc-count", String(count)));
    }
    section.appendChild(heading);
    body.appendChild(section);
    section._heading = heading;      // 절 제목 줄에 동작을 얹을 수 있게 열어 둔다
    return section;
  }

  /* 절 제목 오른쪽에 다는 작은 동작. ⚠️ 개수 배지와 같은 줄 높이를 지켜야 한다 —
     여기서 줄이 커지면 옆 열과 밑줄이 어긋난다 (2026-08-27 에 고친 그 함정). */
  function sectionAction(section, label, onClick) {
    var button = textNode("button", "briefing-doc-section-action", label);
    button.type = "button";
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      onClick();
    });
    (section._heading || section).appendChild(button);
    return button;
  }

  /* 왼쪽 고정폭 시간축이 이 화면의 뼈대다.
     일정은 시작·종료, 메일은 수신 시각, 기한은 날짜 — 모든 항목이 '언제'를 갖는다.
     그래서 아이콘 없이도 종류가 읽히고, 하루가 위에서 아래로 흐른다.
     ⚠️ 숫자는 tabular-nums 여야 세로로 맞는다 (style.css). 안 맞으면 축이 성립하지 않는다. */
  function docRow(section, urgency, label, url, options, question, when) {
    var row = document.createElement("div");
    var href = safeUrl(url || "");
    var head = href ? document.createElement("a") : document.createElement("button");
    var time = textNode("div", "briefing-doc-time", "");
    var main = textNode("div", "briefing-doc-main", "");
    var stamp = when || {};

    row.className = "briefing-doc-row" + (urgency === "high" ? " urgent" : "");
    time.appendChild(textNode("b", "", stamp.start || "—"));
    if (stamp.end) time.appendChild(textNode("span", "", stamp.end));
    row.appendChild(time);

    head.className = "briefing-doc-row-head";
    head.textContent = label;
    head.title = label;
    if (href) {
      head.href = href;
      head.target = "_blank";
      head.rel = "noopener noreferrer";
    } else {
      head.type = "button";
      head.addEventListener("click", function () {
        putQuestionInInput(question || label, options);
      });
    }
    main.appendChild(head);
    row.appendChild(main);
    section.appendChild(row);
    return main;
  }

  function splitRange(value) {
    var parts = String(value || "").split("~");
    return { start: (parts[0] || "").trim(), end: (parts[1] || "").trim() };
  }

  /* 일정 한 줄의 '장소 · 참석자'. 문서(오늘)와 카드(향후 일정)가 **같은 함수**를 쓴다.
     ⚠️ `attendee_count` 는 문서 행에만 있다 (카드는 원본 항목이라 배열 길이를 센다).
        둘 다 서버에서 20명으로 잘려 있어 같은 수를 본다. */
  function eventDetail(item) {
    var attendees = item.attendees || [];
    var total = item.attendee_count || attendees.length;
    var detail = [];
    var shown;
    var more;

    if (item.location) detail.push(item.location);
    if (total) {
      shown = attendees.slice(0, 5).join(", ");
      more = total - Math.min(5, attendees.length);
      detail.push(shown + (more > 0 ? " 외 " + more + "명" : ""));
    }
    return detail.join("  ·  ");
  }

  function renderMeetings(body, doc, options) {
    var meetings = doc.meetings || [];
    var section = docSection(body, "일정", meetings.length);
    if (!meetings.length) {
      docLine(section, "briefing-doc-empty", "오늘 등록된 일정이 없습니다.");
      return;
    }
    meetings.forEach(function (item) {
      var label = item.title + (item.declined ? "  ·  불참 회신함" : "");
      var main = docRow(section, item.urgency, label, item.url, options,
        item.title + " 일정 준비사항을 알려줘", splitRange(item.time));
      var join;
      docLine(main, "briefing-doc-detail", eventDetail(item));
      docLine(main, "briefing-doc-prep", item.prep);
      if (safeUrl(item.conference_url || "")) {
        join = document.createElement("a");
        join.className = "briefing-doc-join";
        join.textContent = "화상 회의 참여";
        join.href = safeUrl(item.conference_url);
        join.target = "_blank";
        join.rel = "noopener noreferrer";
        main.appendChild(join);
      }
      if (item.ended) main.parentNode.classList.add("ended");
    });
  }

  function renderMailSection(body, doc, options, truncated) {
    var rows = doc.mail || [];
    /* ⚠️ 총 건수만 적으면 "몇 개나 안 봤나" 를 알 수 없다 — 둘 다 적는다 */
    var unread = rows.filter(function (row) { return row.unread; });
    var read = rows.filter(function (row) { return !row.unread; });
    var section = docSection(body, "메일", doc.mail_total || 0,
      unread.length ? "안읽음 " + unread.length : "");
    /* 목록이 길어지면 절 안에서 스크롤한다 — 다른 절을 아래로 밀지 않는다.
       (2026-08-26 사용자 지시: "카드 내 스크롤을 통해 공간 확보") */
    section.classList.add("briefing-doc-mail");
    var range = doc.window || {};

    docLine(section, "briefing-doc-window", range.label);
    docLine(section, "briefing-doc-summary", doc.mail_summary);
    if (!rows.length) {
      docLine(section, "briefing-doc-empty",
        doc.mail_total ? "요약할 만한 메일을 고르지 못했습니다." : "새로 온 메일이 없습니다.");
      return;
    }
    /* 안읽음을 **먼저, 따로** 놓는다 (2026-08-26 요청). 한 목록에 굵기로만
       구분하면 사이에 읽은 메일이 끼어 아직 볼 것이 몇 개인지 세어야 한다.
       ⚠️ 빈 칸은 만들지 않는다 — "안읽음 0" 은 알려주는 것이 없고 자리만 먹는다. */
    mailGroup(section, "안읽음", unread, options);
    /* ⛔ 자리가 모자라 잘렸으면 **잘렸다고 말한다.** 목록이 멀쩡히 보이면 그게
       전부인 줄 안다 — 조용히 자르는 것이 이 화면에서 가장 나쁜 실패다.
       ⚠️ 빠진 메일은 **화면 어디에도 없다.** 예전엔 아래 카드가 받았지만 이제
          문서가 전부 싣기 때문에, 넘친 것은 Gmail 에서만 볼 수 있다. */
    if (truncated) {
      docLine(section, "briefing-doc-empty",
        "수집 상한에 걸려 최근 메일만 가져왔습니다 — Gmail 에서 전체를 확인해 주세요.");
    }
    if (doc.mail_omitted_unread) {
      docLine(section, "briefing-doc-empty",
        "안 읽은 메일 " + doc.mail_omitted_unread
        + "건은 상한을 넘어 실리지 않았습니다 — Gmail 에서 확인해 주세요.");
    }
    mailGroup(section, "읽음", read, options);
  }

  function mailGroup(section, title, rows, options) {
    if (!rows.length) return;
    docLine(section, "briefing-doc-subhead", title + " " + rows.length);
    rows.forEach(function (item) {
      var main = docRow(section, item.urgency, item.subject, item.url,
        options, item.from + "의 " + item.subject + " 메일을 자세히 요약해줘",
        { start: item.at });
      /* 칸을 나눠도 행 표시는 남긴다 — 스크롤하다 중간부터 보면 어느 칸인지 모른다.
         ⚠️ 표시는 **행 클래스**로만 한다. 글자를 덧붙이면 좁은 칸에서 제목을 민다 */
      if (main.parentElement) {
        main.parentElement.classList.add(item.unread ? "mail-unread" : "mail-read");
      }
      docLine(main, "briefing-doc-detail", item.from);
      (item.points || []).forEach(function (point) {
        docLine(main, "briefing-doc-point", point);
      });
      docLine(main, "briefing-doc-request",
        item.request ? "회신 필요 — " + item.request : "");
    });
  }

  function renderActions(body, doc, options) {
    var rows = doc.actions || [];
    var section = docSection(body, "할 일", rows.length);
    if (!rows.length) {
      docLine(section, "briefing-doc-empty", "즉시 조치할 항목을 찾지 못했습니다.");
      return;
    }
    rows.forEach(function (item) {
      // 할 일 자체에는 시각이 없다. 근거가 도착한 시각을 쓰되,
      // ⚠️ 그냥 두면 '그 시각에 하는 일' 로 읽힌다 — 무엇에서 나왔는지 함께 적는다.
      var main = docRow(section, item.urgency, item.text, item.url, options, item.text,
        { start: item.at });
      docLine(main, "briefing-doc-detail", item.origin);
    });
  }

  function renderDeadlines(body, doc, options) {
    var rows = doc.deadlines || [];
    var section = docSection(body, "기한", rows.length);
    if (!rows.length) {
      docLine(section, "briefing-doc-empty", "기한이 확인된 항목이 없습니다.");
      return;
    }
    rows.forEach(function (item) {
      docRow(section, item.urgency, item.text, item.url, options, item.text,
        { start: item.label });
    });
  }

  /* ── 내가 저장한 보고 (설정) ───────────────────────────────────────────────
     매일 아침 자동으로 돌려 브리핑·잔디에 실을 질문을 **사용자가 직접** 고른다.
     ⛔ 채팅에서 저장하는 길만 두면 "무엇이 걸려 있는지" 를 볼 수 없다 — 매일 오는
        것을 스스로 바꿀 수 없으면 결국 안 보게 된다 (2026-08-31 사용자 요청).
     ⚠️ 이 파일은 브라우저 저장소를 쓰지 않는다 (구글 데이터를 다루는 파일 규칙).
        목록은 열 때마다 서버에서 받는다. */
  var SAVED_TITLE = "내가 저장한 보고";
  var SAVED_CADENCE = [
    { value: "daily", label: "매일 (근무일)" },
    { value: "weekly", label: "매주 월요일" },
    { value: "monthly", label: "매월 첫 근무일" }
  ];
  var savedManagerBox = null;

  function cadenceLabel(value, weekday) {
    var found = SAVED_CADENCE.filter(function (c) { return c.value === value; })[0];
    if (!found) return String(value || "");
    /* ⚠️ 주간은 요일을 함께 보여준다 — "매주" 만 적으면 언제 오는지 알 수 없다. */
    if (value === "weekly" && typeof weekday === "number") {
      return "매주 " + ["월", "화", "수", "목", "금", "토", "일"][weekday] + "요일";
    }
    return found.label;
  }

  function savedStatusText(row) {
    if (!row.last_run_at) return "아직 실행 전";
    var when = clockLabel(row.last_run_at);
    if (row.last_status === "error") return when + " · 실패";
    if (row.last_status === "empty") return when + " · 결과 없음";
    return when + " · 정상";
  }

  function openSavedManager(options) {
    var overlay = savedManagerBox;
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "fb-overlay saved-manager";
      /* ⛔ **이 파일은 HTML 문자열로 화면을 만들지 않는다.** 구글 데이터를 그리므로
         주입 경로를 아예 두지 않는 것이 규칙이고, 테스트가 소스를 훑어 강제한다
         (tests/frontend/test_personal_briefing_welcome.py).
         ⚠️ "지금은 상수만 넣으니 괜찮다" 가 위험하다 — 다음 사람이 그 틀에 변수를
            하나 끼워 넣는 순간 주입구가 된다. 처음부터 DOM 으로 짓는다.
         ⚠️ 금지된 속성 이름을 **주석에도 적지 마라** — 가드가 소스를 문자열로 보므로
            설명만으로도 걸린다 (오늘 CSS·정규식에서도 같은 함정을 밟았다). */
      var box = textNode("div", "fb-box saved-box", "");
      var addWrap = textNode("div", "saved-add", "");
      var actions = textNode("div", "fb-actions", "");
      var list = textNode("div", "saved-list", "");
      var note = textNode("div", "sq-note", "");
      var input = document.createElement("textarea");
      var select = document.createElement("select");
      var close = textNode("button", "fb-btn", "닫기");
      var add = textNode("button", "fb-btn fb-btn-primary", "추가");

      list.id = "saved-list";
      note.id = "saved-note";
      input.id = "saved-new";
      input.className = "fb-text";
      input.rows = 2;
      input.placeholder = "예: 쇼피 인도네시아 이번 달 매출 알려줘";
      select.id = "saved-new-cadence";
      select.className = "sq-select";
      close.id = "saved-close";
      close.type = "button";
      add.id = "saved-add";
      add.type = "button";

      box.appendChild(textNode("div", "fb-title", SAVED_TITLE));
      box.appendChild(textNode("div", "fb-sub",
        "여기 담아 두면 근무일 아침에 자동으로 돌려 브리핑과 잔디에 함께 보내 드립니다."));
      box.appendChild(list);
      addWrap.appendChild(input);
      addWrap.appendChild(select);
      box.appendChild(addWrap);
      box.appendChild(note);
      actions.appendChild(close);
      actions.appendChild(add);
      box.appendChild(actions);
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) overlay.style.display = "none";
      });
      SAVED_CADENCE.forEach(function (c) {
        var opt = document.createElement("option");
        opt.value = c.value;
        opt.textContent = c.label;
        select.appendChild(opt);
      });
      overlay.querySelector("#saved-close").addEventListener("click", function () {
        overlay.style.display = "none";
        /* 설정을 바꿨으면 첫 화면도 따라와야 한다 — 닫을 때 한 번 새로 그린다. */
        if (typeof options.reload === "function") options.reload();
      });
      overlay.querySelector("#saved-add").addEventListener("click", function () {
        savedAdd(overlay, options);
      });
      savedManagerBox = overlay;
    }
    overlay.style.display = "flex";
    overlay.querySelector("#saved-note").textContent = "";
    savedLoad(overlay, options);
  }

  function savedNote(overlay, text) {
    overlay.querySelector("#saved-note").textContent = text || "";
  }

  function savedLoad(overlay, options) {
    var list = overlay.querySelector("#saved-list");
    list.textContent = "불러오는 중…";
    fetch("/api/saved-questions")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var rows = (data && data.questions) || [];
        list.replaceChildren();
        if (!rows.length) {
          /* ⚠️ 빈 화면은 안내가 아니라 **다음 행동**을 준다. */
          list.appendChild(textNode("p", "briefing-doc-empty",
            "아직 담아 둔 보고가 없습니다. 아래에 질문을 적고 추가해 보세요."));
          return;
        }
        rows.forEach(function (row) { list.appendChild(savedRow(row, overlay, options)); });
      })
      .catch(function () {
        list.replaceChildren();
        list.appendChild(textNode("p", "briefing-doc-empty",
          "목록을 불러오지 못했습니다. 잠시 후 다시 열어 주세요."));
      });
  }

  function savedRow(row, overlay, options) {
    var item = textNode("div", "saved-item" + (row.enabled ? "" : " off"), "");
    var head = textNode("div", "saved-item-head", "");
    var meta = textNode("div", "saved-item-meta", "");
    var actions = textNode("div", "saved-item-actions", "");

    head.appendChild(textNode("span", "saved-item-q", row.question));
    meta.appendChild(textNode("span", "", cadenceLabel(row.cadence, row.weekday)));
    meta.appendChild(textNode("span", "saved-item-dot", "·"));
    meta.appendChild(textNode("span", "", savedStatusText(row)));
    /* ⛔ 실패 이유를 숨기지 마라 — 왜 안 오는지 모르면 고칠 수도 없다. */
    if (row.last_status === "error" && row.last_error) {
      meta.appendChild(textNode("span", "saved-item-dot", "·"));
      meta.appendChild(textNode("span", "saved-item-error", String(row.last_error).slice(0, 60)));
    }

    var toggle = textNode("button", "briefing-doc-action", row.enabled ? "중지" : "다시 켜기");
    toggle.type = "button";
    toggle.addEventListener("click", function () {
      savedPatch(row.id, !row.enabled, overlay, options);
    });
    var remove = textNode("button", "briefing-doc-action danger", "삭제");
    remove.type = "button";
    remove.addEventListener("click", function () {
      savedDelete(row.id, overlay, options);
    });
    actions.appendChild(toggle);
    actions.appendChild(remove);

    item.appendChild(head);
    item.appendChild(meta);
    item.appendChild(actions);
    return item;
  }

  /* 서버 응답은 거절을 `400 + {detail}` 로 준다 — 본문에 `ok` 가 없으므로
     **HTTP 상태를 먼저 본다** (2026-08-27 에 저장 실패를 성공이라 표시한 그 함정). */
  function savedRequest(url, init, overlay, options, okText) {
    return fetch(url, init)
      .then(function (r) {
        var httpOk = r.ok;
        return r.json().catch(function () { return {}; })
          .then(function (d) { return { httpOk: httpOk, data: d || {} }; });
      })
      .then(function (res) {
        if (!res.httpOk) {
          savedNote(overlay, res.data.detail || res.data.reason || "처리하지 못했습니다.");
          return false;
        }
        savedNote(overlay, okText);
        savedLoad(overlay, options);
        return true;
      })
      .catch(function () {
        savedNote(overlay, "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.");
        return false;
      });
  }

  function savedAdd(overlay, options) {
    var text = overlay.querySelector("#saved-new");
    var cadence = overlay.querySelector("#saved-new-cadence");
    var question = (text.value || "").trim();
    if (!question) { savedNote(overlay, "질문을 입력해 주세요."); return; }

    var payload = { question: question, cadence: cadence.value };
    /* ⚠️ 주간은 서버가 요일을 요구한다 — 화면 라벨이 약속한 월요일을 실어 보낸다. */
    if (cadence.value === "weekly") payload.weekday = 0;
    savedRequest("/api/saved-questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }, overlay, options, "추가했습니다.").then(function (ok) {
      if (ok) text.value = "";
    });
  }

  function savedPatch(id, enabled, overlay, options) {
    savedRequest("/api/saved-questions/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: enabled })
    }, overlay, options, enabled ? "다시 켰습니다." : "중지했습니다.");
  }

  function savedDelete(id, overlay, options) {
    savedRequest("/api/saved-questions/" + id, { method: "DELETE" },
      overlay, options, "삭제했습니다.");
  }

  function renderSaved(body, doc, options) {
    var rows = doc.saved || [];
    var section;

    /* ⛔ 저장 질문이 없으면 절 자체를 만들지 않는다. 빈 절은 첫 화면만 길게 만든다.
       ⚠️ 대신 **설정으로 들어갈 입구가 사라지면 안 된다** — 처음 쓰는 사람은 저장된
          것이 없어서 이 절을 볼 수 없다. 입구는 아래 `renderBusiness` 옆이 아니라
          지표 절 제목에 단다 (`openSavedManager`). */
    if (!rows.length) return;
    section = docSection(body, SAVED_TITLE, rows.length);
    sectionAction(section, "관리", function () { openSavedManager(options); });
    rows.forEach(function (item) {
      var main = docRow(section, "normal", item.question, "", options, item.question,
        { start: clockLabel(item.last_run_at) });
      var continuation;

      /* ⚠️ 서버가 300자로 줄이지만, 오래된 캐시도 전문을 화면에 밀어 넣지 못하게 한 번 더 자른다. */
      docLine(main, "briefing-doc-point", String(item.answer || "").slice(0, 300));
      continuation = textNode("button", "briefing-doc-join", "셀라에서 이어보기");
      continuation.type = "button";
      continuation.addEventListener("click", function () {
        putQuestionInInput(item.question, options);
      });
      main.appendChild(continuation);
    });
  }

  /* 업무 지표는 BigQuery 알림이라 문서의 숫자 검증을 거치지 않는다 —
     LLM 이 쓴 문장이 아니라 `briefing.py` 가 조회 결과로 만든 문장이다. */
  /* 무엇을 놓치고 있는지 말한다 — "연결하세요" 만으로는 왜 해야 하는지 알 수 없다. */
  function renderConnectPrompt(body, options) {
    var section = docSection(body, "오늘의 일정과 메일", null);
    var connect;

    docLine(section, "briefing-doc-empty",
      "Google Workspace를 연결하면 오늘 일정·받은 메일·할 일·기한이 여기에 함께 나옵니다.");
    connect = textNode("button", "briefing-doc-action primary", "Google 연결");
    connect.type = "button";
    connect.addEventListener("click", function () {
      if (typeof options.connect === "function") options.connect();
    });
    section.appendChild(connect);
  }

  /* 임시 열 두 개에 담긴 절을 하나의 그리드로 옮겨 **행을 맞춘다.**
     각 절에 열(1|2)과 행 번호를 직접 지정한다 — `align-items: start` 라서 한 행의
     두 칸은 같은 높이에서 시작하고, 행 높이는 둘 중 큰 쪽이 정한다.
     ⚠️ 왼쪽이 먼저 끝나면 남은 오른쪽 절은 자기 행을 계속 이어 간다. */
  function placeInGrid(columns, left, right) {
    var leftItems = Array.prototype.slice.call(left.children);
    var rightItems = Array.prototype.slice.call(right.children);
    var rows = Math.max(leftItems.length, rightItems.length);

    columns.classList.add("is-aligned");
    function place(node, side, row) {
      node.classList.add("briefing-doc-cell", "is-" + side);
      node.style.setProperty("--doc-row", String(row));
      columns.appendChild(node);
    }
    // DOM 순서는 왼쪽 → 오른쪽. 행 정렬은 CSS 의 grid-row 가 맡는다.
    leftItems.forEach(function (node, i) { place(node, "left", i + 1); });

    /* ⛔ 오른쪽 절마다 새 행을 만들지 마라 — 마지막 왼쪽 절(메일)이 길면 나머지가 그
       아래로 밀려 **문서가 통째로 길어지고 메일 옆이 텅 빈다** (2026-08-27 사용자
       지적: "메일 부분에 맞춰 저장한 질문과 지표가 칸에 맞아야 함").
       왼쪽 행 수까지는 한 행에 하나씩 세워 머리글을 맞추고, **남는 절은 마지막 행에
       함께 쌓아** 메일 옆을 채운다.
       ⚠️ 같은 행·같은 열에 둘을 그냥 두면 그리드는 쌓지 않고 **겹친다.** 반드시
          하나의 상자로 감싸서 그 안에서 세로로 흐르게 한다. */
    var lastRow = Math.max(leftItems.length, 1);
    rightItems.forEach(function (node, i) {
      if (i + 1 < lastRow) { place(node, "right", i + 1); }
    });

    var rest = rightItems.slice(Math.max(lastRow - 1, 0));
    if (rest.length === 1) {
      place(rest[0], "right", lastRow);
    } else if (rest.length > 1) {
      var stack = textNode("div", "briefing-doc-stack", "");
      rest.forEach(function (node) { stack.appendChild(node); });
      place(stack, "right", lastRow);
    }
    return rows;
  }

  function renderBusiness(body, data, options) {
    var rows = (data.business && data.business.items) || [];
    var section = docSection(body, "지표", null);

    if (!rows.length) {
      docLine(section, "briefing-doc-empty",
        statusOf(data.business) === "disabled"
          ? "업무 지표 자동 브리핑이 꺼져 있습니다."
          : "새로 확인할 업무 지표가 없습니다.");
      return;
    }
    /* 매출 한 줄 · 마케팅 한 줄 (2026-08-26 사용자 요청).
       ⚠️ 기준일이 서로 다를 수 있다 — 광고 적재가 매출보다 하루 빠르다.
          그래서 각자 자기 날짜를 시간축에 세운다. */
    rows.forEach(function (item) {
      var main = docRow(section, "normal", item.title || "업무 지표", "", options,
        item.follow_up || ((item.title || "업무 지표") + "에 대해 자세히 알려줘"),
        { start: shortDate(item.for_date) });
      String(item.body || "").split("\n").forEach(function (line) {
        docLine(main, "briefing-doc-point", line.replace(/^·\s*/, "").trim());
      });
    });
  }

  function clockLabel(value) {
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "");
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", hour: "numeric", minute: "2-digit"
    }).format(date);
  }

  function longDate(doc) {
    var parts = String(doc.for_date || "").split("-");
    var weekday = doc.weekday ? doc.weekday + "요일" : "";
    if (parts.length !== 3) return weekday || "오늘";
    return Number(parts[1]) + "월 " + Number(parts[2]) + "일 " + weekday;
  }

  /* 환율. ⛔ 값이 없으면 섹션 자체를 만들지 않는다 — 빈 칸이 0원처럼 읽힌다.
     ⚠️ 주말·공휴일엔 새 고시가 없다. 그때는 며칠 전 값인지 밝힌다. */
  function renderFx(body, data) {
    var fx = data.fx || {};
    var items = fx.items || [];
    var section;
    var note;

    var list;

    if (!items.length) return;
    /* ⛔ 통화마다 한 줄씩 쌓지 마라 — 9종이면 아홉 줄이 되고 오른쪽이 텅 빈다
       (2026-08-26 사용자 지적). 환율은 시간축이 필요 없는 유일한 절이라
       **문서 아래 전체 폭**에서 가로로 흐르게 한다. */
    section = docSection(body, "환율", null);
    section.className = "briefing-doc-section briefing-doc-fx";
    /* ⚠️ 무엇과 견준 변동률인지 밝힌다. 퍼센트만 있으면 전일대비로 읽힌다 */
    note = fx.for_date + " 기준" + (fx.stale_days > 0 ? " · " + fx.stale_days + "일 전 고시" : "");
    // ⛔ 여기서 "전월대비" 를 조립하지 마라 — 실제로 한 달 전과 견줬는지는
    //    서버만 안다 (보유일이 드물면 몇 주 전과 견준다). 서버가 준 문구를 쓴다.
    if (fx.basis_note) note += " · " + fx.basis_note;
    docLine(section, "briefing-doc-window", note);

    list = textNode("div", "briefing-doc-fx-list", "");
    items.forEach(function (item) {
      var cell = textNode("div", "briefing-doc-fx-item", "");
      var unit = item.unit && item.unit !== 1 ? "(" + item.unit + ")" : "";
      var change = fxChange(item.change_pct);

      cell.appendChild(textNode("span", "briefing-doc-fx-name", item.currency + unit));
      /* 변동률만 있으면 '얼마에서 얼마로' 가 안 보인다 (2026-08-26 사용자 요청).
         ⚠️ 반올림 후 두 값이 같으면 화살표를 쓰지 않는다 — `1,614 → 1,614원` 은 소음이다. */
      if (item.was_krw !== null && item.was_krw !== undefined
          && fxAmount(item.was_krw) !== fxAmount(item.krw)) {
        cell.appendChild(textNode("span", "briefing-doc-fx-was", fxAmount(item.was_krw)));
        cell.appendChild(textNode("span", "briefing-doc-fx-arrow", "→"));
      }
      cell.appendChild(textNode("span", "briefing-doc-fx-value", fxAmount(item.krw) + "원"));
      if (change) {
        cell.appendChild(textNode("span", "briefing-doc-fx-change " + change.tone, change.text));
      }
      list.appendChild(cell);
    });
    section.appendChild(list);
  }

  function fxAmount(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "";
    return n >= 100
      ? n.toLocaleString("ko-KR", { maximumFractionDigits: 0 })
      : n.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* ⛔ 값이 없을 때 0% 라고 쓰지 마라 — 안 움직인 것과 모르는 것은 다르다. */
  function fxChange(pct) {
    if (pct === null || pct === undefined || !Number.isFinite(Number(pct))) return null;
    var n = Number(pct);
    if (n > 0) return { text: "▲ " + n.toFixed(2) + "%", tone: "up" };
    if (n < 0) return { text: "▼ " + Math.abs(n).toFixed(2) + "%", tone: "down" };
    return { text: "보합", tone: "flat" };
  }

  function shortDate(value) {
    var parts = String(value || "").split("-");
    return parts.length === 3 ? Number(parts[1]) + "/" + Number(parts[2]) : "";
  }

  /* 접었을 때도 긴급한 것은 보여야 한다 — 숫자만 남기면 무엇이 급한지 알 수 없다. */
  function urgentTitles(doc) {
    var titles = [];
    [doc.meetings, doc.deadlines, doc.actions, doc.mail].forEach(function (rows) {
      (rows || []).forEach(function (row) {
        if (row.urgency !== "high") return;
        if (row.time) titles.push(row.time.split("~")[0] + " " + row.title);
        else if (row.label) titles.push("[" + row.label + "] " + row.text);
        else titles.push(row.text || row.subject || row.title || "");
      });
    });
    return titles.filter(Boolean);
  }

  function docFooter(doc, options) {
    var footer = document.createElement("div");
    var copy = textNode("button", "briefing-doc-action", "본문 복사");
    var jandi = textNode("button", "briefing-doc-action", "잔디로 받기");

    footer.className = "briefing-doc-footer";
    copy.type = "button";
    copy.addEventListener("click", function () {
      if (!doc.markdown || !navigator.clipboard) return;
      navigator.clipboard.writeText(doc.markdown).then(function () {
        copy.textContent = "복사됨";
        window.setTimeout(function () { copy.textContent = "본문 복사"; }, 1500);
      }, function () {});
    });
    jandi.type = "button";
    jandi.addEventListener("click", function () {
      openJandiDialog(options);
    });
    /* ⚠️ **저장된 것이 없어도 들어갈 수 있어야 한다.** 위 절은 비면 그려지지 않으므로
       처음 쓰는 사람에게는 입구가 사라진다. 잔디 설정 옆에 두면 "매일 무엇을 받을지"
       를 정하는 두 가지가 한자리에 모인다. */
    var manage = textNode("button", "briefing-doc-action", SAVED_TITLE + " 설정");
    manage.type = "button";
    manage.addEventListener("click", function () { openSavedManager(options); });

    footer.appendChild(copy);
    footer.appendChild(manage);
    footer.appendChild(jandi);
    return footer;
  }

  function renderDocument(root, data, options) {
    var doc = data.document || {};
    var node = docRoot(root);
    var open = !docCollapsed;
    var head;
    var body;
    var strip = null;
    var urgent;
    var heading;
    var columns;
    var left;
    var right;

    var connected = doc.status !== "disconnected";

    node.replaceChildren();
    if (!doc.status) {
      node.hidden = true;
      return null;
    }
    /* ⛔ 미연결이라고 문서를 통째로 숨기지 마라 — 첫 화면에 "연결하세요" 버튼 하나만
       남아 다시 오지 않는다. 지표·환율은 **구글과 무관한 데이터**다 (2026-08-26). */
    node.hidden = false;

    head = document.createElement("button");
    head.className = "briefing-doc-head";
    head.type = "button";
    head.setAttribute("aria-expanded", open ? "true" : "false");
    // 바깥 제목이 이미 'Today' 다 — 여기서 또 '오늘의 브리핑' 이라고 하지 않는다.
    heading = textNode("span", "briefing-doc-title", "");
    heading.appendChild(textNode("span", "briefing-doc-date", longDate(doc)));
    head.appendChild(heading);
    head.appendChild(statNode(connected ? statLine(doc) : []));
    // 화살표 글자 대신 CSS 로 그린다 — 글자는 폰트마다 크기와 무게가 달라진다.
    head.appendChild(textNode("span", "briefing-doc-caret", ""));
    node.appendChild(head);

    urgent = urgentTitles(doc);
    if (urgent.length) {
      strip = textNode("p", "briefing-doc-urgent", "");
      strip.appendChild(textNode("span", "briefing-doc-urgent-label", "긴급"));
      strip.appendChild(textNode("span", "briefing-doc-urgent-list",
        urgent.slice(0, 2).join("  ·  ")
        + (urgent.length > 2 ? "  외 " + (urgent.length - 2) + "건" : "")));
      strip.hidden = open;
      node.appendChild(strip);
    }

    body = document.createElement("div");
    body.className = "briefing-doc-body";
    body.hidden = !open;
    node.appendChild(body);

    head.addEventListener("click", function () {
      var next = body.hidden;
      body.hidden = !next;
      // 펼치면 본문에 다 있으므로 급한 것 요약은 접었을 때만 남긴다.
      if (strip) strip.hidden = next;
      head.setAttribute("aria-expanded", next ? "true" : "false");
      docCollapsed = !next;
    });

    if (doc.status === "error") {
      docLine(body, "briefing-doc-empty",
        "브리핑 문장을 만들지 못했습니다. 아래 카드의 원본 목록은 그대로입니다.");
    }

    /* 두 열로 나눈다 — 왼쪽은 오늘 '일어나는' 것(일정·메일), 오른쪽은 '해야 할' 것과 참고.
       세로로만 쌓으면 넓은 화면에서 오른쪽이 통째로 비고 스크롤만 길어진다.
       ⚠️ 좁은 화면에서는 한 열로 접히고, 그때 순서는 DOM 순서 그대로다. */
    if (!connected) {
      // 일정·메일은 못 보여주지만 지표·환율은 보여준다. 연결하면 무엇이 더 생기는지도 말한다.
      renderConnectPrompt(body, options);
      renderSaved(body, doc, options);
      renderBusiness(body, data, options);
      renderFx(body, data);
      body.appendChild(docFooter(doc, options));
      return null;
    }

    columns = textNode("div", "briefing-doc-columns", "");
    left = textNode("div", "briefing-doc-col", "");
    right = textNode("div", "briefing-doc-col", "");
    renderMeetings(left, doc, options);
    /* ⚠️ 수집이 상한에 걸렸으면 그 사실은 **목록 옆**에 있어야 한다 —
       카드로 밀어 두면 목록만 보고 그게 전부인 줄 안다 */
    renderMailSection(left, doc, options, data.mail && data.mail.truncated);
    renderActions(right, doc, options);
    renderDeadlines(right, doc, options);
    renderSaved(right, doc, options);
    renderBusiness(right, data, options);
    /* 두 열의 **행 머리글을 같은 높이에 세운다** (2026-08-27 사용자 지시).
       예전엔 열마다 따로 쌓아서 1행(일정/할 일)만 맞고 2행부터 어긋났다 —
       오른쪽 절이 짧아 먼저 올라가기 때문이다.
       ⚠️ DOM 순서는 **왼쪽 전부 → 오른쪽 전부** 로 둔다. 좁은 화면에서는 배치를 걸지
          않아 DOM 순서대로 한 줄로 쌓이는데, 섞어 두면 일정 → 할 일 → 메일 순으로
          읽혀 흐름이 깨진다. 넓은 화면의 행 정렬은 grid-row 가 따로 잡는다. */
    placeInGrid(columns, left, right);
    body.appendChild(columns);
    // 환율만 두 열 밖에 둔다 — 통화가 가로로 흐르려면 전체 폭이 필요하다.
    renderFx(body, data);

    if (doc.dropped) {
      docLine(body, "briefing-doc-note",
        "※ 근거(원문 숫자·메일)가 확인되지 않아 제외한 문장 " + doc.dropped + "건");
    }
    body.appendChild(docFooter(doc, options));
    return right;
  }

  /* 잔디 설정. 서버는 잔디에 직접 붙지 못하므로 여기서 등록한 주소로 DB_PC 릴레이가 보낸다. */
  function jandiHelp(box) {
    var help = textNode("ol", "briefing-jandi-help", "");
    [
      "잔디에서 브리핑을 받을 토픽을 하나 만듭니다 (본인만 있는 토픽을 권장합니다).",
      "토픽 우측 상단 ⋮ → 커넥트 → 인커밍 웹훅 → 만들기.",
      "발급된 https://wh.jandi.com/connect-api/webhook/… 주소를 아래에 붙여 넣습니다.",
      "매일 아침 9시에 만들어진 브리핑이 그 토픽으로 전달됩니다."
    ].forEach(function (step) {
      help.appendChild(textNode("li", "", step));
    });
    box.appendChild(help);
  }

  function openJandiDialog(options) {
    var overlay = document.createElement("div");
    var box = document.createElement("div");
    var input = document.createElement("input");
    var status = textNode("p", "briefing-jandi-status", "불러오는 중…");
    var row = document.createElement("div");
    var save = textNode("button", "briefing-doc-action primary", "저장");
    var test = textNode("button", "briefing-doc-action", "지금 대기열에 넣기");
    var remove = textNode("button", "briefing-doc-action danger", "해제");
    var close = textNode("button", "briefing-doc-action", "닫기");

    overlay.className = "briefing-jandi-overlay";
    box.className = "briefing-jandi-box";
    box.appendChild(textNode("h3", "briefing-jandi-title", "잔디로 출근 브리핑 받기"));
    jandiHelp(box);
    input.type = "url";
    input.className = "briefing-jandi-input";
    input.placeholder = "https://wh.jandi.com/connect-api/webhook/…";
    input.spellcheck = false;
    box.appendChild(input);
    box.appendChild(status);
    row.className = "briefing-jandi-actions";
    [save, test, remove, close].forEach(function (button) {
      button.type = "button";
      row.appendChild(button);
    });
    box.appendChild(row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function shut() {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    }

    function onKey(event) {
      if (event.key === "Escape") shut();
    }

    function paint(state) {
      if (!state || !state.registered) {
        status.textContent = "아직 등록된 잔디 토픽이 없습니다.";
        return;
      }
      status.textContent = "등록됨 " + state.masked
        + (state.last_sent_at ? " · 마지막 발송 " + state.last_sent_at : " · 아직 발송 이력 없음")
        + (state.last_error ? " · 최근 오류: " + state.last_error : "");
    }

    async function call(method, payload) {
      var response = await options.fetchImpl("/api/personal-briefing/jandi", payload
        ? { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }
        : { method: method });
      var value = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(value.detail || "요청이 실패했습니다.");
      return value;
    }

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) shut();
    });
    document.addEventListener("keydown", onKey);
    close.addEventListener("click", shut);

    save.addEventListener("click", async function () {
      status.textContent = "저장 중…";
      try {
        paint(await call("PUT", { webhook_url: input.value.trim(), enabled: true }));
        input.value = "";
      } catch (error) {
        status.textContent = error.message;
      }
    });

    remove.addEventListener("click", async function () {
      status.textContent = "해제 중…";
      try {
        paint(await call("DELETE"));
      } catch (error) {
        status.textContent = error.message;
      }
    });

    test.addEventListener("click", async function () {
      status.textContent = "대기열에 넣는 중…";
      try {
        var response = await options.fetchImpl("/api/personal-briefing/jandi/test", { method: "POST" });
        var value = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(value.detail || "요청이 실패했습니다.");
        status.textContent = value.queued
          ? "대기열에 넣었습니다. 릴레이가 도는 시각에 잔디로 전달됩니다."
          : "오늘 몫은 이미 대기열에 있습니다.";
      } catch (error) {
        status.textContent = error.message;
      }
    });

    call("GET").then(paint, function () {
      status.textContent = "설정을 불러오지 못했습니다.";
    });
    input.focus();
  }

  function renderSkeleton(root) {
    var grid = root.querySelector(".personal-briefing-grid");
    // ⚠️ 실제로 그릴 카드와 이름이 같아야 한다 — 다르면 로딩 순간에 없는 카드를 약속한다.
    /* ⚠️ 스켈레톤은 **실제로 뜰 카드만** 약속한다. 메일 카드는 문서가 비었거나
       연결이 끊겼을 때만 뜨므로(평소엔 없다) 자리를 미리 잡아 두면 빈 칸이 남는다. */
    var titles = ["향후 일정"];

    root.hidden = false;
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "personal-briefing-grid";
      grid.id = "personal-briefing-grid";
      root.appendChild(grid);
    }
    grid.replaceChildren();
    grid.hidden = false;
    grid.classList.remove("single");
    docRoot(root).hidden = true;
    titles.forEach(function (title) {
      var card = makeCard(title, { status: "loading" });
      card.appendChild(textNode("div", "personal-briefing-skeleton", ""));
      grid.appendChild(card);
    });
  }

  /* 카드는 **문서가 다루지 않는 것**만 맡는다 (2026-08-26).
     ⛔ 예전엔 `오늘 우선 확인`·`오늘 메일`·`업무 지표` 카드가 문서와 같은 내용을 다시 그렸다 —
        한 화면에 요약이 세 겹이었고, 메일 요약 문장은 글자 그대로 두 번 나왔다.
        같은 사실을 두 곳에서 그리면 언젠가 서로 다른 말을 한다 (프롬프트 사본 사고와 같은 부류). */
  function renderCards(grid, data, options) {
    var visible = 0;
    var calendar;
    var mail;
    var calendarItems;
    var mailItems;
    var docMail;
    var disconnected = statusOf(data.calendar) === "disconnected"
      || statusOf(data.mail) === "disconnected"
      || (data.google && data.google.connected === false);

    // 오늘 일정은 문서가 참석자·장소·준비사항까지 보여준다. 카드는 그 다음날부터.
    calendarItems = safeItems(data.calendar).filter(function (item) {
      return !isSameKstDay(item.start, data.for_date);
    });
    if (calendarItems.length || disconnected) {
      calendar = makeCard("향후 일정", data.calendar || {}, calendarItems.length);
      // Today 의 일정과 같은 어법이다 — 날짜/시각이 왼쪽 축에 서고 제목이 본문이 된다.
      calendarItems.forEach(function (item) {
        var main = docRow(calendar, "normal", item.title || "(제목 없음)", item.url, options,
          (item.title || "일정") + " 일정 준비사항을 알려줘",
          { start: formatKstDate(item.start), end: formatKstTime(item) });
        docLine(main, "briefing-doc-detail", eventDetail(item));
        if (item.ended) main.parentNode.classList.add("ended");
      });
      if (!calendarItems.length) {
        appendEmptyMessage(calendar, "Google Workspace를 연결하면 일정이 표시됩니다.");
      }
      if (data.calendar && data.calendar.truncated) {
        calendar.appendChild(textNode("p", "personal-briefing-note", "50건 이상 · Google Calendar에서 전체 보기"));
      }
      grid.appendChild(calendar);
      visible += 1;
    }

    /* ⛔ **메일 목록을 두 벌 두지 않는다.** 예전엔 문서가 요약 있는 것만 싣고
       나머지를 이 카드가 받았다 — 한 화면에 목록이 두 개라 어느 쪽이 전부인지
       알 수 없었다 (2026-08-26). 이제 Today 의 `메일` 절이 받은 메일을 전부
       싣고 안에서 스크롤한다.
       ⚠️ 그렇다고 카드를 지우면 **문서가 비었을 때 메일이 통째로 사라진다** —
          문서 생성이 실패하거나 시간이 초과돼도 `data.mail` 에는 목록이 남는다.
          그래서 이 카드는 **문서가 메일을 못 실었을 때의 안전망**으로만 남긴다. */
    docMail = (data.document && data.document.mail) || [];
    mailItems = docMail.length ? [] : safeItems(data.mail);
    /* 안 읽은 것을 위로 — 첫 화면 어디서든 같은 순서여야 한다.
       ⚠️ 뒤집지 말고 **안정 정렬**로 옮긴다 (같은 그룹 안에서는 도착순). */
    mailItems = mailItems.filter(function (item) { return item.unread; })
      .concat(mailItems.filter(function (item) { return !item.unread; }));
    if (mailItems.length || disconnected) {
      mail = makeCard(docMail.length ? "메일 연결" : "받은 메일",
        data.mail || {}, mailItems.length || null);
      mailItems.forEach(function (item) {
        var main = docRow(mail, "normal", item.subject || "(제목 없음)", item.url, options,
          (item.from_display || "보낸 사람") + "의 " + (item.subject || "메일") + "을 자세히 요약해줘",
          {
            start: formatKstTime({ start: item.received_at }),
            end: isSameKstDay(item.received_at, data.for_date)
              ? "" : formatKstDate(item.received_at)
          });
        if (main.parentElement) {
          main.parentElement.classList.add(item.unread ? "mail-unread" : "mail-read");
        }
        docLine(main, "briefing-doc-detail", item.from_display);
      });
      if (!mailItems.length) {
        appendEmptyMessage(mail, "Google Workspace를 연결하면 받은 메일이 표시됩니다.");
      }
      if (disconnected || (data.mail && data.mail.error_code === "oauth_expired")) {
        var connect = textNode("button", "personal-briefing-connect", "Google 연결");
        connect.type = "button";
        connect.addEventListener("click", function () {
          if (typeof options.connect === "function") options.connect();
        });
        mail.appendChild(connect);
      }
      grid.appendChild(mail);
      visible += 1;
    }

    // 한 장만 남으면 2열 그리드에서 왼쪽에 붙어 잘린 것처럼 보인다.
    grid.classList.toggle("single", visible === 1);
    grid.hidden = visible === 0;
  }

  function render(root, data, options) {
    var grid;
    var updated;
    var column;

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
    grid.hidden = false;
    /* ⚠️ 참고 섹션(향후 일정·메일)을 문서 오른쪽 열로 올려 봤다가 되돌렸다:
       오른쪽이 과적재돼 그쪽이 높이를 결정하면서 963 → 1046px 로 **늘었다**.
       오른쪽 열의 여백은 낭비가 아니라 숨 쉴 자리다. 실측하지 않았으면 반대로 갔다. */
    renderDocument(root, data, options);
    if ((data.document || {}).status === "disconnected") {
      // 문서가 이미 연결 안내를 보여줬다 — 카드에서 또 하면 버튼이 두 곳이 된다.
      grid.hidden = true;
    } else {
      renderCards(grid, data, options);
    }

    updated = root.querySelector(".personal-briefing-updated") || document.getElementById("personal-briefing-updated");
    if (updated) {
      updated.textContent = data.generated_at
        ? clockLabel(data.generated_at) + " 기준"
        : "저장된 정보 없음";
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

  /* 새로고침 중/실패를 **버튼과 문구 양쪽**에서 말한다.
     ⛔ 스피너만 돌리고 끝내면 실패했을 때 아무 말도 안 남는다. */
  function refreshButton(root) {
    return (root && root.querySelector(".personal-briefing-refresh"))
      || document.getElementById("personal-briefing-refresh");
  }

  function markRefreshing(root, busy) {
    var button = refreshButton(root);
    if (!button) return;
    button.disabled = !!busy;
    button.classList.toggle("is-busy", !!busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
    if (busy) setUpdatedLabel(root, "새로 불러오는 중…");
  }

  function markRefreshFailed(root) {
    setUpdatedLabel(root, "새로 불러오지 못했습니다 — 잠시 후 다시 눌러 주세요");
  }

  function setUpdatedLabel(root, text) {
    var node = (root && root.querySelector(".personal-briefing-updated"))
      || document.getElementById("personal-briefing-updated");
    if (node) node.textContent = text;
  }

  function create(options) {
    var root = options.root;
    var state = null;
    var requestGeneration = 0;
    var refreshing = false;

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

    /* 사용자가 직접 누르는 새로고침. ⛔ `load()` 는 서버가 "갱신이 필요하다"
       (`needs_refresh`) 고 할 때만 새로 가져온다 — 눌러도 캐시가 그대로 보이면
       버튼이 고장 난 것처럼 보인다. 여기서는 **무조건** 새로 가져온다.
       ⚠️ 진행 중 다시 눌리지 않게 막는다. 구글 호출이 여러 번 겹치면 느려진다. */
    async function refreshNow() {
      var generation;
      var response;
      if (refreshing) return;
      refreshing = true;
      generation = ++requestGeneration;
      markRefreshing(root, true);
      try {
        /* ⛔ `force=1` 이 없으면 서버가 캐시(10분)를 그대로 돌려준다 — 눌러도
           시각이 그대로여서 버튼이 고장 난 것처럼 보였다 (2026-08-26 제보) */
        response = await options.fetchImpl("/api/personal-briefing/refresh?force=1",
          { method: "POST" });
        if (generation !== requestGeneration) return;
        if (!response.ok) throw new Error("briefing refresh failed");
        state = mergeTimeoutEnvelope(state, await response.json());
        if (generation !== requestGeneration) return;
        notifyGoogleState(options, state);
        render(root, state, options);
      } catch (_error) {
        /* ⛔ 실패를 조용히 넘기지 않는다 — 누른 사람은 새로 받은 줄 안다 */
        if (generation === requestGeneration) markRefreshFailed(root);
      } finally {
        refreshing = false;
        markRefreshing(root, false);
      }
    }

    /* 설정 화면이 무언가를 바꾸면 첫 화면도 따라와야 한다. 렌더 함수들은 `options`
       하나만 들고 다니므로 거기에 다시 그릴 방법을 열어 둔다.
       ⚠️ 서버 재조회가 아니라 **저장된 것을 다시 읽는** 것이다 — 설정 한 번에
          구글·LLM 을 다시 돌리면 느리고 비싸다. */
    options.reload = load;

    return {
      load: load,
      refresh: refreshNow,
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
