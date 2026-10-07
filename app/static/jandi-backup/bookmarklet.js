/*
 * 잔디 백업 북마크 주소 만들기 — /jandi-backup 화면과 node 시험이 같은 함수를 쓴다.
 *
 * 본체(jandi-backup.js) 전체를 `javascript:` 주소 안에 넣는다. 북마크는 실행 중
 * 셀라에 접속하지 않는다 (잔디 탭에서 사설 주소를 부르면 브라우저가 권한을 묻는다).
 *  - 함수로 한 번 감싼다: 본체 끝이 주석이어도 뒤에 붙는 코드가 먹히지 않게 줄을 바꾼다
 *  - 끝의 void 0: 북마크 결과값으로 화면이 바뀌지 않게
 *
 * ⛔ 주소가 길면 끌어다 놓기가 안 된다 (2026-10-07 실사용 제보: 29.6만 자에서 "버튼이 안 잡힘",
 *    16.7만 자일 때는 됐다). 그래서 주석 줄·들여쓰기를 빼고 UTF-8 → base64 로 넣는다.
 *    base64 글자(A-Z a-z 0-9 + / =)는 주소에 그대로 둘 수 있어 한글 %XX 로 세 배 불던 것이 없어진다.
 *    ⚠️ 본체에 백틱 문자열이 없어야 줄 앞 공백을 지워도 안전하다 (node 시험이 지킨다).
 *    풀어서 실행은 (0,eval) — 잔디 앱 화면에는 CSP 가 없다 (2026-10-07 확인).
 */
(function (root) {
  'use strict';

  function wrapBookmarkletCode(code) {
    var body = String(code).replace(/^﻿/, '');
    return '(function(){\n' + body + '\n})();void 0;';
  }

  // 주석만 있는 줄과 줄 앞 공백·빈 줄을 뺀다 — 토큰은 그대로라 동작이 같다
  function shrinkCode(code) {
    return String(code).replace(/^﻿/, '').split('\n')
      .map(function (l) { return l.replace(/^[ \t]+/, '').replace(/[ \t\r]+$/, ''); })
      .filter(function (l) { return l && l.slice(0, 2) !== '//'; })
      .join('\n');
  }

  function toBase64Utf8(s) {
    if (typeof Buffer !== 'undefined') return Buffer.from(s, 'utf8').toString('base64');
    return btoa(unescape(encodeURIComponent(s)));   // 브라우저: UTF-8 바이트를 latin1 글자로 바꿔 btoa
  }

  function toBookmarkletHref(code) {
    var b64 = toBase64Utf8(wrapBookmarkletCode(shrinkCode(code)));
    return 'javascript:' + encodeURIComponent('(function(){(0,eval)(decodeURIComponent(escape(atob("') + b64 +
      encodeURIComponent('"))))})();void 0;');
  }

  var api = { wrapBookmarkletCode: wrapBookmarkletCode, shrinkCode: shrinkCode, toBookmarkletHref: toBookmarkletHref };
  if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
  else root.JandiBackupBookmarklet = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
