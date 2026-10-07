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

// ── 기준일 (잔디 가입일) ───────────────────────────────────────────────────

const CUT = Date.parse('2021-05-02T15:00:00.000Z');   // 2021-05-03 00:00 KST
const at = (iso) => Date.parse(iso);

test('기준일: 첫 기록의 KST 날짜 00:00 — 그날 늦은 시각·UTC 로는 전날이어도 KST 날짜로', () => {
  assert.deepEqual(L.cutoffFrom([at('2021-05-03T05:12:00.000Z')]), { ms: CUT, date: '2021-05-03' });
  // UTC 로는 5/2 23:30 이지만 KST 로는 5/3 08:30
  assert.deepEqual(L.cutoffFrom([at('2021-05-02T23:30:00.000Z')]), { ms: CUT, date: '2021-05-03' });
  // KST 5/3 23:59 도 같은 날
  assert.equal(L.cutoffFrom([at('2021-05-03T14:59:00.000Z')]).date, '2021-05-03');
  // 후보가 여럿이면 가장 이른 것, 못 찾은 후보(null)는 무시
  assert.equal(L.cutoffFrom([null, at('2022-01-01T00:00:00.000Z'), at('2021-05-03T00:00:00.000Z')]).date, '2021-05-03');
  assert.equal(L.cutoffFrom([]), null);
  assert.equal(L.cutoffFrom([null, null]), null);
});

test('기록 시각은 localTime 과 같은 규칙 (time 우선 · 밀리초/초 · ISO), 모르면 null', () => {
  assert.equal(L.recordMs({ time: 1782864000000 }), 1782864000000);
  assert.equal(L.recordMs({ time: 1782864000 }), 1782864000000);
  assert.equal(L.recordMs({ createdAt: '2026-07-01T00:00:00.000Z' }), 1782864000000);
  assert.equal(L.recordMs({ message: { createdAt: '2026-07-01T00:00:00.000Z' } }), 1782864000000);
  assert.equal(L.recordMs({ id: 1 }), null);
  assert.equal(L.recordMs({ time: '엉뚱한 값' }), null);
  assert.equal(L.earliestMs([{ time: 30 }, { id: 2 }, { time: 10 }]), 10000);
  assert.equal(L.earliestMs([]), null);
});

test('기준일 거르기: 당일 00:00 은 포함, 하루 전 23:59 는 제외, 시각 모르는 기록도 제외', () => {
  const recs = [
    { id: 1, time: CUT - 60000 },        // 5/2 23:59 KST
    { id: 2, time: CUT },                // 5/3 00:00 KST
    { id: 3, time: CUT + 3600000 },
    { id: 4 }                            // 시각 없음
  ];
  assert.deepEqual(L.cutoffRecords(recs, CUT).map((r) => r.id), [2, 3]);
  // 기준일이 없으면 거르지 않는다 (순수 함수 기본값 — 패널은 기준일 없이 시작하지 않는다)
  assert.equal(L.cutoffRecords(recs, undefined).length, 4);
});

test('기준일 거르기: 댓글은 남기고 기준일 이전·시각 모르는 원본(feedback)만 떼어 낸다', () => {
  const att = (id) => ({ id, content: { name: id + '.txt', size: 1 } });
  const recs = [
    { id: 10, time: CUT + 1000, message: { id: 1, attachments: [] }, feedback: { id: 90, createdAt: new Date(CUT - 1000).toISOString(), attachments: [att(901)] } },
    { id: 11, time: CUT + 2000, message: { id: 2, attachments: [] }, feedback: { id: 91, createdAt: new Date(CUT + 1).toISOString(), attachments: [att(911)] } },
    { id: 12, time: CUT + 3000, message: { id: 3, attachments: [] }, feedback: { id: 92, attachments: [att(921)] } }
  ];
  const kept = L.cutoffRecords(recs, CUT);
  assert.deepEqual(kept.map((r) => r.id), [10, 11, 12], '댓글 자체는 기준일 이후라 남는다');
  assert.equal('feedback' in kept[0], false);
  assert.equal(kept[1].feedback.id, 91);
  assert.equal('feedback' in kept[2], false, '원본 시각을 모르면 받지 않는다');
  assert.ok('feedback' in recs[0], '입력 기록은 바꾸지 않는다');
  // 첨부 수집은 거른 기록만 본다 — 기준일 이전 원본의 첨부는 받지 않는다
  assert.deepEqual(L.collectFileJobs(kept).map((j) => j.id), [911]);
});

test('기준일 거르기: 한 장에 섞인 기록 — 이전 기록과 그 첨부는 저장·첨부 수집·미리 확인 어디에도 안 들어간다', () => {
  const old = { id: 1, time: CUT - 1, message: { id: 1, contentType: 'text', attachments: [{ id: 501, content: { name: 'old.pdf', size: 100 } }], content: { body: '입사 전' } } };
  const legacy = { id: 2, time: CUT - 86400000, message: { id: 502, contentType: 'file', attachments: [], content: { name: 'old.docx', size: 200 } } };
  const now = { id: 3, time: CUT, message: { id: 3, contentType: 'text', attachments: [{ id: 503, content: { name: 'new.pdf', size: 7 } }], content: { body: '입사 날' } } };
  const kept = L.cutoffRecords([old, legacy, now], CUT);
  assert.deepEqual(kept.map((r) => r.id), [3]);
  assert.deepEqual(L.collectFileJobs(kept).map((j) => j.id), [503]);
  const out = L.renderRoom({ roomName: 'x', roomId: 1, members: {}, records: kept }, []);
  assert.equal(out.count, 1);
  assert.ok(!out.csv.includes('입사 전') && out.csv.includes('입사 날'));
  const tally = L.newTally();
  L.tallyPage(tally, kept, new Set(), new Set());
  assert.deepEqual([tally.records, tally.files, tally.bytes, tally.legacyFiles], [1, 1, 7, 0]);
});

test('페이지 넘김: 기준일 이전 기록이 보이는 장에서 cutoff 로 멈춘다', () => {
  const page = (...times) => ({ records: times.map((t, i) => ({ id: 100 + i, time: t })), firstLinkId: 1 });
  assert.deepEqual(L.decidePage(page(CUT + 5, CUT - 1), 200, CUT), { done: true, stopReason: 'cutoff', min: 100 });
  // 경계: 당일 00:00 만 있으면 계속 넘긴다
  assert.equal(L.decidePage(page(CUT, CUT + 5), 200, CUT).done, false);
  // 열람 제한보다 먼저 — 기준일까지 다 봤으면 제한은 상관없다
  assert.equal(L.decidePage(Object.assign(page(CUT - 1), { isLimited: true }), 200, CUT).stopReason, 'cutoff');
  // 시각 모르는 기록으로는 멈추지 않는다
  assert.equal(L.decidePage({ records: [{ id: 150 }], firstLinkId: 1 }, 200, CUT).done, false);
  // 기준일을 안 주면 예전과 같다
  assert.equal(L.decidePage(page(CUT - 1), 200).done, false);
});

test('기준일 방 찾기: JANDI 봇(대소문자·공백 무시)이 멤버인 1:1 방', () => {
  const rooms = [
    { id: 1, kind: 'chat', memberIds: [7, 1001], rawName: '' },
    { id: 2, kind: 'chat', memberIds: [7, 2001], rawName: '' },
    { id: 3, kind: 'groupChat', memberIds: [7, 2001, 8] },
    { id: 4, kind: 'chat', memberIds: [7, '2002'], rawName: '' }
  ];
  const bots = [{ id: 2001, name: ' jandi ' }, { id: 2002, name: 'JANDI' }, { id: 2003, name: '잔디봇' }];
  assert.deepEqual(L.findWelcomeRooms(rooms, bots).map((r) => r.id), [2, 4], '그룹 대화는 아니다 · 여럿이면 모두 후보');
  // 봇 목록에 없으면 방 이름이 JANDI 인 1:1 방
  assert.deepEqual(L.findWelcomeRooms([{ id: 5, kind: 'chat', memberIds: [7, 9], rawName: 'JANDI' }], []).map((r) => r.id), [5]);
  // 실패: 봇 없음 · 대화 없음
  assert.deepEqual(L.findWelcomeRooms(rooms, [{ id: 2003, name: '잔디봇' }]), []);
  assert.deepEqual(L.findWelcomeRooms([{ id: 3, kind: 'groupChat', memberIds: [2001] }], bots), []);
  assert.deepEqual(L.findWelcomeRooms([], bots), []);
  // 실패: 대화는 있지만 기록이 없다 → 첫 기록 시각 null → 기준일 없음
  assert.equal(L.cutoffFrom([L.earliestMs([])]), null);
});

test('README·전체 목록에 기준일을 적고, 기준일에서 멈춘 방은 끝난 방이다', () => {
  const readme = L.buildReadme({ teamName: 't', startedAt: 'x', attachments: true, cutoff: '2021-05-03' });
  assert.ok(readme.includes('기준일: 2021-05-03 (잔디 가입일)'));
  assert.ok(readme.includes('잔디에 들어오기 전(가입일 이전) 대화와 첨부'));
  const index = L.buildTopIndex([{ id: '1', kind: 'topic', name: 'a', folder: 'f', records: 1, files: 0, saved: 0, bytes: 0, stop: 'cutoff' }],
    { teamName: 't', date: 'd', cutoff: '2021-05-03' });
  assert.ok(index.includes('기준일: 2021-05-03 (잔디 가입일)'));
  assert.ok(!index.includes('처음 기록까지 가지 못한 방'));
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

// ── 용량 미리 확인 · 남은 시간 ─────────────────────────────────────────────

test('미리 확인: 첨부·댓글 원본 첨부·옛 방식 파일 메시지는 용량으로, 스티커는 개수만, 쪽이 겹쳐도 한 번', () => {
  const t = L.newTally();
  const seenR = new Set();
  const seenF = new Set();
  // fixture 방을 두 쪽으로 나누고, 둘째 쪽에 첫째 쪽 기록 하나를 겹쳐 넣는다
  assert.equal(L.tallyPage(t, FX.records.slice(5), seenR, seenF), 6);
  assert.equal(L.tallyPage(t, FX.records.slice(0, 6), seenR, seenF), 5, '겹친 기록은 새로 세지 않는다');
  assert.equal(t.records, 11);
  // 7000001(11) · 7000002(22) · 7000003(33) · 7000004(7, 댓글 원본) · 5000010(44, 옛 방식)
  assert.equal(t.files, 5);
  assert.equal(t.bytes, 117);
  assert.equal(t.legacyFiles, 1);
  assert.equal(t.legacyBytes, 44);
  assert.equal(t.stickers, 2);
  assert.equal(t.unknown, 0);
  // 실제 백업이 받을 목록과 같은 개수
  assert.equal(t.files + t.stickers + t.unknown, L.collectFileJobs(FX.records).length);
});

test('미리 확인: 크기가 없는 파일(삭제됨)은 따로 센다 — 옛 방식 파일도', () => {
  const t = L.newTally();
  L.tallyPage(t, [
    { id: 1, message: { id: 11, contentType: 'text', content: { body: 'x' }, attachments: [{ id: 501, content: { name: 'a.pdf' } }, { id: 502, content: { name: 'b.pdf', size: 1000 } }] } },
    { id: 2, message: { id: 12, contentType: 'file', content: { name: '옛.hwp' } } },
    { id: 3, message: { id: 13, contentType: 'file', content: { name: '옛2.hwp', size: 3e9 } } },
    { id: 4 }
  ], new Set(), new Set());
  assert.equal(t.records, 4);
  assert.equal(t.files, 2);
  assert.equal(t.bytes, 3e9 + 1000);
  assert.equal(t.unknown, 2);
  assert.equal(t.legacyFiles, 1);
  assert.equal(t.legacyBytes, 3e9);
});

test('미리 확인 합계: 종류별(비공개 토픽은 토픽) · 전체, 확인 못 한 방은 빠진다', () => {
  const rooms = [
    { id: 1, kind: 'topic' }, { id: 2, kind: 'private_topic' }, { id: 3, kind: 'chat' }, { id: 4, kind: 'groupChat' }, { id: 5, kind: 'chat' }
  ];
  const mk = (records, files, bytes) => Object.assign(L.newTally(), { records, files, bytes });
  const per = { 1: mk(100, 3, 5e9), 2: mk(50, 1, 1e9), 3: mk(10, 0, 0), 4: mk(7, 2, 2e6) };
  const s = L.summarizeScan(rooms, per);
  assert.deepEqual([s.topic.rooms, s.topic.records, s.topic.files, s.topic.bytes], [2, 150, 4, 6e9]);
  assert.deepEqual([s.chat.rooms, s.chat.records], [1, 10], '확인 못 한 방(5)은 세지 않는다');
  assert.deepEqual([s.total.rooms, s.total.records, s.total.files, s.total.bytes], [4, 167, 6, 6e9 + 2e6]);
});

// ── 방 고르기 (필요 없는 방 빼기) ───────────────────────────────────────────

test('미리 확인 합계: 방 고르기에서 뺀 방은 종류별·전체 합계에서 빠진다', () => {
  const rooms = [{ id: 1, kind: 'topic' }, { id: 2, kind: 'private_topic' }, { id: 3, kind: 'chat' }];
  const mk = (records, files, bytes) => Object.assign(L.newTally(), { records, files, bytes });
  const per = { 1: mk(100, 3, 5e9), 2: mk(50, 1, 1e9), 3: mk(10, 2, 2e6) };
  const excluded = { 1: true };
  const s = L.summarizeScan(rooms, per, (r) => !excluded[String(r.id)]);
  assert.deepEqual([s.topic.rooms, s.topic.records, s.topic.bytes], [1, 50, 1e9]);
  assert.deepEqual([s.total.rooms, s.total.records, s.total.files, s.total.bytes], [2, 60, 3, 1e9 + 2e6]);
  // 고르는 함수를 안 주면 예전처럼 모두
  assert.equal(L.summarizeScan(rooms, per).total.rooms, 3);
});

test('제외 id 를 지금 방 목록과 맞춘다 — 없어진 id·이상한 값은 버리고 숫자/문자 id 는 같게', () => {
  const rooms = [{ id: 10 }, { id: 20 }, { id: '30' }];
  assert.deepEqual(L.reconcileExcluded(['10', 30, '999', null, 20], rooms), { 10: true, 20: true, 30: true });
  assert.deepEqual(L.reconcileExcluded(['999'], rooms), {});
  assert.deepEqual(L.reconcileExcluded('깨진 값', rooms), {});
  assert.deepEqual(L.reconcileExcluded(null, rooms), {});
});

test('다시 확인 판정: 고른 방 중 미리 확인 값이 없는 방 (확인에 실패한 방은 제외)', () => {
  const per = { 1: L.newTally(), 2: L.newTally() };
  assert.deepEqual(L.unscannedRooms([{ id: 1 }, { id: 2 }], per, {}), [], '빼기만 하면 다시 확인할 필요 없다');
  assert.deepEqual(L.unscannedRooms([{ id: 1 }], per, {}), []);
  assert.deepEqual(L.unscannedRooms([{ id: 1 }, { id: 3 }], per, {}).map((r) => r.id), [3], '확인 때 빠져 있던 방을 다시 넣었다');
  assert.deepEqual(L.unscannedRooms([{ id: 1 }, { id: 3 }], per, { 3: true }), [], '확인에 실패한 방은 따로 알린다');
});

test('방 고르기 순서: 미리 확인 전에는 이름순, 후에는 용량 큰 순(같으면 이름순, 값 없는 방은 끝)', () => {
  const rooms = [{ id: 1, name: '다' }, { id: 2, name: '가' }, { id: 3, name: '나' }, { id: 4, name: '라' }];
  assert.deepEqual(L.sortPickRooms(rooms, null).map((r) => r.name), ['가', '나', '다', '라']);
  const t = (bytes) => Object.assign(L.newTally(), { bytes });
  assert.deepEqual(L.sortPickRooms(rooms, { 1: t(5), 2: t(0), 3: t(9) }).map((r) => r.name), ['나', '다', '가', '라']);
  assert.deepEqual(rooms.map((r) => r.id), [1, 2, 3, 4], '원래 배열은 그대로');
});

test('README·전체 목록: 제외한 방 수와 이름(최대 50개 + 외 N개), 없으면 아무 말 없음', () => {
  const names = Array.from({ length: 53 }, (_, i) => '방' + (i + 1));
  names[0] = '공지 <알림>';
  const meta = { teamName: 't', date: 'd', cutoff: '2021-05-03', attachments: true, excluded: names };
  const readme = L.buildReadme(meta);
  assert.ok(readme.includes('제외한 방 53개'));
  assert.ok(readme.includes('    공지 <알림>\r\n') && readme.includes('    방50\r\n') && !readme.includes('방51'));
  assert.ok(readme.includes('    외 3개'));
  const index = L.buildTopIndex([], meta);
  assert.ok(index.includes('제외한 방 53개'));
  assert.ok(index.includes('공지 &lt;알림&gt;, 방2') && index.includes('방50 외 3개') && !index.includes('방51'));
  for (const m of [{ teamName: 't' }, { teamName: 't', excluded: [] }]) {
    assert.ok(!L.buildReadme(m).includes('제외한 방'));
    assert.ok(!L.buildTopIndex([], m).includes('제외한 방'));
  }
});

test('용량 표기: GB 한 자리 · MB 정수', () => {
  assert.equal(L.fmtBytes(123.4e9), '123.4GB');
  assert.equal(L.fmtBytes(66e9), '66.0GB');
  assert.equal(L.fmtBytes(1234e9), '1,234.0GB');
  assert.equal(L.fmtBytes(512e6), '512MB');
  assert.equal(L.fmtBytes(117), '117B');
  assert.equal(L.fmtBytes(0), '0B');
});

test('걸릴 시간: 대화 = 확인에 걸린 시간 ~ 쓰기 몫, 첨부 = 용량 ÷ 20~60MB/s', () => {
  // 실측(2026-10-01): 123GB, 확인 7분
  const e = L.estimateTimes(123e9, 7 * 60000);
  assert.equal(e.speedLow, 20e6);
  assert.equal(e.speedHigh, 60e6);
  assert.equal(e.messages[0], 7 * 60000);
  assert.ok(Math.abs(e.messages[1] - 7 * 60000 * 1.3) < 1);
  assert.equal(Math.round(e.download[0]), 2050000);   // 123e9 / 60e6 초
  assert.equal(Math.round(e.download[1]), 6150000);   // 123e9 / 20e6 초
  assert.equal(L.fmtRange(e.messages[0], e.messages[1]), '약 7분 ~ 9분');
  assert.equal(L.fmtRange(e.withFiles[0], e.withFiles[1]), '약 41분 ~ 1시간 52분');
  // 첨부가 없으면 대화 시간 그대로
  const z = L.estimateTimes(0, 20000);
  assert.deepEqual(z.withFiles, z.messages);
  assert.equal(L.fmtRange(z.withFiles[0], z.withFiles[1]), '약 1분 미만');
  // 속도를 바꿔 넣을 수 있다
  assert.equal(L.estimateTimes(90e9, 0, { low: 50e6, high: 90e6 }).download[0], 1000000);
});

test('대략 시간 표기는 분 단위', () => {
  assert.equal(L.fmtApprox(0), '1분 미만');
  assert.equal(L.fmtApprox(29000), '1분 미만');
  assert.equal(L.fmtApprox(31000), '1분');
  assert.equal(L.fmtApprox(59 * 60000), '59분');
  assert.equal(L.fmtApprox(60 * 60000), '1시간');
  assert.equal(L.fmtApprox(125 * 60000), '2시간 5분');
  assert.equal(L.fmtRange(60000, 60000), '약 1분');
});

test('속도: 최근 창의 표본만 남기고(기준점 하나는 창 밖), 짧으면 모른다', () => {
  const s = [];
  for (let t = 0; t <= 30000; t += 1000) L.pushSample(s, t, t * 50e3, 15000);   // 50MB/s 로 꾸준히
  assert.ok(s[0].t <= 30000 - 15000 && s[1].t > 30000 - 15000, '창 밖 표본은 하나만');
  assert.ok(s.length <= 17);
  assert.equal(L.rollingRate(s, 5000), 50e6);
  assert.equal(L.rollingRate([{ t: 0, b: 0 }, { t: 2000, b: 1e8 }], 5000), null, '2초로는 재지 않는다');
  assert.equal(L.rollingRate([{ t: 0, b: 0 }], 5000), null);
  // 갑자기 멈추면 창이 지나며 속도가 0 으로 내려간다
  for (let t = 31000; t <= 50000; t += 1000) L.pushSample(s, t, 30000 * 50e3, 15000);
  assert.equal(L.rollingRate(s, 5000), 0);
});

test('남은 시간: 용량을 알고 속도가 붙으면 용량 기준, 아니면 방 수 기준', () => {
  const base = { useBytes: true, expectedComplete: true, expected: 100e9, bytes: 40e9, rate: 50e6, roomsFrac: 0.5, elapsedMs: 600000 };
  assert.deepEqual(L.rawEta(base), { ms: 1200000, basis: 'bytes' });
  assert.equal(L.rawEta(Object.assign({}, base, { expectedComplete: false })).basis, 'rooms', '예상 용량이 다 안 모였으면');
  assert.equal(L.rawEta(Object.assign({}, base, { rate: null })).basis, 'rooms', '속도를 아직 모르면');
  assert.equal(L.rawEta(Object.assign({}, base, { useBytes: false })).ms, 600000, '첨부 제외면 방 수 기준');
  assert.equal(L.rawEta(Object.assign({}, base, { rate: null, roomsFrac: 0.01 })).ms, null, '너무 이르면 말하지 않는다');
  assert.equal(L.rawEta(Object.assign({}, base, { bytes: 120e9 })).ms, 0, '예상을 넘겨도 음수가 아니다');
});

test('남은 시간 다듬기: 흐른 시간만큼 줄이고 새 값 쪽으로 천천히, 근거가 바뀌면 새로', () => {
  assert.equal(L.smoothEta(null, { ms: null, basis: null }, 700, 20000), null);
  const first = L.smoothEta(null, { ms: 600000, basis: 'bytes' }, 700, 20000);
  assert.equal(first.ms, 600000);
  // 예측대로면(10분 → 0.7초 뒤 599.3초) 그대로 따라간다
  const same = L.smoothEta(first, { ms: 599300, basis: 'bytes' }, 700, 20000);
  assert.ok(Math.abs(same.ms - 599300) < 1e-6);
  // 속도가 순간 반토막(원값 두 배) 나도 한 번에 뛰지 않는다
  const spike = L.smoothEta(first, { ms: 1200000, basis: 'bytes' }, 700, 20000);
  assert.ok(spike.ms > 599300 && spike.ms < 625000, String(spike.ms));
  // 원값이 정말로 바뀌었으면(15분 쪽으로 꾸준히 줄어듦) 시상수의 몇 배 뒤에는 따라가 있다
  let cur = first;
  let raw = 900000;
  for (let i = 0; i < 300; i++) { raw -= 700; cur = L.smoothEta(cur, { ms: raw, basis: 'bytes' }, 700, 20000); }
  assert.ok(Math.abs(cur.ms - raw) < 1000, cur.ms + ' vs ' + raw);
  // 근거가 바뀌면 이어 붙이지 않는다
  assert.deepEqual(L.smoothEta(first, { ms: 42, basis: 'rooms' }, 700, 20000), { ms: 42, basis: 'rooms' });
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
