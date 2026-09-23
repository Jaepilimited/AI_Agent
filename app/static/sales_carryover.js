/* 매출 이월 확인 — 두 스냅샷 날짜를 골라 거래처별 차이를 본다.
 *
 * 숫자는 서버(코드)가 만든 것을 그대로 그린다. 여기서 다시 계산하지 않는다 —
 * 두 곳에서 계산하면 언젠가 서로 다른 값이 된다.
 * ⛔ 날짜는 서버가 준 목록에서만 고른다. 없는 날짜의 테이블은 존재하지 않는다.
 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var selA = $("sc-a"), selB = $("sc-b"), inMonth = $("sc-month");
  var btnRun = $("sc-run"), btnCsv = $("sc-csv"), onlyDrop = $("sc-only-drop");
  var elErr = $("sc-error"), elNotice = $("sc-notice"), elSum = $("sc-summary");
  var table = $("sc-table"), tbody = $("sc-body");
  var dates = [];
  var result = null;
  var sortKey = null, sortDir = 1;

  function fmtDate(d) { return d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6); }
  function fmtWon(n) {
    if (n === null || n === undefined) return "";
    return Math.round(n).toLocaleString("ko-KR");
  }
  function fmtPct(p) { return (p === null || p === undefined) ? "" : (p > 0 ? "+" : "") + p.toFixed(1) + "%"; }
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
        // 기본값: A = 가장 최근 달의 1일 이전 마지막 스냅샷 부근이 아니라, 단순히
        // 최신 두 개 중 앞쪽/뒤쪽. 사용자가 고른다 — 지어내지 않는다
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

  function render() {
    if (!result) return;
    var s = result.summary;
    // 공시는 표보다 먼저 — 사람은 표를 보지 각주를 안 본다
    elNotice.textContent = (result.notices || []).join("  ");
    elSum.innerHTML =
      "<b>" + fmtDate(result.date_a) + "</b> → <b>" + fmtDate(result.date_b) + "</b> 스냅샷 · 대상월 <b>" +
      result.month + "</b> (다음 달 " + result.next_month + ") · 거래처 " + s.companies + "곳<br>" +
      "합계 A " + fmtWon(s.total_a) + "원 → B " + fmtWon(s.total_b) + "원 (차이 " +
      (s.diff > 0 ? "+" : "") + fmtWon(s.diff) + "원) · 사라짐 " + s.gone + " · 감소 " + s.decreased +
      " · 증가 " + s.increased + " · 신규 " + s.new +
      (s.truncated ? " · 표에서 뺀 거래처 " + s.truncated : "");
    var rows = visibleRows();
    tbody.innerHTML = "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var cells = [
        ["sc-left sc-company", r.company],
        ["sc-left " + statusClass(r.status), r.status],
        ["", fmtWon(r.s_a)], ["", fmtWon(r.s_b)],
        ["", (r.diff > 0 ? "+" : "") + fmtWon(r.diff)],
        ["", fmtPct(r.pct)],
        ["", String(r.n_a)], ["", String(r.n_b)],
        ["", (r.next_diff > 0 ? "+" : "") + fmtWon(r.next_diff)]
      ];
      cells.forEach(function (c) {
        var td = document.createElement("td");
        if (c[0]) td.className = c[0];
        td.textContent = c[1];
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.hidden = false;
    if (!rows.length) {
      var tr = document.createElement("tr"), td = document.createElement("td");
      td.colSpan = 9; td.className = "sc-left";
      td.textContent = onlyDrop.checked ? "사라지거나 줄어든 거래처가 없습니다." : "표시할 거래처가 없습니다.";
      tr.appendChild(td); tbody.appendChild(tr);
    }
  }

  // CSV — BOM 을 붙인다 (엑셀이 한글을 깨서 연다). 화면 필터와 무관하게 전체를 내보낸다
  function csvText(res) {
    var head = ["거래처", "상태", "A 매출", "B 매출", "차이(B-A)", "변동률(%)", "A 건수", "B 건수",
                "다음달 A", "다음달 B", "다음달 증감(B-A)"];
    var lines = [head.join(",")];
    (res.rows || []).forEach(function (r) {
      lines.push([
        '"' + String(r.company).replace(/"/g, '""') + '"', r.status,
        r.s_a, r.s_b, r.diff, (r.pct === null || r.pct === undefined) ? "" : r.pct,
        r.n_a, r.n_b, r.next_a, r.next_b, r.next_diff
      ].join(","));
    });
    lines.push("");
    lines.push("# 스냅샷 A=" + res.date_a + " B=" + res.date_b + " 대상월=" + res.month +
               " 조건=Brand IN (SK,CBT) AND Sales_Type=B2B 매출=Sales1_R");
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
  Array.prototype.forEach.call(table.querySelectorAll("th[data-key]"), function (th) {
    th.addEventListener("click", function () {
      var k = th.getAttribute("data-key");
      if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = (k === "company" || k === "status") ? 1 : -1; }
      render();
    });
  });

  loadSnapshots();
})();
