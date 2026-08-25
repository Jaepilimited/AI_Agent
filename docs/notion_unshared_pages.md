# 노션 인테그레이션 미공유 페이지 (수정일 없음) — 2026-08-25 실측

Qdrant Cloud 색인 1,854 청크 중 `last_edited_time` 이 비어 있는 **notion 소스** 문서다.
이 페이지들은 API 대신 **공개 링크를 긁어** 색인됐다 (`ingest_page.py`: "공개 notion.site 는
last_edited_time 을 알 수 없으므로 존재 여부만 확인").

⛔ 수정일이 없으면 **"값이 다르면 최신 문서를 따르라" 규칙이 작동하지 않는다.**
실제로 붐따 #105(야근 식대)가 그래서 2023년 문서의 10,000원으로 답했다 —
15,000원이 적힌 `복리후생` 은 이 목록에 있다.

## 해야 할 일

각 페이지를 노션에서 열고 **`...` → 연결(Connections) → 인테그레이션 추가**.
그 다음 05:00 `qdrant_pipeline_daily` 가 API 로 다시 수집하면 수정일이 채워진다.

## 페이지 21개

| 청크 | 팀 | 페이지 | URL |
|---:|---|---|---|
| 10 | PEOPLE | 근태/휴가 | https://www.notion.so/1156714bbf68805abd8cd5215300c2ec |
| 6 | PEOPLE | 복리후생 | https://www.notion.so/1156714bbf6880b681dbce22a89b18d8 |
| 3 | Craver | Craver | (URL 미기록) |
| 3 | PEOPLE | 명함 및 각종 서류 | https://www.notion.so/5f504c36fcc845cf99e2943e1cc0f379 |
| 3 | PEOPLE | 시설 | https://www.notion.so/5bbe47d1099f435eb0d3a7d4fbb1c807 |
| 3 | PEOPLE | 채용 | https://www.notion.so/1156714bbf6880aa8189f1c5d64620fe |
| 2 | PEOPLE | CRAVER 지식in | https://www.notion.so/7cecb6b53fac43c888127729c29b7436 |
| 2 | PEOPLE | 업무 툴 | https://www.notion.so/143997dd9d55431b9853dbf919d9f873 |
| 1 | PEOPLE | (리더용) 하반기 다면 피드백 가이드 | https://www.notion.so/2a86714bbf68814cbce8c0a58644c160 |
| 1 | DB | DB | (URL 미기록) |
| 1 | IT | IT | (URL 미기록) |
| 1 | [GM]EAST | Notion | https://www.notion.so/2702b4283b0080378c95fb9701781a4f |
| 1 | [GM]EAST | Notion | https://www.notion.so/2e62b4283b0080f497dbd5d00d8d1ae7 |
| 1 | PEOPLE | [CRAVER] IT 가이드/매뉴얼 | Notion | https://www.notion.so/2a32b4283b0080968e3ad4a19f3ad26c |
| 1 | [GM]WEST | [GM WEST] 시딩 대시보드 | https://www.notion.so/fffe501bc5c049649666962435430429 |
| 1 | PEOPLE | 공통 역량 & 핵심 가치 | https://www.notion.so/21d6714bbf6880df9a81f82f005e0bcd |
| 1 | PEOPLE | 교육 | https://www.notion.so/1156714bbf6880e3b845e963c0675489 |
| 1 | PEOPLE | 다면 피드백 | https://www.notion.so/2a46714bbf6881f2b41df47382af9f4e |
| 1 | PEOPLE | 보상 | https://www.notion.so/14a6714bbf68807a941ceb12d3c1bb5b |
| 1 | PEOPLE | 사내근로복지기금 | https://www.notion.so/21c6714bbf688009b6a7d216c1213ee1 |
| 1 | PEOPLE | 퇴사 | https://www.notion.so/14c6714bbf6880d3a971f3bf9e0a5dd9 |
