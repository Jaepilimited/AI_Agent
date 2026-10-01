// 잔디 백업 도우미 — 순수 함수 시험 (node --test). pytest 는 tests/test_jandi_backup.py 가 부른다.
//
// 출력 형식의 기준은 qa_artifacts/jandi_backup/render.py 다. 실데이터 484개 방을 두 구현으로
// 그려 바이트 단위로 대조했고(2026-10-01), 여기서는 그 모양을 지어낸 방 하나로 붙잡아 둔다.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const SRC = path.join(ROOT, 'app', 'static', 'jandi-backup', 'jandi-backup.js');
const L = require(SRC);
const B = require(path.join(ROOT, 'app', 'static', 'jandi-backup', 'bookmarklet.js'));
const FX = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests', 'fixtures', 'jandi_backup_room.json'), 'utf8'));

const MEMBERS = { 1: { id: 1, name: '가' }, 2: { id: 2, name: '나' }, 3: { id: 3, name: '다' } };
const name = L.makeNamer(MEMBERS);
const ev = (info, extra) => L.describeEvent(Object.assign({ fromEntity: 1, info }, extra || {}), name).text;

test('시스템 기록 16종은 render.py 와 같은 한국어 문장이 된다', () => {
  assert.equal(ev({ eventType: 'invite', invitorId: 2, inviteUsers: [1, 3] }), '[시스템] 나 님이 가, 다 님을 초대했습니다.');
  assert.equal(ev({ eventType: 'invite', inviteUsers: [] }), '[시스템] 가 님이 ? 님을 초대했습니다.');
  assert.equal(ev({ eventType: 'member_invited', eventInfo: { members: [2] } }), '[시스템] 가 님이 나 님을 초대했습니다.');
  assert.equal(ev({ eventType: 'join' }), '[시스템] 가 님이 참여했습니다.');
  assert.equal(ev({ eventType: 'member_joined' }), '[시스템] 가 님이 참여했습니다.');
  assert.equal(ev({ eventType: 'leave' }), '[시스템] 가 님이 나갔습니다.');
  assert.equal(ev({ eventType: 'member_left' }), '[시스템] 가 님이 나갔습니다.');
  assert.equal(ev({ eventType: 'member_kicked_out', eventInfo: { members: [3] } }), '[시스템] 가 님이 다 님을 내보냈습니다.');
  assert.equal(ev({ eventType: 'kickout', eventInfo: { writerId: 2, kickedIds: [3] } }), '[시스템] 나 님이 다 님을 내보냈습니다.');
  assert.equal(ev({ eventType: 'create' }), '[시스템] 가 님이 방을 만들었습니다.');
  assert.equal(ev({ eventType: 'room_created', eventInfo: { members: [1, 2] } }), '[시스템] 가 님이 방을 만들었습니다. 참여자: 가, 나');
  assert.equal(ev({ eventType: 'room_updated' }), '[시스템] 가 님이 방 설정을 변경했습니다.');
  assert.equal(ev({ eventType: 'name_updated' }), '[시스템] 가 님이 방 이름을 변경했습니다.');
  assert.equal(ev({ eventType: 'admin_changed', eventInfo: { members: [2] } }), '[시스템] 가 님이 관리자를 변경했습니다. 관리자: 나');
  assert.equal(ev({ eventType: 'announcement_created', eventInfo: { writerId: 3 } }), '[시스템] 가 님이 공지를 등록했습니다. (작성자: 다)');
  assert.equal(ev({ eventType: 'announcement_created' }), '[시스템] 가 님이 공지를 등록했습니다.');
  assert.equal(ev({ eventType: 'announcement_updated' }), '[시스템] 가 님이 공지를 수정했습니다.');
  assert.equal(ev({ eventType: 'announcement_deleted' }), '[시스템] 가 님이 공지를 삭제했습니다.');
});

test('모르는 시스템 기록은 원시 JSON 이 아니라 "누구 · 종류" 다', () => {
  assert.equal(ev({ eventType: 'topic_folder_moved' }), '[시스템] 가 · topic_folder_moved');
  // info 가 없으면 status 를 종류로 쓴다
  assert.equal(L.describeEvent({ fromEntity: 2, status: 'event' }, name).text, '[시스템] 나 · event');
  // 명단에 없는 사람은 id 그대로, id 도 없으면 '시스템'
  assert.equal(ev({ eventType: 'join' }, { fromEntity: 999 }), '[시스템] 999 님이 참여했습니다.');
  assert.equal(ev({ eventType: 'join' }, { fromEntity: null }), '[시스템] 시스템 님이 참여했습니다.');
});

test('파일·폴더 이름: 윈도우 금지 글자를 바꾸고 끝 점·공백을 걷는다', () => {
  assert.equal(L.sanitizeName('a<b>c:d"e/f\\g|h?i*j\x01k. . '), 'a_b_c_d_e_f_g_h_i_j_k');
  assert.equal(L.roomFolderName({ id: 42, kind: 'private_topic', name: '[운영] 본부: 채팅방 / 2026년 하반기 프로젝트 모음' }),
    '비공개토픽_42_[운영] 본부_ 채팅방 _ 2026년 하반기', '이름은 24글자까지');
  assert.equal(L.roomFolderName({ id: 7, kind: 'chat', name: '...' }), '1대1_7_room');
  assert.equal(L.roomFolderName({ id: 8, kind: 'groupChat', name: '' }), '그룹DM_8_8');
  assert.equal(L.roomFolderName({ id: 9, kind: 'topic', name: '😀'.repeat(30) }), '토픽_9_' + '😀'.repeat(24), '이모지를 반으로 자르지 않는다');
});

test('첨부 파일 이름은 짧게 자르되 확장자와 파일 id 를 지킨다', () => {
  assert.equal(L.attachmentFileName(123, '보고서.pdf'), '123_보고서.pdf');
  assert.equal(L.attachmentFileName(123, 'a:b?.png'), '123_a_b_.png');
  const long = L.attachmentFileName(4488131132, '가'.repeat(200) + '.xlsx');
  assert.equal(Array.from(long).length, 80);
  assert.ok(long.startsWith('4488131132_가'));
  assert.ok(long.endsWith('.xlsx'));
  const noExt = L.attachmentFileName(5, 'x'.repeat(300));
  assert.equal(noExt.length, 80);
  // 이름이 없으면 id 로
  assert.equal(L.attachmentFileName('sticker_155_2', 'sticker_155_2.png'), 'sticker_155_2_sticker_155_2.png');
  assert.equal(L.attachmentFileName(9, ''), '9_9');
});

test('CSV: 수식으로 읽힐 칸은 앞에 따옴표, 쉼표·줄바꿈·따옴표는 감싼다', () => {
  assert.equal(L.csvCell('=SUM(A1)'), "'=SUM(A1)");
  assert.equal(L.csvCell('+1'), "'+1");
  assert.equal(L.csvCell('-1'), "'-1");
  assert.equal(L.csvCell('@x'), "'@x");
  assert.equal(L.csvCell('\tx'), "'\tx");
  assert.equal(L.csvCell('a,b'), '"a,b"');
  assert.equal(L.csvCell('줄\n바꿈'), '"줄\n바꿈"');
  assert.equal(L.csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(L.csvCell(null), '');
  assert.equal(L.csvRow(['a', 1, '']), 'a,1,\r\n');
});

test('HTML·경로 이스케이프는 파이썬 html.escape · quote 와 같다', () => {
  assert.equal(L.escapeHtml(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#x27;&amp;&#x27;&lt;/a&gt;');
  assert.equal(L.quotePath("attachments/1_회의 사진(최종)!.png"),
    'attachments/1_%ED%9A%8C%EC%9D%98%20%EC%82%AC%EC%A7%84%28%EC%B5%9C%EC%A2%85%29%21.png');
  assert.equal(L.quotePath('a#b%c'), 'a%23b%25c');
});

test('시각은 Asia/Seoul 로 적는다 (밀리초·초·ISO)', () => {
  assert.equal(L.localTime(1782864000000), '2026-07-01 09:00:00');
  assert.equal(L.localTime(1782864000), '2026-07-01 09:00:00');
  assert.equal(L.localTime('2026-07-01T00:00:00.000Z'), '2026-07-01 09:00:00');
  assert.equal(L.localTime(null), '');
  assert.equal(L.localTime('엉뚱한 값'), '엉뚱한 값');
  assert.equal(L.dayKst(1782864000000), '2026-07-01');
  assert.equal(L.dayKst(null), '');
});

test('페이지 넘김: 접근 제한 · 처음 기록 · 제자리 · 계속', () => {
  const recs = (...ids) => ids.map((id) => ({ id }));
  assert.deepEqual(L.pageParams(null), { count: 100 });
  assert.deepEqual(L.pageParams(500), { count: 100, type: 'old', linkId: 500 });
  assert.equal(L.decidePage({ records: recs(10, 11), isLimited: true, firstLinkId: 1 }, null).stopReason, 'access_limit');
  assert.equal(L.decidePage({ records: [], firstLinkId: 1 }, 50).stopReason, 'first_link_reached');
  assert.equal(L.decidePage({ records: recs(5, 9), firstLinkId: 5 }, 20).stopReason, 'first_link_reached');
  assert.equal(L.decidePage({ records: recs(4, 9), firstLinkId: 5 }, 20).stopReason, 'first_link_reached');
  assert.equal(L.decidePage({ records: recs(30, 40), firstLinkId: 1 }, 30).stopReason, 'no_progress');
  const go = L.decidePage({ records: recs(12, 15, 20), firstLinkId: 1 }, 30);
  assert.equal(go.done, false);
  assert.equal(go.min, 12);
  // 숫자가 문자열로 와도 같은 판정
  assert.equal(L.decidePage({ records: recs('5'), firstLinkId: '5' }, null).stopReason, 'first_link_reached');
});

test('메시지가 없는 방은 lastLinkId 칸이 있고 비었을 때만이다', () => {
  assert.equal(L.isEmptyRoom({ id: 1, lastLinkId: 0 }), true);
  assert.equal(L.isEmptyRoom({ id: 1, lastLinkId: null }), true);
  assert.equal(L.isEmptyRoom({ id: 1, lastLinkId: 123 }), false);
  assert.equal(L.isEmptyRoom({ id: 1 }), false, '칸이 없으면 모른다 — 비었다고 단정하지 않는다');
});

test('받을 파일: 메시지·댓글 원본 첨부 + 스티커 + 옛 방식 파일 메시지, 방 안에서 한 번씩', () => {
  const jobs = L.collectFileJobs(FX.records);
  const ids = jobs.map((j) => String(j.id));
  assert.deepEqual(ids, ['7000001', '7000002', '7000003', '7000004', '5000010', 'sticker_155_2', 'sticker_160_11']);
  const legacy = jobs.find((j) => j.id === 5000010);
  assert.equal(legacy.source, 'file_message');
  assert.equal(legacy.size, 44);
  const sticker = jobs.find((j) => j.id === 'sticker_155_2');
  assert.equal(sticker.kind, 'sticker');
  assert.equal(sticker.url, 'https://jandi-box.com/files-sticker/155/2');
  assert.equal(jobs.find((j) => j.id === 7000001).content.name, '회의 사진.png', 'getImage 에 넘길 content 를 지닌다');
});

test('방 뷰어: render.py 와 같은 모양 (fixture 방)', () => {
  const out = L.renderRoom({ roomName: FX.roomName, roomId: FX.roomId, members: FX.members, records: FX.records }, FX.manifest);
  const html = out.html;
  assert.equal(out.count, 11);
  assert.equal((html.match(/<article /g) || []).length, 11);
  assert.ok(html.includes(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:;`));
  assert.ok(html.includes('<title>[테스트] 기획 &lt;팀&gt; 방 — 잔디 백업</title>'));
  assert.ok(html.includes('· 11건</p>'));
  assert.ok(html.includes('id="search"'));
  // 본문은 이스케이프된다
  assert.ok(html.includes('안녕하세요 &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;따옴표&quot;'));
  assert.ok(!html.includes('<script>alert(1)'));
  // 시스템 기록은 문장으로, 보낸 사람은 '시스템'
  assert.ok(html.includes('<strong>시스템</strong><time>2026-07-01 09:00:00</time></div><div class="text">[시스템] Kim Minsu_김민수 님이 방을 만들었습니다. 참여자: Kim Minsu_김민수, Lee O&#x27;Hara_이하라</div>'));
  assert.ok(!html.includes('eventType'));
  // 답장 링크와 기록 별칭
  assert.ok(html.includes('<article id="m-5000003"><span id="l-6000004"></span>'));
  assert.ok(html.includes('<a class="reply" href="#m-5000002">답장 원문 보기</a>'));
  // 받은 그림은 링크 + 미리보기, 이름 끝 점이 둘인 파일도 링크된다, 못 받은 파일은 표시만
  assert.ok(html.includes('<a class="file" href="attachments/7000001_%ED%9A%8C%EC%9D%98%20%EC%82%AC%EC%A7%84.png">회의 사진.png</a><img loading="lazy" src="attachments/7000001_%ED%9A%8C%EC%9D%98%20%EC%82%AC%EC%A7%84.png" alt="회의 사진.png">'));
  assert.ok(html.includes('<a class="file" href="attachments/7000002_'));
  assert.ok(html.includes('<span class="missing">지워진 파일.pdf — 미저장</span>'));
  // 옛 방식 파일 메시지는 JSON 원문이 아니라 파일 이름 + 링크
  assert.ok(html.includes('<div class="text">[파일] 옛날 보고서.docx</div><a class="file" href="attachments/5000010_'));
  assert.ok(!html.includes('fileUrl'));
  // 스티커·투표
  assert.ok(html.includes('[스티커 155/2]</div><a class="file" href="attachments/sticker_155_2_sticker_155_2.png">sticker_155_2.png</a><img'));
  assert.ok(html.includes('[투표] 점심 메뉴\n하나만 고르세요\n국수 — 2표\n김밥 — 1표\n총 참여: 3 · 상태: finished'));
});

test('방 CSV·TXT: BOM, 머리글, 수식 방지, 메모장 줄바꿈', () => {
  const out = L.renderRoom({ roomName: FX.roomName, roomId: FX.roomId, members: FX.members, records: FX.records }, FX.manifest);
  assert.ok(out.csv.startsWith('﻿message_id,link_id,time_Asia_Seoul,sender,type,text,reply_to,attachments\r\n'));
  assert.ok(out.csv.includes('5000002,6000003,2026-07-01 09:02:00,Lee O\'Hara_이하라,text,"\'=SUM(A1:A9) 첨부 세 개, 줄\n바꿈",,"회의 사진.png [attachments/7000001_회의 사진.png]\n매출 현황 - 2026..xlsx [attachments/7000002_매출 현황 - 2026..xlsx]\n지워진 파일.pdf [미저장]"\r\n'));
  assert.ok(out.csv.includes('5000003,6000004,2026-07-01 09:03:00,Kim Minsu_김민수,comment,확인했습니다,5000002,\r\n'));
  assert.ok(out.txt.startsWith('﻿[테스트] 기획 <팀> 방\r\n시간대: Asia/Seoul (UTC+09:00)\r\n\r\n[2026-07-01 09:00:00] 시스템\r\n'));
  assert.ok(out.txt.includes('[2026-07-01 09:03:00] Kim Minsu_김민수\r\n확인했습니다\r\n답장 대상: 5000002'));
  assert.ok(!/[^\r]\n/.test(out.txt), 'TXT 의 줄바꿈은 모두 CRLF');
});

test('기록은 시각 → id 순으로 정렬된다 (id→기록 객체도 받는다)', () => {
  const shuffled = {};
  FX.records.slice().reverse().forEach((r) => { shuffled[String(r.id)] = r; });
  const a = L.renderRoom({ roomName: 'x', members: FX.members, records: shuffled }, FX.manifest);
  const b = L.renderRoom({ roomName: 'x', members: FX.members, records: FX.records }, FX.manifest);
  assert.equal(a.html, b.html);
});

test('전체 목록: 종류 순 · 최근 순, 폴더 링크는 퍼센트 인코딩, 합계', () => {
  const rows = [
    { id: '1', kind: 'chat', name: '대화 A', folder: '1대1_1_대화 A', records: 10, files: 2, saved: 1, bytes: 2e6, first: 1782864000000, last: 1782864000000, stop: 'first_link_reached' },
    { id: '2', kind: 'topic', name: '공지 <방>', folder: '토픽_2_공지 _방_', records: 1234, files: 3, saved: 3, bytes: 1.5e9, first: 1700000000000, last: 1782000000000, stop: 'access_limit' },
    { id: '3', kind: 'topic', name: '최근 토픽', folder: '토픽_3_최근 토픽', records: 0, files: 0, saved: 0, bytes: 0, first: null, last: 1782900000000, stop: 'no_messages' }
  ];
  const html = L.buildTopIndex(rows, { teamName: 'TEAM', teamId: 1, date: '2026-10-01' });
  assert.ok(html.indexOf('최근 토픽') < html.indexOf('공지 &lt;방&gt;'));
  assert.ok(html.indexOf('공지 &lt;방&gt;') < html.indexOf('대화 A'));
  assert.ok(html.includes('href="rooms/%ED%86%A0%ED%94%BD_2_%EA%B3%B5%EC%A7%80%20_%EB%B0%A9_/index.html"'));
  assert.ok(html.includes('방 3개 · 기록 1,244건 · 첨부 4/5개 (1.5GB)'));
  assert.ok(html.includes('<td>1,234</td><td>3/3</td><td>1,500MB</td><td>2023-11-15 ~ 2026-06-21</td>'));
  assert.ok(html.includes('처음 기록까지 가지 못한 방 1개'));
  const readme = L.buildReadme({ teamName: 'TEAM', attachments: true });
  for (const phrase of ['참여하지 않은 토픽', '삭제된 메시지', '편집 이력', '복원할 수 없습니다', '셀라 서버는 대화 내용을 받지 않았습니다']) {
    assert.ok(readme.includes(phrase), phrase);
  }
});

test('팀 이름은 창 제목 끝, 없으면 하위 도메인', () => {
  assert.equal(L.teamNameFrom('기획 방 | CRAVERCORP', 'craver.jandi.com'), 'CRAVERCORP');
  assert.equal(L.teamNameFrom('JANDI', 'craver.jandi.com'), 'craver');
  assert.equal(L.backupFolderName('A/B', '20261001'), '잔디백업_A_B_20261001');
});

// ── 북마크 ─────────────────────────────────────────────────────────────────

const CODE = fs.readFileSync(SRC, 'utf8');

test('북마크 주소에는 본체 전체가 들어가고, 되돌리면 그대로다', () => {
  const href = B.toBookmarkletHref(CODE);
  assert.ok(href.startsWith('javascript:'));
  const body = href.slice('javascript:'.length);
  assert.ok(!/[\s#%]/.test(body.replace(/%[0-9A-F]{2}/g, '')), '날 것의 공백·#·% 가 남지 않는다');
  assert.equal(decodeURIComponent(body), B.wrapBookmarkletCode(CODE));
  assert.ok(decodeURIComponent(body).includes(CODE.trim().slice(-40)));
  // 문법이 살아 있다 (컴파일만)
  new Function(decodeURIComponent(body)); // eslint-disable-line no-new-func
});

function runBookmarklet(sandbox) {
  const code = decodeURIComponent(B.toBookmarkletHref(CODE).slice('javascript:'.length));
  vm.runInNewContext(code, sandbox, { timeout: 2000 });
  return sandbox;
}

test('브라우저 밖(window 없음)에서 실행해도 조용히 끝난다', () => {
  assert.doesNotThrow(() => runBookmarklet({}));
});

test('잔디가 아닌 곳에서 누르면 안내만 하고 아무것도 하지 않는다', () => {
  const alerts = [];
  const sandbox = { alert: (m) => alerts.push(m), location: { hostname: 'cella.example', pathname: '/jandi-backup' } };
  sandbox.window = sandbox;
  sandbox.document = { title: '셀라' };
  runBookmarklet(sandbox);
  assert.equal(alerts.length, 1);
  assert.ok(alerts[0].includes('jandi.com/app'));
  assert.equal(sandbox.__jandiBackup, undefined);
});

test('두 번 누르면 새로 만들지 않고 떠 있는 패널을 다시 연다', () => {
  let shown = 0;
  const sandbox = { location: { hostname: 'team.jandi.com', pathname: '/app/' }, __jandiBackup: { show: () => { shown++; } } };
  sandbox.window = sandbox;
  sandbox.document = { title: 'x' };
  runBookmarklet(sandbox);
  assert.equal(shown, 1);
});
