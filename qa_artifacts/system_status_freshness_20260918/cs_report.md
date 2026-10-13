# CS 최신화 읽기 전용 점검

실측: 2026-09-18 15:48–15:50 KST. 집계 응답과 메타데이터만 증거로 저장했으며 고객·주문 원문은 저장하지 않았다. 동기화, 적재, 코드 변경, 배포, 알림 발송은 실행하지 않았다.

| 항목 | 확인 결과 | 판정 |
|---|---|---|
| 국내 CS BigQuery | 552행. 모든 `source_fetched_at`·`ingested_at`는 9/17 08:00:14 KST. `received_at` 최신은 9/16. | 최신 원본 반영이 뒤처짐 |
| 국내 CS 대시보드 | 로컬 GET 200, `source=google_sheets`. API 표시 `lastUpdated=9/18 11:23:49 KST`. 9/17 접수 3건, 9/18 접수 1건이 집계됨. | BQ에 없는 최근 접수일이 실제 원본 화면에 존재. 원본 구글시트와 API 캐시의 완전 일치까지는 미검증 |
| 해외 CS BigQuery | 1,204행. 최종 `ingested_at=9/18 15:00:05 KST`, 최신 `refund_created_at=9/18 14:45:33 KST`. | 오늘 적재 확인. Shopify 원본 전체 대조는 하지 않음 |
| 해외 CS 대시보드 | 로컬 GET 200, 1,204건, 9/18 2건. API 표시 `lastUpdated=9/18 15:21:48 KST`. 현재 `source=bigquery_overseas`. | 지금 응답은 BigQuery 원본임. 과거 해외 구글시트 응답이라는 전제를 유지하면 안 됨 |
| 운영 WAS → 대시보드 | 국내·해외 endpoint 모두 HTTP 403, `X-Squid-Error: ERR_ACCESS_DENIED 0`. | 연결 차단 지속. 로컬 웹에서 열리는 것과 운영 앱이 API를 읽는 것은 다름 |
| 운영 앱의 해외 API 계약 | 실제 WAS `app.core.cs_dashboard._SOURCES['overseas']='google_sheets_overseas'`; 외부 실측값은 `bigquery_overseas`. | 프록시 접근이 해결되어도 현재 검증 조건에서 거절됨. 이번 감사에서 수정하지 않음 |

국내 전체 건수는 대시보드 543건, BQ 552행이다. 날짜 없는 행, 필터·처리 단위 등 차이를 정밀 대조하지 않았으므로 9행을 유실 또는 중복으로 단정하지 않는다. 최신화 지연 근거는 총건수 차이가 아니라 **대시보드에는 9/17·9/18 접수가 존재하는데 BQ의 최신 접수일은 9/16인 점**이다.

`lastUpdated`는 API가 표시하는 시각이며 구글 Drive 원본 파일의 최종 수정 시각을 직접 확인한 값은 아니다. 해외 응답도 이번 실측에서 출처가 바뀌어 있었으므로 해외 구글시트가 최신화되었다는 증거로 쓰지 않는다.

증거: `cs_local.json`, `cs_was.json`, `cs_recent_days.json`, `cs_was_contract.json`. BQ 근거는 같은 디렉터리의 `status_bq_dates_*.json` 및 `status_bq_metadata.json`에 있다.
