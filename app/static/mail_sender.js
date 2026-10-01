"use strict";
const $ = (id) => document.getElementById(id);
let state = { spreadsheet_id: "", sheet: "", job: null };

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || r.status);
  return j;
}

function text(el, s) { el.textContent = s; }

function showError(msg) {
  $("ms-result").hidden = false;
  text($("ms-summary"), "오류: " + msg);
}

const guard = (fn) => async (...a) => {
  try { await fn(...a); } catch (e) { showError(e.message); }
};

// 요청이 도는 동안 버튼을 잠가 중복 발송을 막는다
async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; }
}

async function init() {
  const s = await api("/api/mail-sender/status");
  text($("ms-me"), s.me);
  if (s.error && s.connected) {
    $("ms-connect").hidden = false;
    $("ms-connect-btn").hidden = true;
    text($("ms-connect-msg"), "오류: " + s.error + " — 잠시 후 새로고침해 주세요");
    return;
  }
  if (!s.connected || s.missing_scopes.length) {
    $("ms-connect").hidden = false;
    text($("ms-connect-msg"), s.error ? s.error : s.connected ? "메일 발송 권한이 빠져 있습니다: " + s.missing_scopes.join(", ") +
      " — 연결 화면에서 모든 항목을 체크하고 허용하세요" : "구글 계정을 연결해야 합니다");
    $("ms-connect-btn").onclick = () => {
      const w = window.open(s.login_url, "gws_auth", "width=500,height=650");
      const t = setInterval(() => { if (w.closed) { clearInterval(t); location.reload(); } }, 1000);
    };
    return;
  }
  $("ms-main").hidden = false;
  for (const a of s.senders) {
    const o = new Option(a.display ? `${a.display} <${a.email}>` : a.email, a.email, a.default, a.default);
    $("ms-sender").add(o);
  }
  const running = await api("/api/mail-sender/jobs/active").catch(() => null);
  if (running && running.job) watchJob(running.job.id);
}

$("ms-load").onclick = guard(async () => {
  const j = await api("/api/mail-sender/sheets", { spreadsheet: $("ms-url").value });
  state.spreadsheet_id = j.spreadsheet_id;
  $("ms-sheet").replaceChildren(...j.sheets.map((t) => new Option(t, t)));
});

$("ms-check").onclick = guard(async () => {
  state.sheet = $("ms-sheet").value;
  text($("ms-summary"), "점검 중… (Docs 탭을 받아오는 데 수십 초 걸릴 수 있습니다)");
  $("ms-result").hidden = false;
  const j = await api("/api/mail-sender/check", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet });
  text($("ms-summary"), `대상 ${j.pending}명 · 오류 ${j.errors}건`);
  $("ms-global-issues").replaceChildren(...j.issues.filter((i) => i.row === null).map((i) => {
    const li = document.createElement("li"); li.className = i.level === "error" ? "ms-err" : "ms-warn";
    li.textContent = i.message; return li; }));
  const byRow = {};
  for (const i of j.issues) if (i.row !== null) (byRow[i.row] = byRow[i.row] || []).push(i);
  $("ms-rows").replaceChildren(...j.rows.map((r) => {
    const tr = document.createElement("tr");
    const iss = byRow[r.row] || [];
    const mark = r.status === "O" ? "보냄" : iss.some((i) => i.level === "error") ? "❌" : iss.length ? "⚠️" : "✅";
    for (const v of [r.row, r.manager, r.email, r.status || "", mark + " " + iss.map((i) => i.message).join(" / ")]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    const td = document.createElement("td");
    if (r.status !== "O") {
      const pv = document.createElement("button"); pv.className = "ms-btn"; pv.textContent = "미리보기";
      pv.onclick = guard(() => preview(r.row));
      const ts = document.createElement("button"); ts.className = "ms-btn"; ts.textContent = "나에게 테스트";
      ts.onclick = guard(() => busy(ts, () => testSend(r.row)));
      td.append(pv, ts);
    }
    tr.append(td); return tr;
  }));
  $("ms-send-all").disabled = j.errors > 0 || j.pending === 0;
  $("ms-send-all").dataset.count = j.pending;
});

async function preview(row) {
  const j = await api("/api/mail-sender/preview", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet, row });
  text($("ms-pv-subject"), j.subject);
  const frame = document.createElement("iframe"); frame.className = "ms-frame";
  frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
  frame.srcdoc = j.html;
  $("ms-pv-body").replaceChildren(frame);
  $("ms-preview").showModal();
}

async function testSend(row) {
  await api("/api/mail-sender/test-send", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet, row,
    sender: $("ms-sender").value });
  alert("본인 메일함으로 테스트 메일을 보냈습니다");
}

$("ms-send-all").onclick = guard(async () => {
  const n = $("ms-send-all").dataset.count;
  if (!confirm(`${state.sheet} 시트의 ${n}명에게 '${$("ms-sender").value}' 로 발송합니다. 진행할까요?`)) return;
  await busy($("ms-send-all"), async () => {
    const j = await api("/api/mail-sender/jobs", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet,
      sender: $("ms-sender").value, confirm_count: Number(n) });
    watchJob(j.job_id);
  });
});

function watchJob(id) {
  $("ms-job").hidden = false;
  $("ms-stop").onclick = () => api(`/api/mail-sender/jobs/${id}/stop`, {});
  const tick = guard(async () => {
    const j = await api(`/api/mail-sender/jobs/${id}`);
    const done = j.sent + j.failed + j.skipped;
    $("ms-job-bar").value = j.total ? Math.round((done / j.total) * 100) : 0;
    text($("ms-job-text"), `${j.status} · 성공 ${j.sent} · 실패 ${j.failed} · 건너뜀 ${j.skipped} / ${j.total}` +
      (j.stop_reason ? ` — ${j.stop_reason}` : ""));
    if (["running", "stopping"].includes(j.status)) setTimeout(tick, 2000);
  });
  tick();
}

init().catch((e) => { $("ms-connect").hidden = false; text($("ms-connect-msg"), "오류: " + e.message); });
