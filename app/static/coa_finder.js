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

  // Every row payload, in arrival order. The CSV of missing COAs is built
  // from this rather than from the DOM: the table can be filtered, and a
  // list built from what happens to be visible would quietly change size.
  let rowData = [];

  function addRow(d) {
    const tr = document.createElement("tr");
    // The filter reads this, so it must be set before the row is appended --
    // rows keep streaming in while the toggle is already on.
    tr.dataset.coaStatus = (d.coa && d.coa.status) || "";
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
    if ($("cf-only-none").checked && tr.dataset.coaStatus !== "없음") {
      tr.hidden = true;
    }
    $("cf-body").appendChild(tr);
    rowData.push(d);
  }

  // Hide everything that is not a COA "없음". This only hides -- the download
  // buttons still act on every checked row, which the label next to the
  // toggle says out loud. A filter that silently changed what a download
  // contains would be the worst kind of quiet.
  function applyNoneFilter(on) {
    document.querySelectorAll("#cf-body tr").forEach((tr) => {
      tr.hidden = on && tr.dataset.coaStatus !== "없음";
    });
  }

  // The list of lots with no COA, as CSV.
  //
  // ⛔ 조회실패 is NOT folded in. A query that never completed says nothing
  // about whether the certificate exists -- putting it in a list titled
  // "COA 없음" would turn "we do not know" into "it is not there", and this
  // list is one someone acts on. It is counted and stated in a footer row
  // instead, so the caveat travels with the file rather than living only on
  // the screen that produced it.
  //
  // WARNING: tests extract this function by brace matching and run it in
  // node. Keep it free of closure references and of braces inside strings.
  function noneListCsv(rows) {
    const lines = ["SKU,제품명,롯트,COA 판정,사유"];
    let none = 0;
    let failed = 0;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i] || {};
      const coa = r.coa || {};
      const status = coa.status || "";
      if (status === "조회실패") failed++;
      if (status !== "없음") continue;
      none++;
      const cells = [r.sku, r.description, r.lot, status, coa.note];
      const quoted = [];
      for (let j = 0; j < cells.length; j++) {
        const text = cells[j] == null ? "" : String(cells[j]);
        quoted.push('"' + text.split('"').join('""') + '"');
      }
      lines.push(quoted.join(","));
    }
    if (failed > 0) {
      lines.push("");
      lines.push('"# 조회실패 ' + failed +
                 '건은 드라이브 조회가 실패해 판정되지 않았습니다 — 이 목록에 없습니다"');
    }
    return { csv: lines.join("\r\n"), none: none, failed: failed };
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

  const KIND_LABEL = { coa: "COA", msds: "MSDS" };
  const ZIP_BASE = { coa: "coa", msds: "msds" };
  // ⛔ 목록에서 버튼 하나가 빠지면 그 버튼만 조회 전에 눌리는 상태로 남는다 —
  //    빈 표에서 눌러도 에러가 아니라 "받을 파일을 선택해주세요" 로 보인다
  const DOWNLOAD_BUTTONS = ["cf-dl-coa", "cf-dl-msds", "cf-download", "cf-dl-none"];

  function setDownloadsEnabled(on) {
    DOWNLOAD_BUTTONS.forEach((id) => { $(id).disabled = !on; });
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
    notice("cf-kind", "");
    // A new search invalidates the caps until this run's "done" restates them.
    caps = null;
    // ⛔ 지난 조회의 행을 남기면 'COA 없는 목록' 이 이번 조회에 없는 롯트를
    //    싣는다 — 표는 비워졌는데 목록만 옛것인 조용한 어긋남이다
    rowData = [];
    $("cf-table").hidden = false;
    setDownloadsEnabled(false);
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
            setDownloadsEnabled(true);
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

  // kindFilter: null = everything selected, or "coa" / "msds" for one kind.
  //
  // 제품COA is deliberately in neither single-kind download: it is a different
  // lot's certificate, so it never rides along with a button someone presses
  // to get "the COAs". It still goes out under 선택 전체 받기, where its own
  // cell checkbox is the thing that put it there.
  async function download(kindFilter) {
    const items = [];
    // Files the filter removed from a selection the user had already made.
    // An archive quietly smaller than the selection is the failure this page
    // exists to avoid, so the count is stated on screen.
    let dropped = 0;
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
        if (kindFilter && (a.dataset.kind || "coa") !== kindFilter) {
          dropped++;
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
      showError(kindFilter
        ? "선택한 행에 " + (KIND_LABEL[kindFilter] || kindFilter) + " 파일이 없습니다"
        : "받을 파일을 선택해주세요");
      return;
    }
    notice("cf-kind", dropped > 0
      ? (KIND_LABEL[kindFilter] || kindFilter) + "만 받습니다 — 선택한 것 중 " +
        dropped + "건(다른 종류)은 이번 받기에서 제외했습니다."
      : "");

    const batches = planBatches(items, caps);
    const many = batches.length > 1;
    notice("cf-batch", many
      ? "선택한 " + items.length + "건이 한 번에 받을 수 있는 양을 넘어 " +
        batches.length + "개 파일로 나눠 받습니다 — 브라우저가 " +
        batches.length + "번 저장합니다."
      : "");

    // ⛔ 누른 버튼 하나만 잠그면 나머지 셋은 그대로다 — 받는 도중에 다른
    //    종류를 또 누르면 같은 ZIP 이름으로 두 저장이 겹친다
    setDownloadsEnabled(false);
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
        // ⛔ A single-kind archive named coa_msds.zip lies about itself. The
        //    server derives the same name from the items it received.
        const base = ZIP_BASE[kindFilter] || "coa_msds";
        saveBlob(await res.blob(),
          many ? base + "_" + (i + 1) + ".zip" : base + ".zip");
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
      setDownloadsEnabled(true);
    }
  }

  // The list of lots that came back with no COA, as a file someone can open
  // in Excel and work from. UTF-8 with a BOM: without it Excel reads the
  // Korean columns as mojibake, which looks like a broken export.
  function downloadNoneList() {
    const built = noneListCsv(rowData);
    if (!built.none) {
      // ⛔ Never hand over an empty file that looks like an answer. "없음이
      //    없다" and "조회를 안 했다" must not look the same.
      showError(rowData.length
        ? "COA '없음' 판정인 행이 없습니다 — 내려받을 목록이 비어 있습니다."
        : "먼저 조회를 실행해주세요.");
      return;
    }
    showError("");
    notice("cf-nonelist", "COA 없는 목록 " + built.none + "행을 내려받았습니다" +
      (built.failed
        ? " · 조회실패 " + built.failed + "건은 판정되지 않아 목록에 없습니다 (파일 끝에 함께 적었습니다)"
        : "") + ".");
    saveBlob(new Blob(["\ufeff" + built.csv],
                      { type: "text/csv;charset=utf-8" }),
             "coa_없는_목록.csv");
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("cf-run").addEventListener("click", run);
    // ⛔ download 를 핸들러로 **바로** 넘기지 마라 — 이벤트 객체가 kindFilter
    //    자리에 들어가고, truthy 라 모든 파일이 걸러져 0건이 된다 (에러는 안 난다).
    //    ⚠️ 이 주석에 그 호출 모양을 그대로 적지 마라 — 회귀가 코드로 읽는다
    $("cf-download").addEventListener("click", () => download(null));
    $("cf-dl-coa").addEventListener("click", () => download("coa"));
    $("cf-dl-msds").addEventListener("click", () => download("msds"));
    $("cf-dl-none").addEventListener("click", downloadNoneList);
    $("cf-only-none").addEventListener("change", (e) => {
      applyNoneFilter(e.target.checked);
    });
    $("cf-all").addEventListener("change", (e) => {
      document.querySelectorAll(".cf-pick").forEach((b) => {
        b.checked = e.target.checked;
      });
    });
  });
})();
