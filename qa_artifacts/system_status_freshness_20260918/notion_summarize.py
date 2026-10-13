"""Summarize metadata collected by the read-only audit."""
from __future__ import annotations
from collections import Counter, defaultdict
from pathlib import Path
import json
import re

ROOT=Path(__file__).parent
def load(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

snapshot=load('notion_snapshot.json')
jobs=load('notion_jobs.json')
cards=load('notion_cards.json')['cards']
hub=load('notion_hub.json')
live={p['page_id']:p for f in sorted(ROOT.glob('notion_sources_*.json'))
      for p in json.loads(f.read_text(encoding='utf-8'))['results']}
def pid(p):
    key=str(p.get('page_id','')).replace('-','')
    if re.fullmatch('[a-fA-F0-9]{32}',key) or key.startswith('inline:'):
        return key
    ids=re.findall('[a-fA-F0-9]{32}',str(p.get('page_url','')).replace('-',''))
    return ids[-1] if ids else key

latest_success=next(x for x in jobs['jobs'] if x['job_id']=='qdrant_pipeline_daily' and x['ok']==1)
lookup={x['id']:x['payload'] for x in snapshot['cloud_points']}
labels={'[GM]WEST':'GM WEST','[GM]EAST':'GM EAST','CS':'BP (CS 문서)'}
notes={
    'B2B1':('미확인','HUB의 빈 문단 1개(텍스트 0자), Qdrant 본문·링크 카드 0개. 현재 원본에 학습할 내용 없음.'),
    'B2B2':('실패','원본 2026-06-29 수정 문서 1개가 2026-04-13 사본으로 남음(48청크). 현재 링크 카드 해시 107개는 모두 존재하나 과거 카드 해시 14개도 잔존.'),
    'BCM':('최신 확정','기존 본문 3문서의 원본 수정시각이 모두 일치. 현재 목록의 링크 카드 2개도 해시 일치. 본문 전체 재수집 검증은 아님.'),
    'CS':('최신 확정','기존 CS 본문 4문서의 원본 수정시각이 모두 일치. 현재 링크 63개 해시는 모두 존재. 현재 카드 목록에 없는 과거 카드 1개 잔존. BP 제품 Q&A·제품정보는 별도 감사 범위.'),
    'Craver':('미확인','현재 HUB 본문 1,545자를 같은 청크 규칙으로 해시 대조: 주사본 3청크 모두 일치. 다른 적재기의 옛 inline 사본 3청크는 별도 식별·검증 불가.'),
    'DB':('미확인','일반 본문 1문서의 수정시각 일치. 별도 inline 사본 1개는 원본 수정시각을 보유하지 않음.'),
    'JBT':('미확인','본문 원본 1문서가 Notion API 404. 현재 링크 카드 해시는 존재하나 연결된 본문 최신 여부는 확인 불가.'),
    'KBT':('최신 확정','기존 본문 1문서(4청크)의 원본 수정시각 일치. 본문 전체 재수집 검증은 아님.'),
    'PEOPLE':('미확인','16개 문서 중 1개 수정시각 일치, 15개는 Notion API 404. 공개 사이트 등의 현재 본문 해시는 재수집하지 않았음.'),
    '[GM]EAST':('미확인','기존 5문서 원본 수정시각은 모두 일치하지만 현재 WAS 로컬 색인과 Cloud의 119청크 내용 해시가 다름. 원본 DB 내부 행 전체 해시는 대조하지 않아 어느 사본이 최신인지 확정 불가.'),
    '[GM]WEST':('실패','94문서 중 18개가 원본보다 오래됨(15개는 금일 05:00 이전 변경, 3개는 오늘 배치 이후 변경). 1개는 최신·옛청크 혼재, 1개 원본404. 74개는 모든 청크 수정시각 일치.'),
}
rows=[]
stale_details=[]
for team in notes:
    cloud=[x['payload'] for x in snapshot['cloud_points'] if x['payload'].get('team')==team]
    body=[p for p in cloud if p.get('source')!='team_resources']
    pages=defaultdict(list)
    for p in body:
        pages[pid(p)].append(p)
    counts=Counter()
    chunks=Counter()
    page_details=[]
    for key,points in pages.items():
        src=live.get(key,{})
        source_date=src.get('last_edited_time','')
        dates={p.get('last_edited_time') for p in points}
        if src.get('http_status')!=200:
            state='source_unavailable' if src else 'unmapped_inline'
        elif dates=={source_date}:
            state='all_current'
        elif source_date in dates:
            state='current_plus_old'
        elif any(v and re.match(r'\d{4}',v) and v<source_date for v in dates):
            state='stale_only'
        else:
            state='no_comparable_date'
        counts[state]+=1
        chunks[state]+=len(points)
        if state in ('stale_only','current_plus_old'):
            d={'team':team,'page_id':key,'indexed_dates':sorted(dates),'source_last_edited':source_date,
               'chunks':len(points),'state':state,
               'changed_after_todays_schedule':source_date>'2026-09-17T20:00:00.000Z'}
            page_details.append(d)
            stale_details.append(d)
    link_points=[p for p in cloud if p.get('source')=='team_resources']
    current_cards=[p for p in cards if p['team']==team]
    current_hashes={p['content_sha256'] for p in current_cards}
    cloud_hashes={p.get('content_sha256') for p in link_points}
    mismatch=0
    for point in snapshot['local_points']:
        if point['payload'].get('team')!=team:
            continue
        cloud_p=lookup.get(point['id'])
        if cloud_p and cloud_p.get('content_sha256')!=point['payload'].get('content_sha256'):
            mismatch+=1
    status,note=notes[team]
    rows.append({
        'label':labels.get(team,team),'team':team,'classification':status,'scope':'기존 색인 문서 메타데이터',
        'cloud_chunks':len(cloud),'body_chunks':len(body),'body_documents':len(pages),
        'metadata_counts':dict(counts),'metadata_chunk_counts':dict(chunks),
        'stale_documents':counts['stale_only'],'mixed_current_old_documents':counts['current_plus_old'],
        'stale_before_todays_schedule':sum(d['state']=='stale_only' and not d['changed_after_todays_schedule'] for d in page_details),
        'changed_after_todays_schedule':sum(d['state']=='stale_only' and d['changed_after_todays_schedule'] for d in page_details),
        'unverifiable_documents':counts['source_unavailable']+counts['unmapped_inline']+counts['no_comparable_date']-(1 if team=='Craver' else 0),
        'live_content_hash_confirmed_documents':1 if team=='Craver' else 0,
        'cloud_link_cards':len(link_points),'current_catalog_cards':len(current_cards),
        'missing_current_card_hashes':len(current_hashes-cloud_hashes),
        'historical_card_hashes_not_in_current_card_set':len(cloud_hashes-current_hashes),
        'local_cloud_hash_mismatch_chunks':mismatch,
        'last_successful_shared_pipeline_kst':latest_success['finished_at'],
        'last_successful_catalog_sync_kst':'2026-09-18 01:01:14',
        'note':note,
    })
summary={
    'audited_at_kst':'2026-09-18 15:40–15:53',
    'source_metadata_unique_ids':len(live),'source_http_status_counts':dict(Counter(str(v.get('http_status')) for v in live.values())),
    'source_api_read_scope':'기존 Cloud/WAS 로컬에 색인된 Notion 문서 ID + 미공유 기록. 신규 미색인 문서 전수 발견, 공개 웹 본문 및 DB 내부 행 전수 검증은 포함하지 않음.',
    'latest_pipeline_attempt':jobs['jobs'][0],'latest_successful_pipeline':latest_success,
    'latest_pipeline_interruption_evidence':load('notion_runtime.json'),
    'cloud_total_chunks':snapshot['cloud_count'],'cloud_link_cards':sum(p['payload'].get('source')=='team_resources' for p in snapshot['cloud_points']),
    'local_json_mtime_kst':snapshot['local_mtime_kst'],'rows':rows,'stale_documents':stale_details,
    'craver_live_hash_match_chunks':3,'b2b1_source_empty':True,
    'excluded_scope':['PR(source_kind=pr_issues), 다른 담당이 원본 시트 감사','source=google_sheets 342청크(팀 없음), 별도 시트 감사','IT 및 임베딩 된 구글시트는 System Status 숨김'],
}
(ROOT/'notion_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

lines=[
    '# Notion 팀 문서 최신화 감사', '',
    '2026-09-18 15:40–15:53 KST, 프로덕션 WAS·Qdrant Cloud·MariaDB·Notion API 읽기 전용 조회. 원문·고객 자료를 저장하지 않았고 동기화, 배포, 알림을 실행하지 않았다.', '',
    'System Status의 팀 `정상`은 청크 존재 여부다. 오늘 05:00 파이프라인은 완료 기록이 없으며, 마지막 정상 완료는 **9/17 05:05:21 KST**다. 오늘 05:03:05에 로컬 본문 저장까지 진행됐고 05:03:19에 서버 프로세스가 바뀌었다. 현재 프로세스 시작은 14:16:28이므로 05:00의 같은 작업이 계속 실행되는 상태가 아니다. 팀 목록 동기화는 **9/18 01:01:14 KST**, 1,117건으로 성공했다.', '',
    '**판정 범위:** 아래 `최신 확정`은 기존 색인에 들어 있는 문서들의 현재 Notion `last_edited_time`과 저장된 시각이 전부 일치한다는 뜻이다. 본문·DB 내부 모든 행의 해시 일치, 새 문서 누락 없음까지 보증하지 않는다. Craver의 주사본만 현재 본문 해시까지 대조했다.', '',
    '| 화면 항목 | 판정 | 본문 문서 | 최신 미반영 | 확인 불가 | 근거 |',
    '|---|---|---:|---:|---:|---|',
]
for r in rows:
    stale=str(r['stale_documents'])
    if r['mixed_current_old_documents']:
        stale+=' + 혼재 '+str(r['mixed_current_old_documents'])
    unknown=r['unverifiable_documents']
    if r['team']=='Craver':
        unknown=1
    lines.append(f"| {r['label']} | {r['classification']} | {r['body_documents']} | {stale} | {unknown} | {r['note']} |")
lines += ['',
    '원본 메타데이터는 **171개 ID**를 조회했다(200 응답 154개, 404 응답 17개). 같은 문서를 두 팀이 참조할 수 있어 팀별 합계와 ID 수는 다르다. 옛 inline 식별자는 원본 Notion page ID로 매핑되지 않는 별도 미확인 대상이다.', '',
    'Cloud 6,352청크 중 **4,291개는 팀 자료 링크 카드**다. 현재 목록 카드 173개(JBT 1, B2B2 107, BCM 2, CS 63)의 내용 해시는 모두 Cloud에 있다. 날짜·ID가 다른 과거 중복 카드도 남아 있어 `2,876 chunks` 같은 숫자를 최신 문서 수로 읽으면 안 된다. GM EAST/WEST의 과거 링크 카드는 현재 본문 색인과 중복되어 새 카드 생성 대상에서 빠질 수도 있으므로 링크가 삭제됐다고 단정하지 않았다.', '',
    'GM WEST 최신 미반영 18개 중 3개는 오늘 09:32·10:45·11:54 KST 변경되어 다음 일일 동기화 전까지 주기 안의 대기분이다. 나머지 15개와 B2B2 1개는 이전 변경이 반영되지 않은 사본이다. GM WEST의 별도 1개 문서는 최신 청크와 9/14의 옛 청크가 함께 남아 있다.', '',
    'B2B1은 현재 HUB 안에 빈 문단 한 개(텍스트 0자)뿐이고 색인이 0개다. `safety.get_safety_status()`는 Cloud에 존재하는 팀만 상태에 추가하므로 B2B1 항목 자체가 반환되지 않는다. 프론트의 기본 표시를 최신화 성공 증거로 삼을 수 없다.', '',
    'PR은 `source_kind=pr_issues`인 시트 전용 적재로 별도 담당 감사에 넘겼다. `source=google_sheets` 342청크와 화면에서 숨기는 IT·임베딩 된 구글시트도 이 팀별 Notion 판정에서 제외했다. BP의 제품 Q&A와 제품정보 역시 별도 시트/제품정보 감사 결과를 합쳐야 한다.', '',
    '근거 파일: [구조화 요약](notion_summary.json), [작업 이력](notion_jobs.json), [Cloud/로컬 메타데이터](notion_snapshot.json), [현재 링크 카드 메타데이터](notion_cards.json), [재시작 증거](notion_runtime.json), [B2B1·Craver 원본 확인](notion_hub.json), `notion_sources_0.json`–`notion_sources_6.json`.',
]
(ROOT/'notion_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'summary':'notion_summary.json','report':'notion_report.md','source_http_status_counts':summary['source_http_status_counts'],
    'teams':[{k:r[k] for k in ('label','classification','body_documents','stale_documents','mixed_current_old_documents','unverifiable_documents','local_cloud_hash_mismatch_chunks')} for r in rows]},ensure_ascii=True))
