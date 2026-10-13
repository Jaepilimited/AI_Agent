"""Assemble locally captured read-only evidence. No application side effects."""
import datetime as dt
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent
def read(name):
    return json.loads((DIR / name).read_text(encoding='utf-8'))

inventory=read('status_inventory.json')
bq=read('status_bq_metadata.json')
tables={r['path']:r for r in bq['tables']}
dates={r['id']:r['rows'][0] for i in range(3) for r in read(f'status_bq_dates_{i}.json')['results'] if 'rows' in r}
kst=dt.timezone(dt.timedelta(hours=9))
def stamp(value):
    return dt.datetime.fromisoformat(value).astimezone(kst).strftime('%m-%d %H:%M:%S')

source_dates={
 '매출':'Date', '제품':'Date', '광고':'date', '마케팅':'Date', 'Shopify':'Date',
 '플랫폼':'Date', '인플루언서':'Measurement_date', '아마존검색':'Week_Start_Date_Sunday',
 '프로모션':'start_date', '메타광고':'collect_date', '국내몰 리뷰':'collected_date',
 '해외몰 리뷰':'collected_date', '매장 리뷰':'review_date', '물류':'order_date',
 '국내CS':'received_at', '해외CS':'refund_created_at',
}
notes={
 '매출':'오늘 수정·오늘 날짜 데이터 존재. 전체 원천시스템과 완전 대조는 미실시.',
 '제품':'오늘 수정·오늘 날짜 데이터 존재. 판매수량 조회 테이블.',
 '손익':'2026-01~06만 존재. 7~9월 자료가 없는 범위 제한. 마지막 테이블 수정 08-03.',
 '광고':'오늘 수정. 매체별 집행 여부와 적재 완료 여부는 이 최대일만으로 단정하지 않음.',
 '마케팅':'오늘 수정. 미래 날짜는 최신 실적 판정에서 제외.',
 'Shopify':'오늘 수정. 실제 판매일은 전날까지.',
 '플랫폼':'오늘 수정·오늘 날짜 데이터 존재.',
 '인플루언서':'오늘 수정. 콘텐츠 업로드일은 09-17, 측정일은 09-18까지.',
 '아마존검색':'오늘 수정. 주간 데이터이며 최신 주 시작일 09-06.',
 '프로모션':'실행 일정. 원본 변경 없는 정상 상태인지 별도 확인하지 않아 최신 여부 미확정.',
 '메타광고':'오늘 수정·오늘 수집일 확인.',
 '국내몰 리뷰':'오늘 수정·오늘 수집일 확인. 기존 몰별 테이블 대신 실제 통합 테이블 조회.',
 '해외몰 리뷰':'오늘 수정·오늘 수집일 확인. 최신 리뷰 작성일은 09-17.',
 '매장 리뷰':'오늘 수정. 최신 리뷰 작성일 09-15가 수집 지연을 의미하지는 않음.',
 '물류':'오늘 수정. 현재 날짜까지의 주문일 확인. 미래 예약 일정은 분리.',
 '국내CS':'지연 확인: 대시보드에는 09-17 3건·09-18 1건이 있으나 DB 최신 접수일은 09-16.',
 '해외CS':'오늘 15:00:05 적재. 최신 환불 14:45:33. Shopify 원본 전체와 완전 대조 미실시.',
}
lines=[
 '# System Status 전체 데이터 최신화 점검', '',
 '점검일: 2026-09-18, 모든 표시 시각은 KST. 운영 WAS·MariaDB·BigQuery·Qdrant와 원본 Google Sheets/Notion을 읽어 확인했다.', '',
 '**결론: 모든 항목이 최신인 상태는 아니다.** 국내 CS 최근 접수 4건의 미반영, 손익의 2026년 6월까지인 제공 범위, Notion 동기화 완료 미기록 및 색인 문제를 확인했다. 시트 계열은 아래 범위에서 원본과 반영 상태가 일치했다.', '',
 '운영 레지스트리 34개 항목을 전부 포함했다. BigQuery 17개 항목은 실제 조회 테이블 18개(물류의 환율 보조 테이블 포함)를 조사했다. Notion 동적 항목은 Qdrant 팀 목록과 현재 표시 규칙을 추가 대조했다. BP는 제품 Q&A 시트와 Notion CS 문서의 결과를 구분했다.', '',
 '## BigQuery 항목 17개', '',
 '아래 “수정”은 BigQuery 테이블 메타데이터 시각이다. 당일 수정은 적재 동작의 증거이며 원천 데이터의 전량 반영을 보증하지 않는다. 최대 업무일은 미래 날짜를 제외했으며, 일정·주간 보고·리뷰 작성일은 매일 움직여야 하는 값이 아니다.', '',
 '| System Status 항목 | 실제 행 수 | 테이블 수정 | 데이터 기준일/범위 | 확인 결과 |',
 '|---|---:|---|---|---|',
]
for entry in inventory['registry']:
    name=entry['key']
    if entry['route']!='bigquery':
        continue
    path=inventory['bq_sources'][name][0]
    t=tables[path]
    d=dates[path]
    latest='2026-01~2026-06' if name=='손익' else d.get('latest_nonfuture_'+source_dates[name],'-')
    lines.append(f"| {name} | {d['n']:,} | {stamp(t['modified'])} | {latest} | {notes[name]} |")
fx=tables['skin1004-319714.Sales_Integration.Exchange_Rate']
lines.extend([
 '', f"물류 환율 보조 테이블: {fx['rows']:,}행, 수정 {stamp(fx['modified'])}. 2026-10 월 기준까지 존재하며 현재 월은 2026-09다.", '',
 '오늘 수정된 것은 17개 중 14개다. 나머지는 국내 CS(09-17), 프로모션(09-17), 손익(08-03)이다. 프로모션은 원본 수정이 없었을 가능성이 있어 지연으로 확정하지 않았다.', '',
 '국내·해외 CS 대시보드는 로컬에서 열리지만 운영 WAS에서는 두 집계 API 모두 프록시 403으로 차단됐다. 해외 API는 현재 `bigquery_overseas`를 반환하며 운영 앱의 기대값 `google_sheets_overseas`와도 다르다. 최신화와 별개의 답변 경로 문제다. [CS 상세 근거](cs_report.md)', '',
 '## Google Sheets 및 BP 보조 데이터', '',
 '| 항목 | 반영 시각 | 원본 대조 결과 |',
 '|---|---|---|',
 '| BP 제품 Q&A | 09-18 15:40:02 | 원본 수정 11:51 이후 재적재. 원본 및 실행 중 캐시 모두 923건. 실행 중 캐시 내용 해시는 미비교. |',
 '| OP 재고 | 09-18 11:20:02 | 원본과 15,018행 전체 내용 일치. |',
 '| OP 유통기한 | 09-18 11:20:06 | 3,264행 수량·날짜 일치. 로트 대소문자 3건 표준화 후 전체 일치. 원본 기준일 자체는 09-17 마감. |',
 '| 수상 | 09-18 04:40:02 | 원본과 206행 전체 내용 일치. |',
 '| 초상권 | 09-18 04:30:02 | 34명·301개 기간 모두 원본과 일치. |',
 '| PR | 09-18 04:50 | 원본·적재 목록·클라우드 색인 356건 모두 내용 일치. |',
 '| BP 전성분(보조) | 09-18 04:00:04 | 원본과 84종 전체 내용 일치. |',
 '| BP 제품정보(보조, Notion 원본) | 09-18 04:20:29 | 원본 8라인·55제품과 등록 목록 일치. 적재 후 수정 페이지 없음. 본문 해시 미비교. |',
 '', '시트 동기화 작업의 최근 7일 기록에서 실패는 없었다. OP는 통상 답변 시 원본 시트를 직접 조회하며 DB는 대체 사본이다. 비교한 것은 운영 파서가 소비하는 탭과 필드다. 사용하지 않는 모든 원본 셀까지 비교했다는 의미는 아니다. [시트 상세 근거](sheets_report.md)', '',
 '## Notion 팀 문서 및 BP의 CS 문서', '',
 '대상: B2B1, B2B2, BCM, Craver, DB, GM EAST, GM WEST, JBT, KBT, PEOPLE, BP(CS 문서). PR 전용 시트와 OP는 위 표에서 별도로 판정했다.', '',
 '- 마지막 전체 파이프라인 성공 기록: **09-17 05:05:21**.',
 '- 09-18 05:00 시작 작업은 종료 시각·성공 여부가 NULL이다. 05:03:05 본문 단계 저장 뒤 05:03:19 새 서버 프로세스 시작 로그가 있어, 같은 작업이 지금도 계속 실행 중인 것으로 해석할 수 없다.',
 '- 09-18 07:31 자가점검은 구문서 조각 20개 잔존과 수정일 없는 53/467개 로컬 Notion 청크를 보고했다.',
 '- 클라우드 6,352개 조각에는 링크 카드 4,291개가 섞여 있어 개수만으로 본문 최신화를 판단할 수 없다.',
 '', '[팀별 현재 원본 대조와 확인 불가 범위](notion_report.md)', '',
 '## 보고서와 Google Workspace', '',
 '| 항목 | 확인 결과 |',
 '|---|---|',
 '| 보고서 | 별도 원본 DB 사본이 아니라 생성 시 조회한 데이터로 만드는 산출물. 운영 블록 15종 로딩 확인. 생성 결과의 최신성은 위 소스에 따름. |',
 '| Google Workspace | Gmail·Calendar·Drive를 요청 시 직접 조회. OAuth 설정만으로 전체 계정 정상 또는 최신 반영을 보증하지 않음. 09-18 07:31 자가점검에서 연결 15명 중 14명 정상·1명 재연결 필요 기록. 이번 감사는 토큰 갱신이나 사용자 메일 조회를 실행하지 않음. |',
 '',
 '참고: 서비스 응답 내부의 메일 발송은 SMTP 미개방으로 비활성, 저장된 토큰 파일은 20개였다. 이 둘은 현재 34개 소스 레지스트리에 포함된 별도 항목이 아니며 토큰 파일 수는 정상 연결 사용자 수와 다르다. 사용자 디렉터리 배치는 09-17 22:00 Entra 연결 오류 기록이 있으나 위 데이터 소스 최신성 판정과 분리했다.', '',
 '## System Status가 최신성을 보증하지 못하는 이유', '',
 '1. BigQuery 상태는 주로 연결 오류·점검 상태·행 수 급감을 본다. 최신 원본 반영 여부를 전부 검사하지 않는다.',
 '2. 실제 항목 **손익·국내몰 리뷰·해외몰 리뷰·매장 리뷰**는 감시 항목명과 불일치한다. 프론트는 대응 서비스 결과가 없으면 `status: ok, detail: 대기`로 표시한다.',
 '3. Notion은 조각이 존재하면 정상으로 표시한다. 구버전 중복, 원본 접근 실패, 배치 완료 미기록을 녹색 표시만으로 판정할 수 없다.',
 '4. 자가점검의 “파생 사본 10종 모두 최신/빠진 소스 없음”은 등록된 파생 사본 감시 결과다. System Status 전체 34개 및 외부 원본 완전 대조 통과와는 다르다.', '',
 '확인한 운영 소스 `safety.py`, `chat.js`, `orchestrator.py`, `cs_dashboard.py`의 해시는 점검에 사용한 로컬 파일과 일치했다.', '',
 '## 점검 범위와 근거', '',
 '- 조회 관측은 09-18 오후에 순차 수행했으므로 모든 원본을 정확히 같은 순간에 고정한 스냅샷은 아니다.',
 '- WAS localhost `/health`, `/health/ready`는 200·ready였고 BP 캐시 923건을 확인했다. Web 주소 `10.1.100.5`의 HTTP는 이 점검 환경과 WAS에서 타임아웃이어서 웹 경로 정상까지 확인한 것으로 확대하지 않았다.',
 '- 애플리케이션 소스·DB·원본 시트·동기화 상태를 변경하거나 재배포하지 않았다. 로컬 감사 스크립트와 보고서만 저장했다.',
 '- 전체 코드 테스트 4,301개 통과 결과와 운영 데이터 최신화는 별도 검증이다.',
 '',
 '원본 증거: `status_inventory.json`, `status_bq_metadata.json`, `status_bq_dates_0.json`~`2.json`, `status_aux.json`, `status_public_health.json`, `sheets_*.json`, `cs_*.json`, `notion_*.json`.',
])
notion_text=(DIR/'notion_report.md').read_text(encoding='utf-8')
notion_lines=notion_text.splitlines()
table_start=next(i for i,line in enumerate(notion_lines) if line.startswith('| 화면 항목 |'))
table_end=next(i for i in range(table_start+1,len(notion_lines)) if not notion_lines[i].startswith('|'))
team_table=notion_lines[table_start:table_end]
assert len(team_table)==13, 'Expected 11 Notion source rows plus header and separator'
insert_at=lines.index('[팀별 현재 원본 대조와 확인 불가 범위](notion_report.md)')
lines[insert_at:insert_at]=[
 '기존 문서 171개 ID의 원본 수정 시각을 대조했다(154개 조회 성공, 17개 API 404). 아래 최신 판정은 기존 색인 문서의 수정 시각 기준이며, 새 문서 누락이나 모든 본문 해시까지 보증하는 것은 아니다.',
 '', *team_table, '',
 'GM WEST 18개 중 3개는 오늘 배치 이후 변경되어 다음 주기 대기분이다. 이전 변경 미반영은 GM WEST 15개와 B2B2 1개이며, GM WEST에 최신·옛 사본 혼재 문서 1개가 별도로 있다. GM EAST의 119개 해시 불일치는 사본 불일치이며 어느 쪽이 최신인지는 미확정이다.',
 '',
]
(DIR/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'report':str(DIR/'REPORT.md'),'registry_entries':len(inventory['registry']),'bigquery_sources':len(inventory['bq_sources']),'bigquery_tables':len(tables)},ensure_ascii=False))
