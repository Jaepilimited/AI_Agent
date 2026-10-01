/*
 * 잔디 백업 도우미 — 북마크 본체 (셀라 /jandi-backup 에서 끌어다 놓는다).
 *
 * 로그인한 잔디 웹 탭에서 실행되어, 이 계정이 볼 수 있는 대화와 첨부파일을
 * 사용자가 고른 내 PC 폴더에 곧장 쓴다 (File System Access API).
 *
 * 불변식 (CLAUDE.md "잔디 백업 도우미" 절):
 *  - 셀라 서버로는 아무것도 보내지 않는다. 실행 중 셀라에서 아무것도 받아 오지도 않는다
 *    (코드 전체가 북마크 주소 안에 들어 있다 — 사설 주소를 부르면 브라우저가 권한을 묻는다).
 *  - 첨부 다운로드 주소는 약 1분 뒤 만료된다. 파일마다 받기 직전에 새로 요청한다.
 *    미리 한꺼번에 받아 두지 않는다.
 *  - 방 하나를 다 모으면 바로 디스크에 쓰고 메모리에서 버린다 (방 484개 · 기록 41만 건 규모).
 *  - 출력 형식은 qa_artifacts/jandi_backup/render.py · finalize_all.py 와 같다.
 *
 * 순수 함수는 node 에서 시험한다 (module.exports). 브라우저에서는 패널을 띄운다.
 */
(function (root) {
  'use strict';

  var VERSION = '2026-10-01';
  var KST_MS = 9 * 3600 * 1000;
  var KIND_LABEL = { topic: '토픽', private_topic: '비공개토픽', chat: '1대1', groupChat: '그룹DM' };
  var KIND_ORDER = ['topic', 'private_topic', 'chat', 'groupChat'];
  var IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'];
  var HASH_LIMIT = 50 * 1024 * 1024;
  var FILE_NAME_MAX = 80;
  var MESSAGES_API = 'https://i1.jandi.com/message-api/v2/teams/';

  // ── 파이썬 의미 흉내 (render.py 와 같은 결과를 내기 위해) ─────────────────

  function truthy(v) {
    if (v === null || v === undefined || v === false || v === 0 || v === '') return false;
    if (typeof v === 'number' && isNaN(v)) return false;
    if (Array.isArray(v)) return v.length > 0;
    if (typeof v === 'object') return Object.keys(v).length > 0;
    return true;
  }

  function has(obj, key) {
    return !!obj && typeof obj === 'object' && Object.prototype.hasOwnProperty.call(obj, key);
  }

  // dict.get(key, default)
  function pick(obj, key, dflt) {
    return has(obj, key) ? obj[key] : dflt;
  }

  // str(value) — None/True/False 철자까지
  function pyStr(v) {
    if (v === null || v === undefined) return 'None';
    if (v === true) return 'True';
    if (v === false) return 'False';
    if (typeof v === 'object') return pyDumps(v);
    return String(v);
  }

  // json.dumps(value, ensure_ascii=False) — 기본 구분자 ', ' / ': '
  function pyDumps(v) {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return isFinite(v) ? String(v) : (isNaN(v) ? 'NaN' : (v > 0 ? 'Infinity' : '-Infinity'));
    if (typeof v === 'string') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(pyDumps).join(', ') + ']';
    return '{' + Object.keys(v).map(function (k) { return JSON.stringify(k) + ': ' + pyDumps(v[k]); }).join(', ') + '}';
  }

  function serialized(v) {
    return typeof v === 'string' ? v : pyDumps(v);
  }

  function pad(n, w) {
    var s = String(n);
    while (s.length < (w || 2)) s = '0' + s;
    return s;
  }

  function fmtKst(ms) {
    var d = new Date(Math.floor(ms / 1000) * 1000 + KST_MS);
    if (isNaN(d.getTime())) return null;
    return pad(d.getUTCFullYear(), 4) + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + ' ' +
      pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds());
  }

  // render.py local_time — 밀리초/초 숫자 또는 ISO 문자열 → 'YYYY-MM-DD HH:MM:SS' (Asia/Seoul)
  function localTime(value) {
    if (typeof value === 'number') {
      var out = fmtKst(value > 1e11 ? value : value * 1000);
      return out === null ? String(value) : out;
    }
    if (value === null || value === undefined || value === '') return '';
    var ms = Date.parse(String(value));
    if (isNaN(ms)) return truthy(value) ? String(value) : '';
    return fmtKst(ms);
  }

  function dayKst(ms) {
    if (!ms) return '';
    var s = fmtKst(ms);
    return s ? s.slice(0, 10) : '';
  }

  // html.escape(s) — quote=True 가 기본이다
  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }

  var _encoder = null;
  // urllib.parse.quote(p, safe='/')
  function quotePath(p) {
    if (!_encoder) _encoder = new TextEncoder();
    var bytes = _encoder.encode(String(p));
    var out = '';
    for (var i = 0; i < bytes.length; i++) {
      var b = bytes[i];
      var c = String.fromCharCode(b);
      if (b < 128 && /[A-Za-z0-9_.\-~\/]/.test(c)) out += c;
      else out += '%' + (b < 16 ? '0' : '') + b.toString(16).toUpperCase();
    }
    return out;
  }

  function chars(s) { return Array.from(String(s)); }

  function rstripDotSpace(s) {
    return String(s).replace(/[. ]+$/, '');
  }

  // 윈도우가 거부하는 글자를 '_' 로, 끝의 점·공백을 걷고, 글자 수(코드포인트)로 자른다
  function sanitizeName(name, max) {
    var s = String(name === null || name === undefined ? '' : name).replace(/[<>:"\/\\|?*\x00-\x1f]/g, '_');
    s = rstripDotSpace(s);
    if (max) s = chars(s).slice(0, max).join('');
    return s;
  }

  // finalize_all.py: f"{KIND}_{rid}_{safe(name or rid)[:24].rstrip('. ')}"
  function roomFolderName(room) {
    var label = KIND_LABEL[room.kind] || String(room.kind || '방');
    var base = sanitizeName(room.name || String(room.id), 60) || 'room';
    base = rstripDotSpace(chars(base).slice(0, 24).join(''));
    return label + '_' + room.id + '_' + base;
  }

  function splitExt(name) {
    var i = name.lastIndexOf('.');
    if (i <= 0) return [name, ''];
    var ext = name.slice(i);
    if (ext.length > 11 || /\s/.test(ext)) return [name, ''];
    return [name.slice(0, i), ext];
  }

  // attachments/{fileId}_{이름} — 전체 경로가 길어지지 않게 줄기를 자르고 확장자는 지킨다
  function attachmentFileName(fileId, name, max) {
    max = max || FILE_NAME_MAX;
    var safe = sanitizeName(truthy(name) ? name : String(fileId)) || 'file';
    var full = String(fileId) + '_' + safe;
    if (chars(full).length <= max) return full;
    var parts = splitExt(full);
    var keep = Math.max(String(fileId).length + 2, max - chars(parts[1]).length);
    return rstripDotSpace(chars(parts[0]).slice(0, keep).join('')) + parts[1];
  }

  function extOf(name) {
    var base = String(name).split(/[\/\\]/).pop();
    var i = base.lastIndexOf('.');
    return i > 0 ? base.slice(i).toLowerCase() : '';
  }

  // ── 이름 ───────────────────────────────────────────────────────────────────

  function makeNamer(members) {
    var map = members || {};
    if (Array.isArray(map)) {
      var m2 = {};
      map.forEach(function (m) { if (m && m.id !== undefined) m2[String(m.id)] = m; });
      map = m2;
    }
    return function name(id) {
      if (id && typeof id === 'object' && !Array.isArray(id)) {
        return id.name || id.displayName || pyStr(pick(id, 'id', ''));
      }
      var key = id === null || id === undefined ? 'None' : String(id);
      var member = has(map, key) ? map[key] : {};
      if (member && typeof member === 'object') {
        return member.name || member.displayName || (truthy(id) ? String(id) : '시스템');
      }
      return String(member);
    };
  }

  // ── 시스템 기록 → 한국어 문장 (render.py describe_event 와 같은 표) ─────────

  function describeEvent(record, name) {
    var info = truthy(record.info) ? record.info : {};
    var ev = info.eventType || record.status || 'event';
    var extra = truthy(info.eventInfo) ? info.eventInfo : {};
    var who = name(record.fromEntity);
    var names = function (ids) {
      var list = Array.isArray(ids) ? ids : [];
      return list.map(function (i) { return name(i); }).join(', ') || '?';
    };
    var templates = {
      invite: function () { return name(info.invitorId || record.fromEntity) + ' 님이 ' + names(info.inviteUsers) + ' 님을 초대했습니다.'; },
      member_invited: function () { return who + ' 님이 ' + names(extra.members) + ' 님을 초대했습니다.'; },
      join: function () { return who + ' 님이 참여했습니다.'; },
      member_joined: function () { return who + ' 님이 참여했습니다.'; },
      leave: function () { return who + ' 님이 나갔습니다.'; },
      member_left: function () { return who + ' 님이 나갔습니다.'; },
      member_kicked_out: function () { return who + ' 님이 ' + names(extra.members) + ' 님을 내보냈습니다.'; },
      kickout: function () { return name(extra.writerId || record.fromEntity) + ' 님이 ' + names(extra.kickedIds) + ' 님을 내보냈습니다.'; },
      create: function () { return who + ' 님이 방을 만들었습니다.'; },
      room_created: function () { return who + ' 님이 방을 만들었습니다. 참여자: ' + names(extra.members); },
      room_updated: function () { return who + ' 님이 방 설정을 변경했습니다.'; },
      name_updated: function () { return who + ' 님이 방 이름을 변경했습니다.'; },
      admin_changed: function () { return who + ' 님이 관리자를 변경했습니다. 관리자: ' + names(extra.members); },
      announcement_created: function () { return who + ' 님이 공지를 등록했습니다.' + (truthy(extra.writerId) ? ' (작성자: ' + name(extra.writerId) + ')' : ''); },
      announcement_updated: function () { return who + ' 님이 공지를 수정했습니다.'; },
      announcement_deleted: function () { return who + ' 님이 공지를 삭제했습니다.'; }
    };
    var fn = has(templates, ev) ? templates[ev] : null;
    return { text: '[시스템] ' + (fn ? fn() : who + ' · ' + ev), kind: String(ev) };
  }

  // ── 기록 하나 → 뷰어·CSV·TXT 용 값 (render.py main 루프의 본문) ─────────────

  function recordView(record, ctx) {
    var name = ctx.name;
    var files = ctx.files;
    var message = truthy(record.message) ? record.message : {};
    var messageId = String(message.id || record.id || record.linkId || '');
    var linkId = String(record.id || record.linkId || messageId);
    var stamp = localTime(pick(record, 'time', message.createdAt));
    var sender = name(pick(message, 'writerId', pick(record, 'fromEntity', pick(record, 'writerId',
      pick(message, 'creatorId', record.creatorId)))));
    var content = pick(message, 'content', '');
    var original = (content && typeof content === 'object' && !Array.isArray(content)) ? content : {};
    if (content && typeof content === 'object' && !Array.isArray(content)) {
      content = pick(content, 'body', pick(content, 'text', serialized(content)));
    }
    var text = truthy(content) ? serialized(content) : '';
    var kind = pyStr(pick(message, 'contentType', pick(record, 'eventType', pick(record, 'type', 'system'))));
    var isSticker = kind.toLowerCase().indexOf('sticker') >= 0;
    // 옛 방식 파일 메시지는 본문이 없다 — 파일 정보 JSON 원문 대신 파일 이름을 보인다
    if (kind === 'file' && !has(original, 'body') && !has(original, 'text') && (original.name || original.title)) {
      text = '[파일] ' + (original.name || original.title);
    }
    if (isSticker) {
      text = '[스티커 ' + pyStr(pick(original, 'groupId', '')) + '/' + pyStr(pick(original, 'stickerId', '')) + ']' +
        (truthy(original.body) ? '\n' + String(original.body) : '');
    }
    if (truthy(record.poll)) {
      var poll = record.poll;
      text = '[투표] ' + pyStr(pick(poll, 'subject', '')) + '\n' + String(poll.description || '');
      text += '\n' + (poll.items || []).map(function (item) {
        return pyStr(pick(item, 'name', '')) + ' — ' + pyStr(pick(item, 'votedCount', 0)) + '표';
      }).join('\n');
      text += '\n총 참여: ' + pyStr(pick(poll, 'votedCount', 0)) + ' · 상태: ' + pyStr(pick(poll, 'status', ''));
    }
    if (!truthy(message)) {
      var ev = describeEvent(record, name);
      text = ev.text;
      kind = ev.kind;
      sender = '시스템';
    } else if (!text) {
      text = isSticker ? '[스티커]' : '[' + kind + ']';
    }
    var parent = pick(record, 'feedbackId', null);
    if (parent === null || parent === undefined || parent === -1 || parent === '-1' || parent === 0 || parent === '0') {
      parent = message.parentId || message.replyToId || message.rootId || record.parentId;
    }
    if (parent && typeof parent === 'object') parent = parent.id;
    if (!truthy(parent)) parent = '';

    var ids = [];
    var attNames = {};
    if (isSticker) {
      var stickerId = 'sticker_' + pyStr(pick(original, 'groupId', '')) + '_' + pyStr(pick(original, 'stickerId', ''));
      if (has(files, stickerId)) ids.push(stickerId);
    }
    (message.attachments || []).forEach(function (a) {
      var fid = (a && typeof a === 'object') ? pick(a, 'id', a.fileId) : a;
      ids.push(String(fid));
      if (a && typeof a === 'object') {
        var ac = a.content || {};
        attNames[String(fid)] = ac.name || ac.title || String(fid);
      }
    });
    if (has(files, messageId)) ids.unshift(messageId);
    var seen = {};
    var attHtml = [];
    var attText = [];
    ids.forEach(function (fid) {
      if (seen[fid]) return;
      seen[fid] = true;
      var entry = has(files, fid) ? files[fid] : {};
      var path = String(entry.path || '').replace(/\\/g, '/');
      var ok = entry.status === 'downloaded' && path && path.split('/').indexOf('..') < 0 && path.charAt(0) !== '/';
      var url = ok ? quotePath(path) : null;
      var filename = entry.name || attNames[fid] || String(fid);
      attText.push(filename + (url ? ' [' + entry.path + ']' : ' [미저장]'));
      if (url) {
        attHtml.push('<a class="file" href="' + escapeHtml(url) + '">' + escapeHtml(filename) + '</a>');
        if (IMAGE_EXT.indexOf(extOf(filename)) >= 0) {
          attHtml.push('<img loading="lazy" src="' + escapeHtml(url) + '" alt="' + escapeHtml(filename) + '">');
        }
      } else {
        attHtml.push('<span class="missing">' + escapeHtml(filename) + ' — 미저장</span>');
      }
    });
    var reply = parent ? '<a class="reply" href="#m-' + escapeHtml(String(parent)) + '">답장 원문 보기</a>' : '';
    var article = '<article id="m-' + escapeHtml(messageId) + '"><span id="l-' + escapeHtml(linkId) + '"></span>' +
      '<div class="meta"><strong>' + escapeHtml(sender) + '</strong><time>' + escapeHtml(stamp) + '</time></div>' +
      reply + '<div class="text">' + escapeHtml(text) + '</div>' + attHtml.join('') + '</article>';
    return {
      messageId: messageId, linkId: linkId, stamp: stamp, sender: sender, kind: kind, text: text,
      parent: parent === '' ? '' : String(parent), attachmentsText: attText, article: article
    };
  }

  function sortRecords(records) {
    var keyed = records.map(function (r) {
      return { r: r, t: localTime(pick(r, 'time', r.createdAt)), id: String(pick(r, 'id', '')) };
    });
    keyed.sort(function (a, b) {
      if (a.t < b.t) return -1;
      if (a.t > b.t) return 1;
      return a.id < b.id ? -1 : (a.id > b.id ? 1 : 0);
    });
    return keyed.map(function (k) { return k.r; });
  }

  // ── CSV (UTF-8 BOM · 수식 주입 방지) ───────────────────────────────────────

  function csvCell(v) {
    var s = v === null || v === undefined ? '' : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    if (/[",\r\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function csvRow(values) {
    return values.map(csvCell).join(',') + '\r\n';
  }

  var CSV_HEADER = ['message_id', 'link_id', 'time_Asia_Seoul', 'sender', 'type', 'text', 'reply_to', 'attachments'];

  // ── 방 뷰어 (render.py 의 페이지 그대로) ────────────────────────────────────

  var ROOM_PAGE = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' data:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>__TITLE__ — 잔디 백업</title><style>body{font:15px/1.65 system-ui,sans-serif;background:#f2f5f7;color:#172b36;margin:0}header,main{max-width:900px;margin:auto;padding:24px}header{position:sticky;top:0;background:#f2f5f7ee;backdrop-filter:blur(8px);z-index:1}h1{font-size:24px;margin:0 0 8px}p{margin:4px 0;color:#536675}input{width:100%;box-sizing:border-box;padding:12px;border:1px solid #bac7cf;border-radius:8px;font:inherit;margin-top:12px}article{background:white;border:1px solid #dde5e9;border-radius:10px;padding:18px;margin:0 0 14px;scroll-margin-top:210px}article:target{outline:3px solid #277c72}.meta{display:flex;justify-content:space-between;gap:12px}time{font-size:13px;color:#657582}.text{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:8px}a{color:#146b64;overflow-wrap:anywhere}.file,.missing,.reply{display:block;margin-top:8px}img{display:block;max-width:100%;max-height:450px;object-fit:contain;margin-top:12px}.missing{color:#a24f1b}[hidden]{display:none!important}</style></head><body><header><h1>__TITLE__</h1><p>로컬 백업 · Asia/Seoul (UTC+09:00) · __COUNT__건</p><p>첨부파일은 저장된 로컬 파일만 연결됩니다.</p><input id="search" type="search" placeholder="이름, 날짜, 대화 내용 검색" aria-label="대화 검색"><p id="count"></p></header><main>__ARTICLES__</main><script>const entries=Array.from(document.querySelectorAll(\'article\'));const search=document.getElementById(\'search\');const count=document.getElementById(\'count\');function filter(){let n=0;const q=search.value.trim().toLocaleLowerCase();for(const e of entries){e.hidden=!e.textContent.toLocaleLowerCase().includes(q);if(!e.hidden)n++}count.textContent=n+\' / \'+entries.length+\'건 표시\'}search.addEventListener(\'input\',filter);function reveal(){const target=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(target){search.value=\'\';filter();target.scrollIntoView()}}window.addEventListener(\'hashchange\',reveal);filter();reveal();</script></body></html>';

  function fill(template, key, value) {
    return template.split(key).join(value);
  }

  function manifestMap(manifest) {
    var files = {};
    (Array.isArray(manifest) ? manifest : []).forEach(function (f) {
      if (f && typeof f === 'object' && has(f, 'id')) files[String(f.id)] = f;
    });
    return files;
  }

  // data = { roomName, roomId, members, records(배열 또는 id→기록) } → { html, csv, txt, count }
  function renderRoom(data, manifest) {
    var files = manifestMap(manifest);
    var ctx = { name: makeNamer(data.members), files: files };
    var records = data.records || [];
    if (!Array.isArray(records)) records = Object.keys(records).map(function (k) { return records[k]; });
    records = sortRecords(records);
    var title = data.roomName || String(data.roomId || '');
    var articles = [];
    var csv = ['﻿', csvRow(CSV_HEADER)];
    var plain = [];
    records.forEach(function (record) {
      var v = recordView(record, ctx);
      articles.push(v.article);
      csv.push(csvRow([v.messageId, v.linkId, v.stamp, v.sender, v.kind, v.text, v.parent, v.attachmentsText.join('\n')]));
      plain.push('[' + v.stamp + '] ' + v.sender + '\n' + v.text + (v.parent ? '\n답장 대상: ' + v.parent : '') +
        (v.attachmentsText.length ? '\n' + v.attachmentsText.join('\n') : ''));
    });
    var html = fill(ROOM_PAGE, '__TITLE__', escapeHtml(title));
    html = fill(html, '__COUNT__', String(records.length));
    html = fill(html, '__ARTICLES__', articles.join(''));
    // 메모장용 줄바꿈 — render.py 도 윈도우에서 CRLF 로 썼다
    var txt = '﻿' + (title + '\n시간대: Asia/Seoul (UTC+09:00)\n\n' + plain.join('\n\n')).replace(/\n/g, '\r\n');
    return { html: html, csv: csv.join(''), txt: txt, count: records.length };
  }

  // ── 전체 목록 (finalize_all.py 의 index.html · summary.json) ────────────────

  function fmtInt(n) {
    return Math.round(Number(n) || 0).toLocaleString('en-US');
  }

  function fmtFixed(n, digits) {
    return (Number(n) || 0).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function sortRows(rows) {
    return rows.slice().sort(function (a, b) {
      var ka = KIND_ORDER.indexOf(a.kind); if (ka < 0) ka = 9;
      var kb = KIND_ORDER.indexOf(b.kind); if (kb < 0) kb = 9;
      if (ka !== kb) return ka - kb;
      return (b.last || 0) - (a.last || 0);
    });
  }

  function summaryRow(room, folder, records, manifest, stopReason) {
    var times = records.map(function (r) { return r.time; }).filter(function (t) { return truthy(t); });
    var saved = manifest.filter(function (f) { return f.status === 'downloaded'; });
    return {
      id: String(room.id), kind: room.kind, name: room.name, folder: folder, records: records.length,
      files: manifest.length, saved: saved.length,
      bytes: saved.reduce(function (s, f) { return s + (f.size || 0); }, 0),
      first: times.length ? Math.min.apply(null, times) : null,
      last: times.length ? Math.max.apply(null, times) : null,
      stop: stopReason
    };
  }

  function buildTopIndex(rows, meta) {
    rows = sortRows(rows);
    var team = meta.teamName || String(meta.teamId || '');
    var totalBytes = 0, totalRecords = 0, totalSaved = 0, totalFiles = 0;
    var trs = rows.map(function (r) {
      totalBytes += r.bytes || 0; totalRecords += r.records || 0; totalSaved += r.saved || 0; totalFiles += r.files || 0;
      return '<tr><td>' + escapeHtml(KIND_LABEL[r.kind] || r.kind) + '</td><td><a href="rooms/' + quotePath(r.folder) + '/index.html">' +
        escapeHtml(r.name || r.id) + '</a></td><td>' + fmtInt(r.records) + '</td><td>' + fmtInt(r.saved) + '/' + fmtInt(r.files) +
        '</td><td>' + fmtInt((r.bytes || 0) / 1e6) + 'MB</td><td>' + dayKst(r.first) + ' ~ ' + dayKst(r.last) + '</td></tr>';
    }).join('');
    var unfinished = rows.filter(function (r) { return r.stop !== 'first_link_reached' && r.stop !== 'no_messages'; });
    var note = unfinished.length
      ? '<p>처음 기록까지 가지 못한 방 ' + unfinished.length + '개 — 잔디가 열람을 제한했거나(access_limit) 수집이 멈췄습니다. summary.json 의 stop 을 보세요.</p>'
      : '';
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>잔디 백업 — ' + escapeHtml(team) + '</title>\n' +
      '<style>body{font:14px/1.6 system-ui,sans-serif;background:#f2f5f7;color:#172b36;margin:0}main{max-width:1100px;margin:auto;padding:24px}input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #bac7cf;border-radius:8px;font:inherit;margin:12px 0}table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:6px 10px;border-bottom:1px solid #e3e9ec;text-align:left}td:nth-child(n+3){text-align:right;white-space:nowrap}a{color:#146b64}[hidden]{display:none}</style></head>\n' +
      '<body><main><h1>잔디 백업 (' + escapeHtml(team) + ')</h1><p>백업일 ' + escapeHtml(meta.date || '') + ' · 방 ' + rows.length + '개 · 기록 ' + fmtInt(totalRecords) +
      '건 · 첨부 ' + fmtInt(totalSaved) + '/' + fmtInt(totalFiles) + '개 (' + fmtFixed(totalBytes / 1e9, 1) + 'GB)</p>\n' +
      '<p>방 이름을 누르면 해당 방의 대화 뷰어가 열립니다. 각 방 폴더에 CSV·TXT·원본 JSON·첨부파일이 있습니다. 무엇이 들어 있고 무엇이 빠졌는지는 README.txt 에 적었습니다.</p>' + note + '\n' +
      '<input id="q" type="search" placeholder="방 이름 검색"><table><thead><tr><th>종류</th><th>방</th><th>기록</th><th>첨부(저장/전체)</th><th>용량</th><th>기간</th></tr></thead><tbody>' + trs + '</tbody></table></main>\n' +
      '<script>const q=document.getElementById(\'q\');q.oninput=()=>{for(const tr of document.querySelectorAll(\'tbody tr\'))tr.hidden=!tr.textContent.toLowerCase().includes(q.value.toLowerCase())}</script></body></html>';
  }

  function buildReadme(meta) {
    return '﻿' + [
      '잔디 백업 — ' + (meta.teamName || meta.teamId),
      '백업 시작: ' + (meta.startedAt || '') + ' (한국 시간)',
      '만든 도구: 셀라 잔디 백업 도우미 (' + VERSION + ')',
      '',
      '[여는 방법]',
      '- 이 폴더의 index.html 을 브라우저로 열면 방 목록이 나옵니다. 방 이름을 누르면 그 방의 대화 뷰어가 열리고,',
      '  이름·날짜·대화 내용으로 검색할 수 있습니다. 인터넷 연결 없이 열립니다.',
      '- rooms 폴더 아래 방마다 폴더가 있습니다:',
      '  index.html(대화 뷰어) · messages.csv(엑셀용) · messages.txt(텍스트) · raw.json(잔디 원본 데이터)',
      '  files_manifest.json(첨부파일 목록·크기·SHA-256·실패 사유) · attachments(첨부파일)',
      '- summary.json: 방별 기록 수·첨부 수·용량·기간·수집 종료 사유(stop)',
      '',
      '[들어 있는 것]',
      '- 백업한 계정이 볼 수 있는 대화만: 참여 중인 토픽(공개·비공개), 1:1 대화, 그룹 대화 중 고른 것',
      '- 메시지·댓글·스티커·투표·시스템 기록(초대·참여·나감·공지 등)',
      '- ' + (meta.attachments ? '첨부파일과 스티커 이미지 (받지 못한 파일은 files_manifest.json 에 사유와 함께 적힘)' : '첨부파일은 이번 백업에서 제외했습니다 (뷰어에 "미저장" 으로 표시)'),
      '',
      '[들어 있지 않은 것]',
      '- 참여하지 않은 토픽, 이 계정이 볼 수 없는 대화',
      '- 이미 삭제된 메시지·파일, 수정 전 내용(편집 이력)',
      '- 잔디가 열람을 제한한 오래된 기록 (그런 방은 summary.json 의 stop 이 access_limit)',
      '',
      '[알아 둘 것]',
      '- 열람용 보관본입니다. 잔디로 다시 가져와 복원할 수 없습니다.',
      '- 작성자 이름은 백업 시점의 표시 이름입니다.',
      '- 이 파일들은 이 PC 에서만 만들어졌습니다. 셀라 서버는 대화 내용을 받지 않았습니다.',
      '- 같은 폴더를 골라 다시 실행하면 끝난 방(done.json 이 있는 방)은 건너뛰고, 받지 못한 첨부파일만 다시 받습니다.',
      ''
    ].join('\r\n');
  }

  // ── 첨부 작업 목록 ─────────────────────────────────────────────────────────

  // 한 방의 기록에서 받을 파일을 모은다 (방 안에서 id 로 중복 제거)
  function collectFileJobs(records) {
    var jobs = [];
    var seen = {};
    function add(job) {
      var key = String(job.id);
      if (seen[key]) return;
      seen[key] = true;
      jobs.push(job);
    }
    records.forEach(function (r) {
      [r.message, r.feedback].forEach(function (m, idx) {
        if (!m || typeof m !== 'object') return;
        (m.attachments || []).forEach(function (a) {
          if (!a || typeof a !== 'object') return;
          var fid = pick(a, 'id', a.fileId);
          if (fid === undefined || fid === null) return;
          var c = a.content || {};
          add({ id: fid, name: c.name || c.title || String(fid), size: c.size || null, type: c.type || null, content: c, source: 'attachment' });
        });
        var c = m.content;
        if (c && typeof c === 'object' && truthy(c.stickerId) && truthy(c.url)) {
          var sid = 'sticker_' + pyStr(pick(c, 'groupId', '')) + '_' + pyStr(c.stickerId);
          add({ id: sid, name: sid + '.png', size: null, type: null, kind: 'sticker', url: c.url, source: 'sticker' });
        }
        // 옛 방식 파일 메시지 — 메시지 자체가 파일이다 (메시지 id = 파일 id). 댓글이 가리키는 원본(feedback)은 제 방에서 받는다
        if (idx === 0 && m.contentType === 'file' && c && typeof c === 'object' && (c.name || c.title) && m.id !== undefined) {
          add({ id: m.id, name: c.name || c.title, size: c.size || null, type: c.type || null, content: c, source: 'file_message' });
        }
      });
    });
    return jobs;
  }

  // ── 페이지 넘김 판정 ────────────────────────────────────────────────────────

  function pageParams(cursor) {
    return cursor === null || cursor === undefined ? { count: 100 } : { count: 100, type: 'old', linkId: cursor };
  }

  // 응답 한 장을 보고 더 넘길지 정한다. min = 이 장에서 가장 작은 기록 id
  function decidePage(data, cursor) {
    var recs = Array.isArray(data.records) ? data.records : [];
    var min = null;
    recs.forEach(function (r) {
      var id = r && r.id !== null && r.id !== '' ? Number(r.id) : NaN;
      if (isFinite(id) && (min === null || id < min)) min = id;
    });
    var first = data.firstLinkId !== null && data.firstLinkId !== undefined && data.firstLinkId !== '' ? Number(data.firstLinkId) : NaN;
    if (data.isLimited) return { done: true, stopReason: 'access_limit', min: min };
    if (!recs.length || min === null) return { done: true, stopReason: 'first_link_reached', min: min };
    if (isFinite(first) && min <= first) return { done: true, stopReason: 'first_link_reached', min: min };
    if (cursor !== null && cursor !== undefined && min >= Number(cursor)) return { done: true, stopReason: 'no_progress', min: min };
    return { done: false, stopReason: null, min: min };
  }

  // 메시지가 없는 방은 API 를 부르지 않는다. 목록에 lastLinkId 칸 자체가 없으면 판단하지 않는다
  function isEmptyRoom(room) {
    return has(room, 'lastLinkId') && !room.lastLinkId;
  }

  function teamNameFrom(title, hostname) {
    var t = String(title || '');
    var i = t.lastIndexOf(' | ');
    var name = i >= 0 ? t.slice(i + 3).trim() : '';
    if (!name) name = String(hostname || '').split('.')[0];
    return name;
  }

  function backupFolderName(teamLabel, ymd) {
    return '잔디백업_' + (sanitizeName(teamLabel, 40) || 'team') + '_' + ymd;
  }

  function ymdKst(ms) {
    var s = fmtKst(ms);
    return s ? s.slice(0, 10).replace(/-/g, '') : '';
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return fmtFixed(n / 1e9, 1) + 'GB';
    if (n >= 1e6) return fmtFixed(n / 1e6, 0) + 'MB';
    if (n >= 1e3) return fmtFixed(n / 1e3, 0) + 'KB';
    return n + 'B';
  }

  function fmtDuration(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h) return h + '시간 ' + m + '분';
    if (m) return m + '분 ' + sec + '초';
    return sec + '초';
  }

  var lib = {
    VERSION: VERSION, KIND_LABEL: KIND_LABEL, truthy: truthy, pyDumps: pyDumps, localTime: localTime, dayKst: dayKst,
    escapeHtml: escapeHtml, quotePath: quotePath, sanitizeName: sanitizeName, roomFolderName: roomFolderName,
    attachmentFileName: attachmentFileName, makeNamer: makeNamer, describeEvent: describeEvent, recordView: recordView,
    sortRecords: sortRecords, csvCell: csvCell, csvRow: csvRow, renderRoom: renderRoom, summaryRow: summaryRow,
    sortRows: sortRows, buildTopIndex: buildTopIndex, buildReadme: buildReadme, collectFileJobs: collectFileJobs,
    pageParams: pageParams, decidePage: decidePage, isEmptyRoom: isEmptyRoom, teamNameFrom: teamNameFrom,
    backupFolderName: backupFolderName, ymdKst: ymdKst, fmtBytes: fmtBytes, fmtDuration: fmtDuration
  };

  if (typeof module !== 'undefined' && module && module.exports) {
    module.exports = lib;
    return;
  }
  if (typeof window === 'undefined' || typeof document === 'undefined') return;
  boot(window, lib);

  // ══ 여기부터 브라우저 전용 ══════════════════════════════════════════════════

  function boot(win, L) {
    var existing = win.__jandiBackup;
    if (existing && typeof existing.show === 'function') {
      existing.show();
      return;
    }
    if (!/(^|\.)jandi\.com$/i.test(String(win.location && win.location.hostname || ''))) {
      win.alert('잔디 백업: 잔디 웹 화면(https://팀이름.jandi.com/app)에 로그인한 탭에서 북마크를 눌러 주세요.');
      return;
    }
    var app = createApp(win, L);
    win.__jandiBackup = app.handle;
    app.open();
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function StopSignal() { this.message = '중지됨'; this.isStop = true; }

  function errText(e) {
    if (!e) return '알 수 없는 오류';
    if (e.status !== undefined) return 'HTTP ' + e.status;
    return (e.name ? e.name + ': ' : '') + (e.message || String(e));
  }

  function hex(buf) {
    var a = new Uint8Array(buf), s = '';
    for (var i = 0; i < a.length; i++) s += (a[i] < 16 ? '0' : '') + a[i].toString(16);
    return s;
  }

  function Semaphore(n) {
    var active = 0, queue = [];
    this.acquire = function () {
      if (active < n) { active++; return Promise.resolve(); }
      return new Promise(function (r) { queue.push(r); });
    };
    this.release = function () {
      var next = queue.shift();
      if (next) next(); else active--;
    };
  }

  function createApp(win, L) {
    var doc = win.document;
    var S = {
      version: L.VERSION, phase: 'intro', message: '', teamId: null, teamName: '', rooms: [], members: {},
      opts: { kinds: { topic: true, chat: true, groupChat: true }, attachments: true },
      root: null, rootName: '', startedAt: 0, finishedAt: 0, paused: false, stopped: false,
      progress: { roomsTotal: 0, roomsDone: 0, roomsSkipped: 0, roomsFailed: 0, filesTotal: 0, filesDone: 0, filesFailed: 0, bytes: 0, records: 0 },
      current: {}, roomFailures: [], fileFailures: [], abort: null
    };
    var host = null, shadow = null, timer = null, ctlKey = '';
    var inj = null, http = null, fileApi = null;

    var STYLE = ':host{all:initial}.p{position:fixed;top:16px;right:16px;width:380px;max-height:calc(100vh - 32px);overflow:auto;z-index:2147483000;' +
      'background:#fff;color:#172b36;border:1px solid #c9d4da;border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.18);' +
      'font:13px/1.55 system-ui,-apple-system,"Malgun Gothic",sans-serif}' +
      '.hd{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid #e3e9ec;font-weight:700;font-size:14px}' +
      '.x{border:0;background:none;font-size:18px;cursor:pointer;color:#536675;padding:0 4px}' +
      '.bd{padding:12px 14px}p{margin:0 0 8px}.muted{color:#657582;font-size:12px}' +
      'label{display:flex;gap:8px;align-items:center;margin:4px 0;cursor:pointer}' +
      '.btn{display:block;width:100%;margin-top:10px;padding:9px;border-radius:8px;border:1px solid #146b64;background:#146b64;color:#fff;font:inherit;font-weight:600;cursor:pointer}' +
      '.btn:disabled{opacity:.5;cursor:not-allowed}.btn.sub{background:#fff;color:#146b64}.row{display:flex;gap:8px}.row .btn{margin-top:6px}' +
      '.stat{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-variant-numeric:tabular-nums;margin:6px 0}' +
      '.stat b{font-weight:600}.warn{color:#a24f1b}.bar{height:6px;background:#e3e9ec;border-radius:3px;overflow:hidden;margin:6px 0}' +
      '.bar i{display:block;height:100%;background:#146b64}ul{margin:4px 0 8px;padding-left:18px;max-height:160px;overflow:auto}li{margin:1px 0;overflow-wrap:anywhere}' +
      'code{background:#f2f5f7;padding:0 4px;border-radius:4px}';

    function el(sel) { return shadow.querySelector(sel); }

    function mount() {
      host = doc.createElement('div');
      host.id = '__jandi-backup-host';
      shadow = host.attachShadow({ mode: 'open' });
      shadow.innerHTML = '<style>' + STYLE + '</style><div class="p" role="dialog" aria-label="잔디 백업"><div class="hd"><span>잔디 백업</span>' +
        '<button class="x" type="button" title="닫기" aria-label="닫기">×</button></div><div class="bd"></div></div>';
      el('.x').addEventListener('click', close);
      doc.body.appendChild(host);
    }

    function show() {
      if (!host || !host.isConnected) mount();
      host.style.display = '';
      render();
    }

    function close() {
      if (host) host.style.display = 'none';
    }

    function setBody(html) { el('.bd').innerHTML = html; }

    function counts() {
      var c = { topic: 0, chat: 0, groupChat: 0 };
      S.rooms.forEach(function (r) { c[r.kind === 'private_topic' ? 'topic' : r.kind]++; });
      return c;
    }

    function render() {
      if (!shadow) return;
      if (S.phase === 'error') {
        setBody('<p class="warn">' + L.escapeHtml(S.message) + '</p><button class="btn sub" type="button" data-act="again">다시 시도</button>');
        el('[data-act=again]').addEventListener('click', open);
      } else if (S.phase === 'intro') {
        renderIntro();
      } else {
        renderProgress();
      }
    }

    function renderIntro() {
      var c = counts();
      var supported = typeof win.showDirectoryPicker === 'function';
      setBody(
        '<p>이 계정이 볼 수 있는 잔디 대화와 첨부파일을 <b>내 PC 폴더</b>에 저장합니다. 셀라 서버로는 아무것도 보내지 않습니다.</p>' +
        '<p class="muted">팀: ' + L.escapeHtml(S.teamName || S.teamId) + ' · 토픽 ' + c.topic + ' / 1:1 ' + c.chat + ' / 그룹 ' + c.groupChat + '</p>' +
        '<label><input type="checkbox" data-k="topic"' + (S.opts.kinds.topic ? ' checked' : '') + '> 토픽 (공개·비공개, 참여 중인 것) ' + c.topic + '개</label>' +
        '<label><input type="checkbox" data-k="chat"' + (S.opts.kinds.chat ? ' checked' : '') + '> 1:1 대화 ' + c.chat + '개</label>' +
        '<label><input type="checkbox" data-k="groupChat"' + (S.opts.kinds.groupChat ? ' checked' : '') + '> 그룹 대화 ' + c.groupChat + '개</label>' +
        '<label><input type="checkbox" data-k="attachments"' + (S.opts.attachments ? ' checked' : '') + '> 첨부파일 포함</label>' +
        '<p class="muted">첨부파일을 포함하면 수십 GB · 몇 시간이 걸릴 수 있습니다. 디스크 여유를 확인하고, 끝날 때까지 이 탭을 닫지 마세요. ' +
        '바탕화면 등에 빈 폴더를 하나 만들어 고르는 것을 권합니다. 같은 폴더로 다시 실행하면 이어서 받습니다.</p>' +
        (supported ? '' : '<p class="warn">이 브라우저는 폴더 저장을 지원하지 않습니다. PC 의 Chrome 또는 Edge 에서 열어 주세요.</p>') +
        '<button class="btn" type="button" data-act="start"' + (supported ? '' : ' disabled') + '>저장 폴더 선택 후 시작</button>' +
        '<p class="muted" data-msg></p>');
      Array.prototype.forEach.call(shadow.querySelectorAll('input[type=checkbox]'), function (box) {
        box.addEventListener('change', function () {
          var k = box.getAttribute('data-k');
          if (k === 'attachments') S.opts.attachments = box.checked; else S.opts.kinds[k] = box.checked;
        });
      });
      el('[data-act=start]').addEventListener('click', onStartClick);
    }

    function introMessage(text) {
      var m = el('[data-msg]');
      if (m) { m.textContent = text; m.className = 'warn'; }
    }

    // ⚠️ showDirectoryPicker 는 사용자 클릭 안에서 곧바로 불러야 한다 — 앞에 await 를 두지 않는다
    function onStartClick() {
      if (typeof win.showDirectoryPicker !== 'function') {
        introMessage('이 브라우저는 폴더 저장을 지원하지 않습니다. PC 의 Chrome 또는 Edge 에서 열어 주세요.');
        return;
      }
      var selected = selectedRooms();
      if (!selected.length) { introMessage('저장할 대화 종류를 하나 이상 고르세요.'); return; }
      var picking;
      try {
        picking = win.showDirectoryPicker({ id: 'jandi-backup', mode: 'readwrite', startIn: 'desktop' });
      } catch (e) {
        introMessage('폴더를 열지 못했습니다: ' + errText(e));
        return;
      }
      picking.then(function (dir) {
        return run(dir, selected);
      }, function (e) {
        if (e && e.name === 'AbortError') return;
        introMessage('폴더를 열지 못했습니다: ' + errText(e) + ' — 다른 폴더(바탕화면 안의 새 폴더 등)를 골라 보세요.');
      });
    }

    function selectedRooms() {
      return S.rooms.filter(function (r) {
        var k = r.kind === 'private_topic' ? 'topic' : r.kind;
        return !!S.opts.kinds[k];
      });
    }

    function renderProgress() {
      var p = S.progress;
      var now = S.finishedAt || Date.now();
      var elapsed = S.startedAt ? now - S.startedAt : 0;
      var handled = p.roomsDone + p.roomsSkipped + p.roomsFailed;
      var frac = p.roomsTotal ? handled / p.roomsTotal : 0;
      var eta = (S.phase === 'running' && frac > 0.02 && frac < 1) ? '약 ' + L.fmtDuration(elapsed / frac - elapsed) + ' 남음 (대략)' : '';
      var cur = Object.keys(S.current).map(function (k) { return '<li>' + L.escapeHtml(S.current[k]) + '</li>'; }).join('');
      var head;
      if (S.phase === 'running') head = S.paused ? '<p><b>일시정지됨</b> — 진행 중이던 파일만 마저 받습니다.</p>' : '<p><b>백업 중입니다.</b> 이 탭을 닫지 마세요. 패널은 닫아도 계속됩니다.</p>';
      else if (S.phase === 'stopping') head = '<p><b>중지하는 중…</b></p>';
      else if (S.phase === 'stopped') head = '<p><b>중지했습니다.</b> 끝난 방까지만 저장됐습니다. 같은 폴더를 골라 다시 시작하면 이어서 받습니다.</p>';
      else head = '<p><b>백업을 마쳤습니다.</b></p>';
      var html = head +
        '<div class="bar"><i style="width:' + Math.round(frac * 100) + '%"></i></div>' +
        '<div class="stat">' +
        '<span>방</span><b>' + handled + ' / ' + p.roomsTotal + (p.roomsSkipped ? ' (이미 끝난 방 ' + p.roomsSkipped + ')' : '') + '</b>' +
        '<span>기록</span><b>' + p.records.toLocaleString('en-US') + '건</b>' +
        '<span>첨부</span><b>' + p.filesDone.toLocaleString('en-US') + ' / ' + p.filesTotal.toLocaleString('en-US') + ' · ' + L.fmtBytes(p.bytes) + '</b>' +
        '<span>실패</span><b' + (p.roomsFailed || p.filesFailed ? ' class="warn"' : '') + '>방 ' + p.roomsFailed + ' · 파일 ' + p.filesFailed + '</b>' +
        '<span>경과</span><b>' + L.fmtDuration(elapsed) + (eta ? ' · ' + eta : '') + '</b>' +
        '</div>';
      if (S.phase === 'running' || S.phase === 'stopping') {
        if (cur) html += '<p class="muted">지금 받는 방</p><ul>' + cur + '</ul>';
      } else {
        html += '<p>고른 폴더 안 <code>' + L.escapeHtml(S.rootName) + '</code> 의 <b>index.html</b> 을 브라우저로 여세요. 무엇이 들어 있는지는 README.txt 에 적었습니다.</p>';
        if (S.roomFailures.length) {
          html += '<p class="warn">받지 못한 방 ' + S.roomFailures.length + '개 (다시 실행하면 그 방만 다시 받습니다)</p><ul>' +
            S.roomFailures.map(function (f) { return '<li>' + L.escapeHtml(f.name + ' — ' + f.error) + '</li>'; }).join('') + '</ul>';
        }
        if (S.fileFailures.length) {
          html += '<p class="warn">받지 못한 첨부 ' + S.fileFailures.length + '개 (서버에서 지워진 파일일 수 있습니다 · 방별 files_manifest.json 에 사유)</p><ul>' +
            S.fileFailures.slice(0, 50).map(function (f) { return '<li>' + L.escapeHtml(f.room + ' · ' + f.name + ' — ' + f.error) + '</li>'; }).join('') +
            (S.fileFailures.length > 50 ? '<li>… 외 ' + (S.fileFailures.length - 50) + '개</li>' : '') + '</ul>';
        }
      }
      // ⚠️ 0.7초마다 다시 그린다 — 버튼까지 매번 갈아 끼우면 누르는 사이에 버튼이 바뀌어 클릭이 사라진다.
      //    숫자 칸만 갈고, 버튼은 상태(진행·일시정지·중지중)가 바뀔 때만 새로 만든다
      var live = el('[data-live]');
      if (!live) {
        setBody('<div data-live></div><div data-ctl></div>');
        live = el('[data-live]');
        ctlKey = '';
      }
      live.innerHTML = html;
      var key = (S.phase === 'running' || S.phase === 'stopping') ? S.phase + '|' + S.paused : 'end';
      if (key === ctlKey) return;
      ctlKey = key;
      var ctl = el('[data-ctl]');
      if (key === 'end') {
        // 중지했거나 끝난 뒤 — 같은 폴더를 골라 이어 받으려면 처음 화면으로
        ctl.innerHTML = '<button class="btn sub" type="button" data-act="again">처음 화면으로</button>';
        el('[data-act=again]').addEventListener('click', open);
        return;
      }
      ctl.innerHTML = '<div class="row"><button class="btn sub" type="button" data-act="pause"' + (S.phase === 'stopping' ? ' disabled' : '') + '>' +
        (S.paused ? '재개' : '일시정지') + '</button>' +
        '<button class="btn sub" type="button" data-act="stop"' + (S.phase === 'stopping' ? ' disabled' : '') + '>중지</button></div>';
      el('[data-act=pause]').addEventListener('click', function () { S.paused = !S.paused; render(); });
      el('[data-act=stop]').addEventListener('click', function () {
        if (!win.confirm('백업을 중지할까요? 끝난 방까지는 저장돼 있고, 같은 폴더로 다시 시작하면 이어서 받습니다.')) return;
        S.stopped = true; S.paused = false; S.phase = 'stopping';
        if (S.abort) S.abort.abort();
        render();
      });
    }

    // ── 잔디 세션 읽기 ──────────────────────────────────────────────────────

    function svc(name) {
      try { return inj.has && !inj.has(name) ? null : inj.get(name); } catch (e) { return null; }
    }

    function listFrom(s, preferJson) {
      if (!s) return [];
      var d = s._data;
      var tryJson = function () {
        try {
          if (typeof s.toJSON === 'function') {
            var j = s.toJSON();
            if (Array.isArray(j)) return j;
            if (j && typeof j === 'object') return Object.keys(j).map(function (k) { return j[k]; });
          }
        } catch (e) { /* 다음 후보 */ }
        return null;
      };
      if (preferJson) { var j1 = tryJson(); if (j1 && j1.length) return j1; }
      var cands = [d && d.entityId && d.entityId.list, d && d.list, Array.isArray(d) ? d : null];
      for (var i = 0; i < cands.length; i++) if (Array.isArray(cands[i]) && cands[i].length) return cands[i];
      var j2 = tryJson();
      if (j2) return j2;
      if (d && typeof d === 'object') return Object.keys(d).map(function (k) { return d[k]; }).filter(function (v) { return v && typeof v === 'object' && v.id !== undefined; });
      return [];
    }

    function readMembers() {
      var out = {};
      ['UserList', 'BotList'].forEach(function (n) {
        listFrom(svc(n), true).forEach(function (m) {
          if (m && m.id !== undefined && m.id !== null) out[String(m.id)] = { id: m.id, name: m.name || m.displayName || String(m.id), status: m.status };
        });
      });
      return out;
    }

    function readRooms(members) {
      var rooms = [], seen = {};
      function add(r, kind, fallbackName) {
        var id = r && (r.id !== undefined ? r.id : r.roomId);
        if (id === undefined || id === null || seen[String(id)]) return;
        seen[String(id)] = true;
        var room = { id: id, kind: kind, name: r.name || fallbackName || String(id), teamId: r.teamId };
        if (has(r, 'lastLinkId')) room.lastLinkId = r.lastLinkId;
        if (r.viewType) room.viewType = r.viewType;
        rooms.push(room);
      }
      var T = svc('RoomTopicList');
      listFrom(T, true).forEach(function (t) {
        if (!t || t.id === undefined) return;
        var joined = true;
        try {
          if (T && typeof T.isJoined === 'function') joined = !!T.isJoined(t.id);
          else if (has(t, 'isJoined')) joined = !!t.isJoined;
        } catch (e) { joined = true; }
        if (joined) add(t, t.type === 'privateGroup' ? 'private_topic' : 'topic');
      });
      var memberNames = function (r) {
        var ids = Array.isArray(r.members) ? r.members : [];
        return ids.slice(0, 6).map(function (m) {
          var id = m && typeof m === 'object' ? m.id : m;
          return (members[String(id)] || {}).name || String(id);
        }).join(', ');
      };
      listFrom(svc('RoomChatDmList'), false).forEach(function (r) { add(r, 'chat', memberNames(r)); });
      listFrom(svc('RoomChatGroupList'), false).forEach(function (r) { add(r, 'groupChat', memberNames(r)); });
      return rooms;
    }

    function prepare() {
      if (String(win.location.pathname || '').indexOf('/app') !== 0) {
        throw new Error('잔디 웹 앱 화면(https://팀이름.jandi.com/app/…)에서 실행해 주세요. 지금 화면은 앱 화면이 아닙니다.');
      }
      if (!win.angular || typeof win.angular.element !== 'function') {
        throw new Error('잔디 앱이 아직 다 열리지 않았거나 화면 구조가 바뀌었습니다. 대화 목록이 보일 때 다시 눌러 주세요.');
      }
      try { inj = win.angular.element(doc.body).injector(); } catch (e) { inj = null; }
      if (!inj) throw new Error('잔디 앱 정보를 읽지 못했습니다. 페이지를 새로고침한 뒤 다시 눌러 주세요.');
      http = svc('$http');
      fileApi = svc('FileApi');
      if (!http) throw new Error('잔디 앱 구조가 바뀌어 대화를 읽을 수 없습니다. (잔디 업데이트로 이 도구가 멈췄을 수 있습니다.)');
      S.members = readMembers();
      S.rooms = readRooms(S.members);
      var withTeam = S.rooms.filter(function (r) { return r.teamId; })[0];
      S.teamId = withTeam ? withTeam.teamId : null;
      if (!S.rooms.length || !S.teamId) {
        throw new Error('로그인한 대화 목록을 찾지 못했습니다. 잔디에 로그인돼 있는지, 왼쪽에 대화 목록이 보이는지 확인한 뒤 다시 눌러 주세요.');
      }
      S.teamName = L.teamNameFrom(doc.title, win.location.hostname);
    }

    // ── 실행 ────────────────────────────────────────────────────────────────

    function beforeUnload(e) {
      e.preventDefault();
      e.returnValue = '';
      return '';
    }

    function gate() {
      if (S.stopped) return Promise.reject(new StopSignal());
      if (!S.paused) return Promise.resolve();
      return sleep(300).then(gate);
    }

    async function dirIf(parent, name) {
      try { return await parent.getDirectoryHandle(name); } catch (e) { return null; }
    }

    async function readTextIf(dir, name) {
      try { return await (await (await dir.getFileHandle(name)).getFile()).text(); } catch (e) { return null; }
    }

    async function sizeIf(dir, name) {
      try { return (await (await dir.getFileHandle(name)).getFile()).size; } catch (e) { return null; }
    }

    async function writeText(dir, name, text) {
      var fh = await dir.getFileHandle(name, { create: true });
      var w = await fh.createWritable();
      try {
        await w.write(text);
        await w.close();
      } catch (e) {
        try { await w.abort(); } catch (e2) { /* 이미 닫힘 */ }
        throw e;
      }
    }

    // raw.json 은 방 하나가 수십 MB 다 — 기록을 나눠 쓴다
    async function writeRaw(dir, meta, records) {
      var fh = await dir.getFileHandle('raw.json', { create: true });
      var w = await fh.createWritable();
      try {
        var head = JSON.stringify(meta);
        await w.write(head.slice(0, -1) + (head.length > 2 ? ',' : '') + '"records":{');
        var buf = [], first = true;
        for (var i = 0; i < records.length; i++) {
          buf.push(JSON.stringify(String(records[i].id)) + ':' + JSON.stringify(records[i]));
          if (buf.length >= 2000) { await w.write((first ? '' : ',') + buf.join(',')); first = false; buf = []; }
        }
        if (buf.length) await w.write((first ? '' : ',') + buf.join(','));
        await w.write('}}');
        await w.close();
      } catch (e) {
        try { await w.abort(); } catch (e2) { /* 이미 닫힘 */ }
        throw e;
      }
    }

    async function chooseRoot(picked) {
      var ymd = L.ymdKst(Date.now());
      var label = S.teamName || String(S.teamId);
      if (/^잔디백업_/.test(picked.name)) return picked;   // 백업 폴더 자체를 골랐다 → 그 안에 이어 쓴다
      var prefix = L.backupFolderName(label, '');
      var today = L.backupFolderName(label, ymd);
      var best = null;
      for await (var entry of picked.values()) {
        if (entry.kind !== 'directory' || entry.name.indexOf(prefix) !== 0 || entry.name === today) continue;
        var state = await readTextIf(entry, 'backup_state.json');
        var finished = false;
        try { finished = !!(state && JSON.parse(state).finishedAt); } catch (e) { finished = false; }
        if (!finished && (!best || entry.name > best.name)) best = entry;   // 끝나지 않은 지난 백업은 이어서
      }
      return best || picked.getDirectoryHandle(today, { create: true });
    }

    async function existingRoomFolders(roomsDir) {
      var map = {};
      for await (var entry of roomsDir.values()) {
        if (entry.kind !== 'directory') continue;
        var m = /^[^_]+_(\d+)_/.exec(entry.name);
        if (m) map[m[1]] = entry.name;
      }
      return map;
    }

    async function getPage(url, params, roomId) {
      var last = null;
      for (var attempt = 0; attempt < 4; attempt++) {
        await gate();
        try {
          var res = await http.get(url, { params: params });
          var data = res && res.data;
          if (!data || !Array.isArray(data.records)) throw new Error('응답 형식이 다릅니다');
          if (data.roomId !== undefined && String(data.roomId) !== String(roomId)) throw new Error('다른 방의 응답입니다');
          return data;
        } catch (e) {
          if (e && e.isStop) throw e;
          last = e;
          await sleep(800 * Math.pow(2, attempt));
        }
      }
      throw new Error('대화를 읽지 못했습니다 (' + errText(last) + ')');
    }

    async function collectMessages(room) {
      if (L.isEmptyRoom(room)) return { records: [], pages: [], stopReason: 'no_messages' };
      var url = MESSAGES_API + encodeURIComponent(S.teamId) + '/rooms/' + encodeURIComponent(room.id) + '/messages';
      var recs = new Map(), pages = [], cursor = null;
      for (var i = 0; i < 100000; i++) {
        var params = L.pageParams(cursor);
        var data = await getPage(url, params, room.id);
        var added = 0;
        data.records.forEach(function (r) { if (!recs.has(r.id)) added++; recs.set(r.id, r); });
        S.progress.records += added;
        var d = L.decidePage(data, cursor);
        pages.push({ n: data.records.length, added: added, firstLinkId: data.firstLinkId, isLimited: !!data.isLimited, min: d.min });
        S.current[room.id] = room.name + ' — 기록 ' + recs.size.toLocaleString('en-US') + '건';
        if (d.done) return { records: Array.from(recs.values()), pages: pages, stopReason: d.stopReason };
        cursor = d.min;
      }
      return { records: Array.from(recs.values()), pages: pages, stopReason: 'page_limit' };
    }

    var downloads = new Semaphore(8);

    async function downloadOne(job, room, attDir, prev) {
      var fileName = L.attachmentFileName(job.id, job.name);
      var entry = { roomId: room.id, id: job.id, name: job.name, size: job.size, type: job.type };
      if (job.kind) entry.kind = job.kind;
      entry.path = 'attachments/' + fileName;
      var known = job.size || 0;
      var have = await sizeIf(attDir, fileName);
      if (have !== null && (known ? have === known : have > 0)) {
        var old = prev[String(job.id)];
        S.progress.filesDone++;
        S.progress.bytes += have;
        return Object.assign(entry, { status: 'downloaded', size: have, sha256: old && old.sha256 ? old.sha256 : null });
      }
      await downloads.acquire();
      var lastErr = '받지 못함';
      try {
        for (var attempt = 0; attempt < 3; attempt++) {
          await gate();
          var url = null;
          if (job.kind === 'sticker') {
            url = job.url;
          } else {
            if (!fileApi || typeof fileApi.getImage !== 'function') { lastErr = '잔디 파일 기능을 찾지 못함'; break; }
            try {
              // ⛔ 주소는 약 1분 뒤 만료된다 — 받기 직전에, 시도마다 새로 받는다
              var r = await fileApi.getImage({ fileId: job.id, mode: 'ATTACHMENT', content: job.content });
              url = r && r.data && r.data.downloadUrl;
            } catch (e) {
              lastErr = '다운로드 주소 ' + errText(e);
              if (e && e.status >= 400 && e.status < 500) break;   // 서버에서 지워진 파일 — 다시 해도 같다
              await sleep(1000 * (attempt + 1));
              continue;
            }
            if (!url) { lastErr = '다운로드 주소 없음'; await sleep(1000 * (attempt + 1)); continue; }
          }
          try {
            var res = await fetch(url, { signal: S.abort.signal });
            if (!res.ok) {
              lastErr = 'HTTP ' + res.status;
              if (job.kind === 'sticker' && res.status === 404) break;
              await sleep(1000 * (attempt + 1));
              continue;
            }
            var lenHeader = Number(res.headers.get('content-length')) || 0;
            var expect = known || lenHeader;
            var size, sha = null;
            if (expect && expect <= HASH_LIMIT) {
              var buf = await res.arrayBuffer();
              size = buf.byteLength;
              if (known && size !== known) throw new Error('크기 불일치 ' + size + '/' + known);
              sha = hex(await win.crypto.subtle.digest('SHA-256', buf));
              await writeText(attDir, fileName, buf);
            } else {
              var fh = await attDir.getFileHandle(fileName, { create: true });
              var w = await fh.createWritable();
              await res.body.pipeTo(w, { signal: S.abort.signal });
              size = (await fh.getFile()).size;
              if (known && size !== known) throw new Error('크기 불일치 ' + size + '/' + known);
            }
            S.progress.filesDone++;
            S.progress.bytes += size;
            return Object.assign(entry, { status: 'downloaded', size: size, sha256: sha });
          } catch (e) {
            if (S.stopped) throw new StopSignal();
            lastErr = errText(e);
            await sleep(1000 * (attempt + 1));
          }
        }
      } finally {
        downloads.release();
      }
      try {
        var leftover = await sizeIf(attDir, fileName);
        if (leftover !== null && (!known || leftover !== known)) await attDir.removeEntry(fileName);
      } catch (e) { /* 지우지 못해도 다음 실행에서 크기로 걸러진다 */ }
      S.progress.filesDone++;
      S.progress.filesFailed++;
      S.fileFailures.push({ room: room.name, name: job.name, error: lastErr });
      return Object.assign(entry, { status: 'failed', error: lastErr });
    }

    async function downloadAll(jobs, room, roomDir, prevList) {
      if (!jobs.length) return [];
      var attDir = await roomDir.getDirectoryHandle('attachments', { create: true });
      var prev = {};
      (prevList || []).forEach(function (f) { if (f && f.id !== undefined) prev[String(f.id)] = f; });
      S.progress.filesTotal += jobs.length;
      return Promise.all(jobs.map(function (j) { return downloadOne(j, room, attDir, prev); }));
    }

    async function writeRoomOutputs(dir, room, folder, collected, manifest) {
      var meta = {
        roomId: room.id, roomName: room.name, kind: room.kind, teamId: S.teamId, viewType: room.viewType || null,
        stopReason: collected.stopReason, pages: collected.pages, collectedAt: new Date().toISOString(),
        tool: 'cella-jandi-backup ' + L.VERSION, members: S.members
      };
      await writeText(dir, 'files_manifest.json', JSON.stringify(manifest, null, 1));
      await writeRaw(dir, meta, collected.records);
      var out = L.renderRoom({ roomName: room.name, roomId: room.id, members: S.members, records: collected.records }, manifest);
      await writeText(dir, 'index.html', out.html);
      await writeText(dir, 'messages.csv', out.csv);
      await writeText(dir, 'messages.txt', out.txt);
      var row = L.summaryRow(room, folder, collected.records, manifest, collected.stopReason);
      // ⚠️ done.json 은 맨 마지막 — 이것이 있어야 다음 실행이 이 방을 건너뛴다
      await writeText(dir, 'done.json', JSON.stringify({ row: row, finishedAt: new Date().toISOString(), version: L.VERSION }, null, 1));
      return row;
    }

    async function processRoom(room, roomsDir, folders) {
      var folder = folders[String(room.id)] || L.roomFolderName(room);
      var dir = await roomsDir.getDirectoryHandle(folder, { create: true });
      var doneText = await readTextIf(dir, 'done.json');
      var prevManifest = [];
      try { prevManifest = JSON.parse(await readTextIf(dir, 'files_manifest.json') || '[]'); } catch (e) { prevManifest = []; }
      if (doneText) {
        var failed = prevManifest.filter(function (f) { return f.status !== 'downloaded'; });
        if (!S.opts.attachments || !failed.length) { S.progress.roomsSkipped++; return; }
        // 끝난 방 — 받지 못한 첨부만 다시
        S.current[room.id] = room.name + ' — 못 받은 첨부 ' + failed.length + '개 다시';
        var raw = JSON.parse(await readTextIf(dir, 'raw.json') || '{}');
        var records = raw.records ? Object.keys(raw.records).map(function (k) { return raw.records[k]; }) : [];
        var failedIds = {};
        failed.forEach(function (f) { failedIds[String(f.id)] = true; });
        var jobs = L.collectFileJobs(records).filter(function (j) { return failedIds[String(j.id)]; });
        var retried = await downloadAll(jobs, room, dir, prevManifest);
        var byId = {};
        retried.forEach(function (f) { byId[String(f.id)] = f; });
        var merged = prevManifest.map(function (f) { return byId[String(f.id)] || f; });
        await writeRoomOutputs(dir, room, folder, { records: records, pages: raw.pages || [], stopReason: raw.stopReason || 'first_link_reached' }, merged);
        S.progress.roomsDone++;
        return;
      }
      S.current[room.id] = room.name + ' — 읽는 중';
      var collected = await collectMessages(room);
      var fileJobs = S.opts.attachments ? L.collectFileJobs(collected.records) : [];
      if (fileJobs.length) S.current[room.id] = room.name + ' — 첨부 ' + fileJobs.length + '개 받는 중';
      var manifest = await downloadAll(fileJobs, room, dir, prevManifest);
      await gate();
      await writeRoomOutputs(dir, room, folder, collected, manifest);
      S.progress.roomsDone++;
    }

    async function pool(rooms, size, roomsDir, folders) {
      var queue = rooms.slice();
      async function worker() {
        while (queue.length && !S.stopped) {
          var room = queue.shift();
          try {
            await processRoom(room, roomsDir, folders);
          } catch (e) {
            if (e && e.isStop) return;
            S.progress.roomsFailed++;
            S.roomFailures.push({ id: room.id, name: room.name, error: errText(e) });
          } finally {
            delete S.current[room.id];
          }
        }
      }
      var workers = [];
      for (var i = 0; i < size; i++) workers.push(worker());
      await Promise.all(workers);
    }

    async function finalize(root, roomsDir, finished) {
      var rows = [];
      for await (var entry of roomsDir.values()) {
        if (entry.kind !== 'directory') continue;
        var t = await readTextIf(entry, 'done.json');
        if (!t) continue;
        try {
          var row = JSON.parse(t).row;
          if (row) { row.folder = entry.name; rows.push(row); }
        } catch (e) { /* 깨진 표식 — 다음 실행에서 다시 받는다 */ }
      }
      rows = L.sortRows(rows);
      var meta = { teamId: S.teamId, teamName: S.teamName, date: L.dayKst(S.startedAt), startedAt: L.localTime(S.startedAt), attachments: S.opts.attachments };
      await writeText(root, 'summary.json', JSON.stringify(rows, null, 1));
      await writeText(root, 'index.html', L.buildTopIndex(rows, meta));
      await writeText(root, 'README.txt', L.buildReadme(meta));
      await writeText(root, 'backup_state.json', JSON.stringify({
        teamId: S.teamId, teamName: S.teamName, startedAt: new Date(S.startedAt).toISOString(),
        finishedAt: finished ? new Date().toISOString() : null, rooms: rows.length,
        roomFailures: S.roomFailures, version: L.VERSION
      }, null, 1));
    }

    async function run(picked, selected) {
      S.phase = 'running';
      S.startedAt = Date.now();
      S.stopped = false; S.paused = false;
      S.abort = new AbortController();
      S.progress = { roomsTotal: selected.length, roomsDone: 0, roomsSkipped: 0, roomsFailed: 0, filesTotal: 0, filesDone: 0, filesFailed: 0, bytes: 0, records: 0 };
      S.roomFailures = []; S.fileFailures = []; S.current = {};
      win.addEventListener('beforeunload', beforeUnload);
      timer = win.setInterval(render, 700);
      render();
      try {
        var root = await chooseRoot(picked);
        S.root = root;
        S.rootName = root.name;
        var roomsDir = await root.getDirectoryHandle('rooms', { create: true });
        var folders = await existingRoomFolders(roomsDir);
        await writeText(root, 'backup_state.json', JSON.stringify({ teamId: S.teamId, teamName: S.teamName, startedAt: new Date(S.startedAt).toISOString(), finishedAt: null, version: L.VERSION }, null, 1));
        var dms = selected.filter(function (r) { return r.kind === 'chat'; });
        var others = selected.filter(function (r) { return r.kind !== 'chat'; });
        await pool(others, 5, roomsDir, folders);
        if (!S.stopped) await pool(dms, 8, roomsDir, folders);
        await finalize(root, roomsDir, !S.stopped);
        S.phase = S.stopped ? 'stopped' : 'done';
      } catch (e) {
        S.phase = 'error';
        S.message = '백업이 멈췄습니다: ' + errText(e) + ' — 같은 폴더를 골라 다시 시작하면 끝난 방은 건너뜁니다.';
      } finally {
        S.finishedAt = Date.now();
        win.removeEventListener('beforeunload', beforeUnload);
        if (timer) win.clearInterval(timer);
        timer = null;
        render();
      }
    }

    function open() {
      if (!host || !host.isConnected) mount();
      host.style.display = '';
      try {
        prepare();
        S.phase = 'intro';
      } catch (e) {
        S.phase = 'error';
        S.message = e.message || String(e);
      }
      render();
    }

    return {
      open: open,
      handle: {
        version: L.VERSION,
        state: S,
        lib: L,
        // 북마크를 다시 누르면 여기로 온다 — 시작 전이면 잔디 목록을 새로 읽는다
        show: function () { if (S.phase === 'intro' || S.phase === 'error') open(); else show(); },
        close: close,
        pause: function () { S.paused = true; render(); },
        resume: function () { S.paused = false; render(); }
      }
    };
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
