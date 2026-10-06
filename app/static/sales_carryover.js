/* 매출 이월 확인 — 두 스냅샷 날짜를 골라 거래처별 차이를 팀별로 본다.
 *
 * 숫자는 서버(코드)가 만든 것을 그대로 그린다. 여기서 다시 계산하지 않는다 —
 * 두 곳에서 계산하면 언젠가 서로 다른 값이 된다. 화면은 백만원 단위로 줄여 보이고
 * 정확한 원 값은 title(마우스 오버)과 CSV 에 남긴다.
 * ⛔ 날짜는 서버가 준 목록에서만 고른다. 없는 날짜의 테이블은 존재하지 않는다.
 * ⛔ 열은 9개로 고정이다 (colgroup 이 폭을 나눈다). 열을 더하면 가로 스크롤이 되살아난다 —
 *    새 값은 열이 아니라 title 이나 CSV 로 넣는다.
 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var selA = $("sc-a"), selB = $("sc-b"), inMonth = $("sc-month");
  var btnRun = $("sc-run"), btnCsv = $("sc-csv"), onlyDrop = $("sc-only-drop"), teamsOnly = $("sc-teams-only");
  var elErr = $("sc-error"), elNotice = $("sc-notice"), elSum = $("sc-summary");
  var table = $("sc-table"), tbody = $("sc-body");
  var COLS = 9;
  var dates = [];
  var result = null;
  var sortKey = null, sortDir = 1;
  var openTeams = {};   // "팀 합계만 보기" 상태에서 사용자가 펼친 팀

  function fmtDate(d) { return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6); }
  function fmtWon(n) {
    if (n === null || n === undefined) return "";
    return Math.round(n).toLocaleString("ko-KR") + "원";
  }
  // 원 단위, 천 단위 구분. 칸에는 "원" 을 붙이지 않는다 (요약 줄이 단위를 말한다)
  function fmtNum(n) {
    if (n === null || n === undefined) return "";
    return Math.round(n).toLocaleString("ko-KR");
  }
  // 증감: 부호 + 숫자. 색은 CSS 클래스가 붙인다 (화살표는 쓰지 않는다 — 2026-09-23 사용자 결정)
  function arrow(n) { return n > 0 ? "+" : (n < 0 ? "-" : ""); }
  function arrowClass(n) { return n > 0 ? "sc-up" : (n < 0 ? "sc-down" : "sc-zero"); }
  function fmtDelta(n) { return (n === null || n === undefined) ? "" : arrow(n) + fmtNum(Math.abs(n)); }
  function fmtPct(p) { return (p === null || p === undefined) ? "" : arrow(p) + Math.abs(p).toFixed(1) + "%"; }
  function signedM(n) { return arrow(n) + fmtNum(Math.abs(n)); }
  function statusClass(s) {
    return { "사라짐": "st-gone", "감소": "st-down", "동일": "st-same", "증가": "st-up", "신규": "st-new" }[s] || "";
  }

  function setError(msg) { elErr.textContent = msg || ""; }

  function fillSelect(sel, list, chosen) {
    sel.innerHTML = "";
    list.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d; o.textContent = fmtDate(d);
      if (d === chosen) o.selected = true;
      sel.appendChild(o);
    });
  }

  function defaultMonthFor(d) { return d.slice(0, 4) + "-" + d.slice(4, 6); }

  function loadSnapshots() {
    setError("");
    return fetch("/api/sales-carryover/snapshots", { credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) { setError(res.body.detail || "백업 날짜 목록을 읽지 못했습니다."); return; }
        dates = res.body.dates || [];
        if (!dates.length) { setError("고를 수 있는 백업 날짜가 없습니다."); return; }
        var last = dates[dates.length - 1];
        var prev = dates.length > 1 ? dates[dates.length - 2] : last;
        fillSelect(selA, dates, prev);
        fillSelect(selB, dates, last);
        inMonth.value = defaultMonthFor(prev);
        btnRun.disabled = false;
      })
      .catch(function () { setError("백업 날짜 목록을 읽지 못했습니다 (네트워크)."); });
  }

  selA.addEventListener("change", function () { inMonth.value = defaultMonthFor(selA.value); });

  function run() {
    var a = selA.value, b = selB.value, month = inMonth.value;
    if (!a || !b) return;
    setError(""); elNotice.textContent = ""; elSum.textContent = "조회 중…";
    btnRun.disabled = true; btnCsv.disabled = true; table.hidden = true;
    var qs = "a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b) +
             (month ? "&month=" + encodeURIComponent(month) : "");
    fetch("/api/sales-carryover/compare?" + qs, { credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        btnRun.disabled = false;
        if (!res.ok) { elSum.textContent = ""; setError(res.body.detail || "조회에 실패했습니다."); return; }
        result = res.body;
        sortKey = null;
        render();
        btnCsv.disabled = !(result.rows && result.rows.length);
      })
      .catch(function () { btnRun.disabled = false; elSum.textContent = ""; setError("조회에 실패했습니다 (네트워크)."); });
  }

  function visibleRows() {
    var rows = (result && result.rows) ? result.rows.slice() : [];
    if (onlyDrop.checked) rows = rows.filter(function (r) { return r.status === "사라짐" || r.status === "감소"; });
    if (sortKey) {
      rows.sort(function (x, y) {
        var p = x[sortKey], q = y[sortKey];
        if (p === null || p === undefined) p = -Infinity;
        if (q === null || q === undefined) q = -Infinity;
        if (typeof p === "string") return p.localeCompare(q, "ko") * sortDir;
        return (p - q) * sortDir;
      });
    }
    return rows;
  }

  function cell(tr, cls, text, title) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    if (title) td.title = title;
    tr.appendChild(td);
    return td;
  }
  function money(tr, n, signed) {
    if (signed) return cell(tr, "sc-num " + arrowClass(n), fmtDelta(n), fmtWon(n));
    return cell(tr, "sc-num", fmtNum(n), fmtWon(n));
  }
  function pct(tr, p) {
    return cell(tr, "sc-num " + ((p === null || p === undefined) ? "" : arrowClass(p)), fmtPct(p));
  }

  // 팀 머리줄 / 전체 합계 줄 — 서버가 계산한 합계를 그대로 그린다. 앞 세 칸을 합쳐 이름과 건수를 적는다
  function sumRow(cls, label, g) {
    var tr = document.createElement("tr");
    tr.className = cls;
    var head = cell(tr, "sc-left sc-wrap", "");
    head.colSpan = 3;
    head.textContent = label + " · 거래처 " + g.companies + "곳";
    var sub = document.createElement("span");
    sub.className = "sc-sub";
    sub.textContent = "  사라짐 " + g.gone + " · 감소 " + g.decreased + " · 증가 " + g.increased + " · 신규 " + g.new;
    head.appendChild(sub);
    money(tr, g.s_a); money(tr, g.s_b); money(tr, g.diff, true);
    pct(tr, g.pct);
    cell(tr, "sc-num", g.n_a + "→" + g.n_b, "매출 행 수 A→B");
    money(tr, g.next_diff, true);
    return tr;
  }

  function companyRow(r) {
    var tr = document.createElement("tr");
    tr.className = "sc-row";
    var name = cell(tr, "sc-left sc-company sc-wrap", r.company);
    // 팀이 둘 이상인 거래처만 두 번째 팀부터 작게 덧붙인다 (첫 팀은 팀 줄이 말한다)
    var teams = (r.team || "").split(", ").filter(Boolean);
    if (teams.length > 1) {
      var extra = document.createElement("span");
      extra.className = "sc-sub";
      extra.textContent = " (+" + teams.slice(1).join(", ") + ")";
      name.appendChild(extra);
    }
    cell(tr, "sc-left sc-wrap", r.manager || "");
    cell(tr, "sc-left " + statusClass(r.status), r.status);
    money(tr, r.s_a); money(tr, r.s_b); money(tr, r.diff, true);
    pct(tr, r.pct);
    cell(tr, "sc-num", r.n_a + "→" + r.n_b, "매출 행 수 A→B (주문×SKU 줄)");
    money(tr, r.next_diff, true);
    return tr;
  }

  function render() {
    if (!result) return;
    var s = result.summary;
    // 공시는 표보다 먼저 — 사람은 표를 보지 각주를 안 본다
    elNotice.textContent = (result.notices || []).join("  ");
    elSum.innerHTML =
      "<b>" + fmtDate(result.date_a) + "</b> → <b>" + fmtDate(result.date_b) + "</b> 스냅샷 · 대상월 <b>" +
      result.month + "</b> (다음 달 " + result.next_month + ") · 거래처 " + s.companies + "곳 · " +
      "합계 " + fmtNum(s.total_a) + " → " + fmtNum(s.total_b) + "원 (<span class=\"" + arrowClass(s.diff) + "\">" + signedM(s.diff) + "</span>) · " +
      "사라짐 " + s.gone + " · 감소 " + s.decreased + " · 증가 " + s.increased + " · 신규 " + s.new +
      (s.truncated ? " · 표에서 뺀 거래처 " + s.truncated : "");
    var rows = visibleRows();
    var byTeam = {};
    rows.forEach(function (r) {
      var k = r.primary_team || "(팀 없음)";
      (byTeam[k] = byTeam[k] || []).push(r);
    });
    tbody.innerHTML = "";
    (result.teams || []).forEach(function (g) {
      var members = byTeam[g.team] || [];
      var collapsed = teamsOnly.checked && !openTeams[g.team];
      var caret = teamsOnly.checked ? (collapsed ? "▸ " : "▾ ") : "";
      var head = sumRow("sc-team", caret + g.team, g);
      head.title = "누르면 이 팀을 접거나 펼칩니다";
      head.addEventListener("click", function () {
        if (!teamsOnly.checked) { teamsOnly.checked = true; openTeams = {}; openTeams[g.team] = true; }
        else openTeams[g.team] = !openTeams[g.team];
        render();
      });
      tbody.appendChild(head);
      if (collapsed) return;
      members.forEach(function (r) { tbody.appendChild(companyRow(r)); });
      if (!members.length && !teamsOnly.checked) {
        var tr = document.createElement("tr"); tr.className = "sc-row";
        var td = cell(tr, "sc-left sc-company sc-sub", onlyDrop.checked ? "이 팀에는 빠진 거래처가 없습니다" : "거래처 없음");
        td.colSpan = COLS; tbody.appendChild(tr);
      }
    });
    tbody.appendChild(sumRow("sc-total", "전체 합계", s));
    table.hidden = false;
    if (!(result.teams || []).length) {
      var tr0 = document.createElement("tr"), td0 = document.createElement("td");
      td0.colSpan = COLS; td0.className = "sc-left";
      td0.textContent = "표시할 거래처가 없습니다.";
      tr0.appendChild(td0); tbody.appendChild(tr0);
    }
  }

  // CSV — BOM 을 붙인다 (엑셀이 한글을 깨서 연다). 화면 필터와 무관하게 전체를 원 단위로 낸다
  function csvText(res) {
    var head = ["거래처", "팀", "담당자", "상태", "A 매출", "B 매출", "차이(B-A)", "변동률(%)", "A 행수", "B 행수",
                "다음달 A", "다음달 B", "다음달 증감(B-A)"];
    var lines = [head.join(",")];
    (res.rows || []).forEach(function (r) {
      lines.push([
        '"' + String(r.company).replace(/"/g, '""') + '"',
        '"' + String(r.team || "").replace(/"/g, '""') + '"',
        '"' + String(r.manager || "").replace(/"/g, '""') + '"', r.status,
        r.s_a, r.s_b, r.diff, (r.pct === null || r.pct === undefined) ? "" : r.pct,
        r.n_a, r.n_b, r.next_a, r.next_b, r.next_diff
      ].join(","));
    });
    lines.push("");
    lines.push("# 팀별 합계 (첫 팀 기준, 필터 무관)");
    lines.push(["팀", "거래처수", "사라짐", "감소", "증가", "신규", "A 매출", "B 매출", "차이(B-A)", "변동률(%)", "A 행수", "B 행수", "다음달 증감(B-A)"].join(","));
    (res.teams || []).forEach(function (g) {
      lines.push(['"' + String(g.team).replace(/"/g, '""') + '"', g.companies, g.gone, g.decreased, g.increased, g.new,
                  g.s_a, g.s_b, g.diff, (g.pct === null || g.pct === undefined) ? "" : g.pct, g.n_a, g.n_b, g.next_diff].join(","));
    });
    var t = res.summary || {};
    lines.push(["전체", t.companies, t.gone, t.decreased, t.increased, t.new, t.total_a, t.total_b, t.diff,
                (t.pct === null || t.pct === undefined) ? "" : t.pct, t.n_a, t.n_b, t.next_diff].join(","));
    lines.push("");
    lines.push("# 스냅샷 A=" + res.date_a + " B=" + res.date_b + " 대상월=" + res.month +
               " 조건=Brand IN (SK,CBT) AND Sales_Type=B2B 매출=Sales1_R (원)");
    (res.notices || []).forEach(function (n) { lines.push("# " + n); });
    return "﻿" + lines.join("\r\n");
  }

  function downloadCsv() {
    if (!result) return;
    var blob = new Blob([csvText(result)], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = "sales_carryover_" + result.date_a + "_" + result.date_b + "_" + result.month + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  btnRun.addEventListener("click", run);
  btnCsv.addEventListener("click", downloadCsv);
  onlyDrop.addEventListener("change", render);
  teamsOnly.addEventListener("change", function () { openTeams = {}; render(); });
  Array.prototype.forEach.call(table.querySelectorAll("th[data-key]"), function (th) {
    th.addEventListener("click", function () {
      var k = th.getAttribute("data-key");
      if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = (k === "company" || k === "status" || k === "manager") ? 1 : -1; }
      render();
    });
  });

  loadSnapshots();
})();
