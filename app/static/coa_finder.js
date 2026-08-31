(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STATUS_CLASS = {
    "찾음": "st-found",
    "없음": "st-none",
    "여러건": "st-many",
    "확인필요": "st-check",
    // A query that never completed is not a verdict. Without its own entry
    // here it would render unstyled and read as an ordinary result.
    "조회실패": "st-check",
  };

  // kind: "coa" | "msds" | "product_coa".
  //  - MSDS is a product-level document with no lot.
  //  - product_coa is a certificate for a DIFFERENT lot of the same product.
  // Stamping the row's lot onto either would make it look lot-matched, which
  // is exactly why the screen keeps them in their own columns.
  //
  // product_coa is also the one column the row checkbox must not sweep up: it
  // is the most dangerous thing on this page, so it opts in per cell and the
  // header "select all" cannot reach it.
  function cell(v, sku, lot, kind) {
    const optIn = kind === "product_coa" && (v.files || []).length > 0;
    const wrap = document.createElement("td");
    const label = document.createElement("div");
    label.className = STATUS_CLASS[v.status] || "";
    label.textContent = v.status;
    if (optIn) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.className = "cf-pick-product";
      box.title = "다른 롯트의 증명서입니다 — 받으려면 직접 선택하세요";
      label.prepend(box);
    }
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
      a.dataset.lot = (kind === "msds" || kind === "product_coa") ? "" : lot;
      if (optIn) a.dataset.optin = "1";
      // The verdict travels with the file all the way into the ZIP -- a
      // caveat that only exists on screen is gone by the time the archive
      // reaches a customer.
      a.dataset.status = v.status || "";
      a.dataset.kind = kind;
      // Sizes come from the search result, so the page can total a
      // selection before asking the server for any of it.
      a.dataset.size = f.size == null ? "" : String(f.size);
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
    tr.appendChild(cell(d.product_coa || { status: "", files: [] },
                        d.sku, d.lot, "product_coa"));
    $("cf-body").appendChild(tr);
  }

  // Caps published by the server in the "done" event. The page never keeps
  // its own copy of a server limit -- a duplicate like that drifts silently.
  let caps = null;

  // Split a selection into consecutive batches that each stay under both
  // caps, so no request is ever made that the server would have to truncate.
  //
  // Rules that matter:
  //  - caps absent  -> one batch. The server still enforces both caps and
  //    reports a truthful partial archive, so guessing here is worse.
  //  - a file bigger than the byte cap can never fit anywhere. It gets its
  //    own batch and the server rejects it, which puts a reason in
  //    _받지못한_목록.txt. Dropping it here would be silent.
  //  - a missing size counts as zero. Drive omits size for Google-native
  //    files; refusing to proceed would be worse, and an overflow is caught
  //    by the server and announced through the response headers.
  //
  // WARNING: tests extract this function by brace matching and run it in
  // node. Keep it free of closure references and of braces inside strings.
  function planBatches(items, caps) {
    if (!caps || !caps.items || !caps.bytes) return items.length ? [items] : [];
    const batches = [];
    let current = [];
    let bytes = 0;
    for (let i = 0; i < items.length; i++) {
      const size = Number(items[i] && items[i].size) || 0;
      const tooManyItems = current.length >= caps.items;
      const tooManyBytes = current.length > 0 && bytes + size > caps.bytes;
      if (tooManyItems || tooManyBytes) {
        batches.push(current);
        current = [];
        bytes = 0;
      }
      current.push(items[i]);
      bytes += size;
    }
    if (current.length) batches.push(current);
    return batches;
  }

  function setBusy(busy) {
    $("cf-run").disabled = busy;
    $("cf-run").textContent = busy ? "찾는 중..." : "찾기";
  }

  function showError(msg) {
    $("cf-error").textContent = msg || "";
  }

  // A line above the table. Empty text removes it -- .cf-error already
  // hides when empty, but leaving a stale node behind would carry the last
  // run's warning into the next one.
  function notice(id, text) {
    let el = $(id);
    if (!text) {
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
      el.id = id;
      el.className = "cf-error";
      scroll.parentNode.insertBefore(el, scroll);
    }
    el.textContent = text;
  }

  // Every row came back "없음". That looks identical whether the documents
  // really are absent, the Google token died, the user is not a member of
  // the shared drive, or the lots were typed wrong -- so say so out loud
  // instead of letting a total miss pass for a finished search.
  function showAllNone(show) {
    notice("cf-allnone", show
      ? "전 행이 '없음'입니다 — 문서가 정말 없는 것인지 판단하기 전에 " +
        "구글 계정 연결 상태, 그 공유드라이브의 멤버인지, 롯트 표기(대소문자·공백)를 " +
        "먼저 확인하세요. 이 셋 중 하나만 어긋나도 화면은 똑같이 '없음' 으로 보입니다."
      : "");
  }

  // Rows whose SKU cell was empty are dropped. Someone pasting 40 rows and
  // getting 38 back will not notice unless the page counts them out loud.
  function showSkipped(n) {
    notice("cf-skipped", n > 0
      ? "SKU 칸이 비어 건너뛴 행 " + n + "건 — 이 행들은 조회하지 않았습니다. " +
        "SKU 를 채워 다시 올리세요."
      : "");
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
    notice("cf-layout", "");
    showAllNone(false);
    showSkipped(0);
    notice("cf-batch", "");
    // A new search invalidates the caps until this run's "done" restates them.
    caps = null;
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
            // A failed query is not a verdict -- it gets its own count, and
            // MSDS failures are counted separately because `counts` only
            // tallies the COA column.
            const msdsFailed = data.msds_failed || 0;
            $("cf-progress").textContent =
              "완료 — " + data.total + "행 · COA 기준 찾음 " + (c["찾음"] || 0) +
              " · 여러건 " + (c["여러건"] || 0) +
              " · 확인필요 " + (c["확인필요"] || 0) +
              " · 없음 " + (c["없음"] || 0) +
              " · 조회실패 " + (c["조회실패"] || 0) +
              (msdsFailed ? " · MSDS 조회실패 " + msdsFailed : "") +
              (data.product_coa_failed
                ? " · 제품COA 조회실패 " + data.product_coa_failed : "") +
              " · COA·MSDS 파일의 파일명으로만 찾았습니다 (파일 본문은 검색하지 않습니다)";
            // Never apply an inferred layout silently -- if the wrong column
            // was read as the lot, only the user can tell.
            notice("cf-layout", data.layout || "");
            // Caps come from the server so the page holds no copy of them.
            // Absent -> no batching; the server still enforces both.
            caps = (data.max_download_items && data.max_download_bytes)
              ? { items: data.max_download_items, bytes: data.max_download_bytes }
              : null;
            showSkipped(data.skipped_no_sku || 0);
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

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // A partial archive downloads exactly like a whole one. The server states
  // out of band what it actually wrote, because a count the client computed
  // can be wrong -- sizes are missing for some Drive files, and files change
  // between the search and the download.
  function skippedFromHeaders(res) {
    return Number(res.headers.get("X-COA-Items-Skipped")) || 0;
  }

  async function download() {
    const items = [];
    document.querySelectorAll("#cf-body tr").forEach((tr) => {
      const rowOn = tr.querySelector(".cf-pick").checked;
      tr.querySelectorAll("a.cf-file").forEach((a) => {
        // Opt-in files (product COA) follow their own cell's checkbox, never
        // the row's and never "select all" -- they are other lots' documents.
        if (a.dataset.optin === "1") {
          const td = a.closest("td");
          const box = td && td.querySelector(".cf-pick-product");
          if (!box || !box.checked) return;
        } else if (!rowOn) {
          return;
        }
        items.push({
          file_id: a.dataset.fileId,
          sku: a.dataset.sku,
          lot: a.dataset.lot,
          name: a.textContent,
          status: a.dataset.status || "",
          kind: a.dataset.kind || "coa",
          size: Number(a.dataset.size) || 0,
        });
      });
    });
    if (!items.length) {
      showError("받을 파일을 선택해주세요");
      return;
    }

    const batches = planBatches(items, caps);
    const many = batches.length > 1;
    notice("cf-batch", many
      ? "선택한 " + items.length + "건이 한 번에 받을 수 있는 양을 넘어 " +
        batches.length + "개 파일로 나눠 받습니다 — 브라우저가 " +
        batches.length + "번 저장합니다."
      : "");

    $("cf-download").disabled = true;
    let skipped = 0;
    try {
      for (let i = 0; i < batches.length; i++) {
        if (many) {
          notice("cf-batch",
            batches.length + "개 중 " + (i + 1) + "번째를 받는 중입니다...");
        }
        // The size field is for batching only -- the server does not read it.
        const res = await fetch("/api/coa-finder/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: batches[i] }),
        });
        if (!res.ok) {
          showError(await errMessage(res, "받기에 실패했습니다") +
            (many ? " (" + batches.length + "개 중 " + (i + 1) + "번째)" : ""));
          return;
        }
        skipped += skippedFromHeaders(res);
        saveBlob(await res.blob(),
          many ? "coa_msds_" + (i + 1) + ".zip" : "coa_msds.zip");
      }
      notice("cf-batch", many
        ? "완료 — " + batches.length + "개 파일로 받았습니다."
        : "");
      if (skipped > 0) {
        // Never let a partial archive pass for a whole one.
        showError(
          "받지 못한 파일이 " + skipped + "건 있습니다 — " +
          "ZIP 안의 _받지못한_목록.txt 에 파일별 사유가 적혀 있습니다."
        );
      }
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
