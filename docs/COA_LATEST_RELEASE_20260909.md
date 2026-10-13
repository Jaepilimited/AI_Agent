# COA·MSDS 최신 파일 선택 — 2026-09-09

같은 LOT의 COA 또는 같은 품목의 MSDS가 여러 개 검색되면, 검색된 후보 중 Google Drive 최종 수정일이 가장 최근인 파일 한 개를 선택한다. 화면 안내에도 선택 기준과 수정 시각(KST)을 표시하며, 다운로드와 ZIP에는 선택된 파일 ID만 전달한다.

## 선택 범위

- 기존 LOT·품목 일치 판정 이후에 최신 파일을 고른다. 다른 LOT, 짧거나 불확실한 LOT, 본문에서만 일치하는 COA를 자동 확정하지 않는다.
- 후보의 수정일이 없거나 유효하지 않으면 직접 선택하도록 남긴다. 서로 다른 최신 문서의 수정 시각이 같아도 직접 선택한다.
- 기존 복사본 중복 제거는 유지하면서, 수정 시각을 비교할 수 있는 복사본은 최신본을 대표로 삼는다.
- 긴 MSDS 품목명의 추가 단어가 후보에서 확인되지 않으면 자동 선택하지 않는다.
- 기존 검색 범위·페이지 처리·권한은 유지한다. 전체 Drive 문서를 새로 열거하거나 문서 내부의 발행일·버전을 판독하는 기능은 아니다.

## 검증 및 운영 반영

- `tests/test_coa_finder.py`와 `tests/test_coa_finder_api.py`: **201 passed**. 실제 분류 및 검색 응답에서 선택된 최신 파일이 ZIP까지 전달되는 경로를 포함한다.
- `tests/frontend/test_coa_finder_downloads.py`: **32 passed**. 최신 파일 표시·선택·다운로드 동작을 포함한다.
- 운영 WAS `10.1.150.5`에 `app/core/coa_finder.py`, `app/api/coa_finder_api.py`, `app/static/coa_finder.html` **3개 파일** 반영. 기존 Entra 인증 변경을 보존했다.
- 반영 전 운영 원본과 준비한 기준 파일의 해시를 대조하고 백업했다. 반영 후 파일 해시, `ai-craver`의 `active` 상태, `/health` HTTP 200을 확인했다.
- 운영 가상환경에서 고정 입력으로 COA·MSDS 최신 선택, 동일/누락 수정일의 수동 선택, 다른 LOT 거부, 화면 안내를 확인했다. 이 검증은 실제 사용자 Google Drive 파일 조회를 수행하지 않는다.
- 인증 방법 확인: `password=false`, `entra=true`.

검증 결과: `qa_artifacts/coa_latest_20260909/verification.json`

배포 준비 및 복구 스크립트: `qa_artifacts/coa_latest_20260909/release.py`

운영 원본 백업: `/home/jeffrey/AI_Agent/.release_backups/coa_latest_20260909`
