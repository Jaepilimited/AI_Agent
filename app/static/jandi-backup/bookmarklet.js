/*
 * 잔디 백업 북마크 주소 만들기 — /jandi-backup 화면과 node 시험이 같은 함수를 쓴다.
 *
 * 본체(jandi-backup.js) 전체를 `javascript:` 주소 안에 넣는다. 북마크는 실행 중
 * 셀라에 접속하지 않는다 (잔디 탭에서 사설 주소를 부르면 브라우저가 권한을 묻는다).
 *  - 함수로 한 번 감싼다: 본체 끝이 주석이어도 뒤에 붙는 코드가 먹히지 않게 줄을 바꾼다
 *  - encodeURIComponent: '%'·'#'·줄바꿈이 그대로 남으면 주소가 깨지거나 줄 끝 자동 세미콜론이 사라진다
 *  - 끝의 void 0: 북마크 결과값으로 화면이 바뀌지 않게
 */
(function (root) {
  'use strict';

  function wrapBookmarkletCode(code) {
    var body = String(code).replace(/^﻿/, '');
    return '(function(){\n' + body + '\n})();void 0;';
  }

  function toBookmarkletHref(code) {
    return 'javascript:' + encodeURIComponent(wrapBookmarkletCode(code));
  }

  var api = { wrapBookmarkletCode: wrapBookmarkletCode, toBookmarkletHref: toBookmarkletHref };
  if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
  else root.JandiBackupBookmarklet = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
