"use strict";
const $ = (id) => document.getElementById(id);
const TERMINAL = ["done", "stopped", "interrupted"];
const STATUS_LABEL = { running: "발송 중", stopping: "중지하는 중", done: "완료", stopped: "중지됨",
  interrupted: "중단됨(서버 재시작)" };
// checkOk: 마지막 점검이 오류 없이 대상 1명 이상 / watching: 지켜보는 작업 id (그동안 전체 발송 잠금)
let state = { spreadsheet_id: "", sheet: "", checkOk: false, watching: null };

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || String(r.status));
    err.status = r.status;
    err.detail = j.detail;
    throw err;
  }
  return j;
}

// ⛔ 작업을 지켜보는 동안에는 전체 발송을 다시 누를 수 없다 (끝난 뒤에도 새 점검이 있어야 켜진다)
function refreshSendAll() {
  $("ms-send-all").disabled = state.watching !== null || !state.checkOk;
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
  refreshSendAll();
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

// 본인 작업의 '발송 여부 불확실' 행 — 서버 재시작으로 중단된 작업도 여기서만 '확인했음' 을 누를 수 있다
function renderUnknownJobs(list) {
  $("ms-unknown-jobs").replaceChildren(...(list || []).map((u) => {
    const box = document.createElement("div"); box.className = "ms-warn";
    const p = document.createElement("p");
    p.textContent = `작업 #${u.job_id}: ${u.rows.join(", ")}행 — 발송 여부 불확실. Gmail 보낸편지함에서 확인하고, ` +
      "실제로 나간 행은 시트 전송여부 열에 O 를 적은 뒤 '확인했음' 을 누르세요 (O 가 없는 행은 다시 대상이 됩니다)";
    const b = document.createElement("button"); b.className = "ms-btn"; b.textContent = "확인했음";
    b.onclick = guard(() => busy(b, async () => {
      if (!confirm(`${u.rows.join(", ")}행을 보낸편지함에서 확인했고, 나간 행은 O 로 표시했습니까?`)) return;
      await api(`/api/mail-sender/jobs/${u.job_id}/ack-unknown`, {});
      await runCheck();
    }));
    box.append(p, b); return box;
  }));
}

$("ms-check").onclick = guard(runCheck);

async function runCheck() {
  state.sheet = $("ms-sheet").value;
  state.checkOk = false;
  refreshSendAll();
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
    const isSent = (r.status || "").trim().toUpperCase() === "O";
    const mark = isSent ? "보냄" : iss.some((i) => i.level === "error") ? "❌" : iss.length ? "⚠️" : "✅";
    for (const v of [r.row, r.manager, r.email, r.status || "", mark + " " + iss.map((i) => i.message).join(" / ")]) {
      const td = document.createElement("td"); td.textContent = v; tr.append(td);
    }
    const td = document.createElement("td");
    if (!isSent) {
      const pv = document.createElement("button"); pv.className = "ms-btn"; pv.textContent = "미리보기";
      pv.onclick = guard(() => preview(r.row));
      const ts = document.createElement("button"); ts.className = "ms-btn"; ts.textContent = "나에게 테스트";
      ts.onclick = guard(() => busy(ts, () => testSend(r.row)));
      td.append(pv, ts);
    }
    tr.append(td); return tr;
  }));
  renderUnknownJobs(j.unknown_jobs);
  $("ms-send-all").dataset.count = j.pending;
  state.checkOk = j.errors === 0 && j.pending > 0;
  refreshSendAll();
}

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
  const btn = $("ms-send-all");
  if (btn.disabled) return;
  const n = btn.dataset.count;
  if (!confirm(`${state.sheet} 시트의 ${n}명에게 '${$("ms-sender").value}' 로 발송합니다. 진행할까요?`)) return;
  // busy() 는 끝나면 버튼을 다시 켠다 — 작업을 지켜보는 동안 잠가 두려고 직접 다룬다
  btn.disabled = true;
  try {
    const j = await api("/api/mail-sender/jobs", { spreadsheet_id: state.spreadsheet_id, sheet: state.sheet,
      sender: $("ms-sender").value, confirm_count: Number(n) });
    state.checkOk = false;  // 다음 발송은 다시 점검한 뒤에
    watchJob(j.job_id);
  } catch (e) {
    refreshSendAll();
    throw e;
  }
});

function renderJob(j) {
  const n = (k) => Number(j[k] || 0);
  const done = n("sent") + n("failed") + n("skipped") + n("unknown");
  $("ms-job-bar").value = j.total ? Math.round((done / j.total) * 100) : 0;
  text($("ms-job-text"), `${STATUS_LABEL[j.status] || j.status} · 성공 ${n("sent")} · 실패 ${n("failed")} · ` +
    `건너뜀 ${n("skipped")} · 불확실 ${n("unknown")} · 시트 기록 실패 ${n("sheet_write_failed")} / 전체 ${j.total}` +
    (j.stop_reason ? ` — ${j.stop_reason}` : ""));
  const rows = Array.isArray(j.unknown_rows) ? j.unknown_rows : [];
  $("ms-unknown").hidden = rows.length === 0;
  if (!rows.length) return;
  text($("ms-unknown-text"), `발송 여부 불확실: ${rows.join(", ")}행 — Gmail 보낸편지함에서 확인하세요. ` +
    "실제로 나간 행은 시트 전송여부 열에 O 를 적은 뒤 '확인했음' 을 누르세요 (O 가 없는 행은 다음 발송 때 보내집니다)");
  $("ms-ack").onclick = guard(() => busy($("ms-ack"), async () => {
    if (!confirm(`${rows.join(", ")}행을 보낸편지함에서 확인했고, 나간 행은 O 로 표시했습니까?`)) return;
    await api(`/api/mail-sender/jobs/${j.id}/ack-unknown`, {});
    renderJob(await api(`/api/mail-sender/jobs/${j.id}`));
  }));
}

function endWatch(id) {
  if (state.watching !== id) return;
  state.watching = null;
  $("ms-stop").hidden = true;
  refreshSendAll();
}

function watchJob(id) {
  state.watching = id;
  refreshSendAll();
  $("ms-job").hidden = false;
  $("ms-stop").hidden = false;
  $("ms-stop").onclick = guard(() => busy($("ms-stop"), () => api(`/api/mail-sender/jobs/${id}/stop`, {})));
  const tick = async () => {
    if (state.watching !== id) return;
    let j;
    try {
      j = await api(`/api/mail-sender/jobs/${id}`);
    } catch (e) {
      if (e.status === 404) {
        text($("ms-job-err"), "작업을 찾을 수 없습니다");
        endWatch(id);
        return;
      }
      // 일시 오류(네트워크·서버 재시작) — 지켜보기를 멈추지 않는다
      text($("ms-job-err"), `상태를 불러오지 못했습니다 (${e.message}) — 5초 뒤 다시 확인합니다`);
      setTimeout(tick, 5000);
      return;
    }
    text($("ms-job-err"), "");
    renderJob(j);
    if (TERMINAL.includes(j.status)) endWatch(id);
    else setTimeout(tick, 2000);
  };
  tick();
}

init().catch((e) => { $("ms-connect").hidden = false; text($("ms-connect-msg"), "오류: " + e.message); });
