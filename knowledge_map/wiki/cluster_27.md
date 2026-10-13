# Cluster 27

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 3

## Purpose
스프레드시트 기반의 정형 운영 데이터(수상 이력, OP 재고, 모델 초상권)를 MariaDB에 동기화하고 정확한 표 조회를 통해 질의에 응답하는 모듈군입니다. 수치와 사용 권한 같은 정밀 데이터에서 벡터 검색(임베딩)의 한계를 극복하고, 규정 위반 및 오안내 리스크를 방지하기 위해 구조화된 데이터 조회 방식을 채택했습니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/awards.py` — 수상/랭킹 내역 및 마케팅 활용 가능 여부(O/△/X 판정)를 MariaDB에 적재하고 조회하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/inventory.py` — 운영팀 구글 시트의 SKU별/창고별 OP 재고 수량을 매일 MariaDB로 동기화하고 조회하는 모듈
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/model_rights.py` — 모델 사진의 사용 가능 매체·지역·기간 및 라이선스 위반 방지를 위한 초상권 사용 가능 여부 판정 모듈

## Key Concepts
- **표 기반 정밀 조회 (vs 벡터 검색)** — 문장 유사도를 찾는 임베딩은 숫자(예: `1`과 `10`, `104` 등 재고/순위 수치) 구분에 취약하므로, 수치와 조건 판정이 핵심인 도메인에는 정형 테이블 조회를 적용합니다.
- **마케팅 활용성 판정 (Awards)** — 수상 실적 및 랭킹 1위 데이터의 마케팅 활용 가능 여부(O/△/X)를 판정하여 잘못된 수상 내역 홍보를 방지합니다.
- **OP 재고 조회 (Inventory)** — `SKU | 품목명칭 | 창고별 수량` 구조의 정형 데이터를 바탕으로 수치 왜곡 없이 재고 현황을 응답합니다.
- **모델 초상권 규정 준수 (Model Rights)** — 모델 사진의 계약 기간, 사용 매체, 지역 조건을 검증하여 위반 시 발생하는 에이전시 위약금(모델당 200~500만 원) 리스크를 사전 차단합니다.

## How It Fits In
- **Cluster 09 (`concept:mariadb_storage`)**: `awards.py`를 비롯한 모듈들이 구글 시트 데이터를 정기적으로 적재하고 관리하는 MariaDB 스토리지 인프라를 활용합니다.
- **Cluster 06 (`concept:intent_detection`)**: 사용자의 "이 모델 사진 지금 써도 되나?"와 같은 질의를 분류하여 `model_rights.py`의 조회 로직으로 연결하는 인텐트 감지 기능과 연계됩니다.

## Common Questions This Page Answers
- 왜 수상 내역이나 재고 데이터 검색에 벡터 임베딩 대신 MariaDB 표 조회를 사용하나요?
- 특정 SKU의 창고별 OP 재고 수량은 어떻게 동기화되고 조회되나요?
- 마케터가 특정 모델 사진을 현재 시점에 프로모션 소재로 사용 가능한지 어떻게 검증하나요?
- 수상 실적 및 랭킹 데이터를 마케팅 문구로 활용해도 되는지(O/△/X) 어떻게 판정하나요?