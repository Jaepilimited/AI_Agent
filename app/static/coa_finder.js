(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STATUS_CLASS = {
    "찾음": "st-found",
    "없음": "st-none",
    "여러건": "st-many",
    "확인필요": "st-check",
  };

  // kind: "coa" or "msds". MSDS is a product-level document with no lot --
  // stamping the row's lot onto it would make it look lot-matched, which is
  // exactly why the screen keeps it in its own column.
  function cell(v, sku, lot, kind) {
    const wrap = document.createElement("td");
    const label = document.createElement("div");
    label.className = STATUS_CLASS[v.status] || "";
    label.textContent = v.status;
    wrap.appendChild(label);
    (v.files || []).forEach((f) => {
      const line = document.createElement("div");
      const a = document.createElement("a");
      a.href = f.link;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = f.name;
      a.className = "cf-file";
      a.dataset.fileId = f.id;
      a.dataset.sku = sku;
      a.dataset.lot = kind === "msds" ? "" : lot;
      // The verdict travels with the file all the way into the ZIP -- a
      // caveat that only exists on screen is gone by the time the archive
      // reaches a customer.
      a.dataset.status = v.status || "";
      a.dataset.kind = kind;
      line.appendChild(a);
      wrap.appendChild(line);
    });
    if (v.note) {
      const n = document.createElement("div");
      n.className = "cf-note";
      n.textContent = v.note;
      wrap.appendChild(n);
    }
    return wrap;
  }

  function addRow(d) {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "cf-pick";
    box.checked = d.coa.status === "찾음";
    pick.appendChild(box);
    tr.appendChild(pick);
    [d.sku, d.description, d.lot].forEach((t) => {
      const td = document.createElement("td");
      td.textContent = t || "";
      tr.appendChild(td);
    });
    tr.appendChild(cell(d.coa, d.sku, d.lot, "coa"));
    tr.appendChild(cell(d.msds, d.sku, d.lot, "msds"));
    $("cf-body").appendChild(tr);
  }

  function setBusy(busy) {
    $("cf-run").disabled = busy;
    $("cf-run").textContent = busy ? "찾는 중..." : "찾기";
  }

  function showError(msg) {
    $("cf-error").textContent = msg || "";
  }

  // Every row came back "없음". That looks identical whether the documents
  // really are absent, the Google token died, the user is not a member of
  // the shared drive, or the lots were typed wrong -- so say so out loud
  // instead of letting a total miss pass for a finished search.
  function showAllNone(show) {
    let el = $("cf-allnone");
    if (!show) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      const scroll = document.querySelector(".cf-scroll");
      // Never let a missing container throw here -- this runs inside the
      // frame loop, so an exception would repaint a finished search as a
      // network failure.
      if (!scroll || !scroll.parentNode) return;
      el = document.createElement("div");
      el.id = "cf-allnone";
      el.className = "cf-error";
      scroll.parentNode.insertBefore(el, scroll);
    }
    el.textContent =
      "전 행이 '없음'입니다 — 문서가 정말 없는 것인지 판단하기 전에 " +
      "구글 계정 연결 상태, 그 공유드라이브의 멤버인지, 롯트 표기(대소문자·공백)를 " +
      "먼저 확인하세요. 이 셋 중 하나만 어긋나도 화면은 똑같이 '없음' 으로 보입니다.";
  }

  function errMessage(res, fallback) {
    return res.json().then(
      (body) => (body && body.detail) || fallback,
      () => fallback
    );
  }

  // SSE cannot use POST from EventSource, so this reads the streamed
  // response body directly and splits it into SSE frames by hand.
  async function run() {
    $("cf-body").innerHTML = "";
    showError("");
    showAllNone(false);
    $("cf-table").hidden = false;
    $("cf-download").disabled = true;
    $("cf-progress").textContent = "";
    setBusy(true);

    const fd = new FormData();
    const file = $("cf-file").files[0];
    if (file) {
      fd.append("file", file);
    } else {
      fd.append("pasted", $("cf-paste").value);
    }

    // Whether a "done" or "error" event actually arrived. A stream that
    // ends without either one is a dropped connection, not a finished
    // search -- rendering it as complete would hand out a certificate
    // set the user believes is whole when it might not be.
    let finished = false;
    let lastIndex = 0;
    let lastTotal = 0;

    try {
      const res = await fetch("/api/coa-finder/search", { method: "POST", body: fd });
      if (!res.ok) {
        showError(await errMessage(res, "요청에 실패했습니다"));
        $("cf-table").hidden = true;
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop();
        frames.forEach((frame) => {
          const evMatch = /^event:\s*(\w+)/m.exec(frame);
          const dataMatch = /^data:\s*(.+)$/m.exec(frame);
          if (!evMatch || !dataMatch) return;
          const kind = evMatch[1];
          const data = JSON.parse(dataMatch[1]);
          if (kind === "row") {
            addRow(data);
            lastIndex = data.index;
            lastTotal = data.total;
            $("cf-progress").textContent = data.index + " / " + data.total;
          } else if (kind === "done") {
            finished = true;
            const c = data.counts || {};
            // The counts are COA verdicts, and the search ran over file
            // names only -- without saying so, "없음 41" reads as a broken
            // tool rather than as documents that are not on the drive.
            $("cf-progress").textContent =
              "완료 — " + data.total + "행 · COA 기준 찾음 " + (c["찾음"] || 0) +
              " · 여러건 " + (c["여러건"] || 0) +
              " · 확인필요 " + (c["확인필요"] || 0) +
              " · 없음 " + (c["없음"] || 0) +
              " · COA·MSDS 파일의 파일명으로만 찾았습니다 (파일 본문은 검색하지 않습니다)";
            showAllNone(data.total > 0 && (c["없음"] || 0) === data.total);
            $("cf-download").disabled = false;
          } else if (kind === "error") {
            finished = true;
            lastIndex = data.completed;
            lastTotal = data.total;
            showError(
              (data.message || "조회 중 오류가 발생했습니다") +
              " (" + data.completed + " / " + data.total + "건까지만 완료됨 — " +
              "이후 결과는 없습니다)"
            );
            $("cf-progress").textContent =
              "중단됨 — " + data.completed + " / " + data.total;
          }
        });
      }

      if (!finished) {
        // Connection closed without a "done" or "error" frame ever
        // arriving -- treat this as a failed search, not a finished one.
        const shown = lastTotal || "?";
        showError(
          "연결이 끊겨 조회가 중단되었습니다 (" +
          lastIndex + " / " + shown + "건까지만 표시됨). " +
          "새로고침 후 다시 시도해주세요."
        );
        $("cf-progress").textContent = "연결 끊김 — " + lastIndex + " / " + shown;
      }
    } catch (err) {
      const shown = lastTotal || "?";
      showError(
        "네트워크 오류로 조회가 중단되었습니다: " +
        (err && err.message ? err.message : String(err))
      );
      $("cf-progress").textContent = "연결 끊김 — " + lastIndex + " / " + shown;
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    const items = [];
    document.querySelectorAll("#cf-body tr").forEach((tr) => {
      if (!tr.querySelector(".cf-pick").checked) return;
      tr.querySelectorAll("a.cf-file").forEach((a) => {
        items.push({
          file_id: a.dataset.fileId,
          sku: a.dataset.sku,
          lot: a.dataset.lot,
          name: a.textContent,
          status: a.dataset.status || "",
          kind: a.dataset.kind || "coa",
        });
      });
    });
    if (!items.length) {
      showError("받을 파일을 선택해주세요");
      return;
    }

    $("cf-download").disabled = true;
    try {
      const res = await fetch("/api/coa-finder/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: items }),
      });
      if (!res.ok) {
        showError(await errMessage(res, "받기에 실패했습니다"));
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "coa_msds.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showError(
        "받는 중 네트워크 오류가 발생했습니다: " +
        (err && err.message ? err.message : String(err))
      );
    } finally {
      $("cf-download").disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("cf-run").addEventListener("click", run);
    $("cf-download").addEventListener("click", download);
    $("cf-all").addEventListener("change", (e) => {
      document.querySelectorAll(".cf-pick").forEach((b) => {
        b.checked = e.target.checked;
      });
    });
  });
})();
