# SKIN1004 Knowledge Map — 설계 문서

**Date**: 2026-04-16
**Author**: Claude Code (brainstormed with 임재필)
**Status**: Approved, ready for implementation plan
**Related**: [Karpathy LLM Wiki](https://github.com/Astro-Han/karpathy-llm-wiki), [Graphify](https://github.com/safishamsi/graphify)

## 문제

Claude Code 세션은 매 대화마다 기억이 없다. 같은 질문에 같은 grep·Read를 반복하고, `docs/` 60+개 + `app/**/*.py` 수백 파일을 탐색하느라 토큰을 태운다. 지식이 쌓이지 않는다. 자료가 많아질수록 느려진다.

## 해결책 요약

카파시 LLM Wiki 개념(AI가 원본을 한 번 읽고 구조화된 위키로 정리 → 이후 원본 대신 위키를 읽음)을 Graphify 개념(자동 생성된 graph.json 한 페이지로 전체 지도 압축)과 결합한다. 단일 경량 Python 스크립트가 매일 03:00 자동 실행되어 `knowledge/` 디렉토리에 지도를 갱신한다. `CLAUDE.md` 한 줄이 모든 세션에 "먼저 `knowledge/GRAPH_REPORT.md`를 읽어라"를 강제한다.

기대 효과: 쿼리당 토큰 최대 71.5배 절감 (Graphify 벤치 기준, 본 프로젝트에서는 실측 필요), 자료가 쌓일수록 답변 품질 복리 성장, 유지비용 ≈ 0 (AI가 스스로 갱신).

## 목표 / 비목표

**목표**:
- Claude Code 개발자 세션의 파일 탐색 비용 최소화
- `docs/*.md` + `app/**/*.py`의 구조·의미 지도 자동 생성
- 매일 03:00 자동 증분 빌드 (캐시 기반, 변경 파일만 재처리)
- `CLAUDE.md` 트리거 한 섹션으로 Claude가 자동으로 지도부터 읽도록 강제
- graph.json 스키마·무결성 검증

**비목표** (현 범위 제외):
- 런타임 챗 에이전트(3000 서버 사용자)용 지식 라우트 추가 — 실사용 빈도 낮음, 별도 단계에서 검토
- 이미지/비디오/오디오 처리 — 이 프로젝트는 `.md` + `.py`만 필요
- 25+ 언어 다국어 AST — Python 단일
- `tests/`, `scripts/`, `backup_*`, `logs/`, `temp_*` 인덱싱 — 노이즈
- Graphify 본체 설치 — Windows 설치 고통 회피, 커스텀 구현으로 대체

## 소비자

**오직 개발자 측 (Claude Code 세션)**. 즉 이 리포에서 작업하는 나 자신이 유일한 소비자다.

런타임 챗 에이전트는 이미 BigQuery/Notion/GWS/CS/Multi 라우터가 있고 실사용자는 매출·마케팅 데이터를 물어본다. "이 AI가 어떻게 동작해?" 같은 메타 질문은 0에 수렴하므로 Knowledge Map을 runtime 라우터에 추가하지 않는다.

## 디렉토리 레이아웃

```
AI_Agent/
├── knowledge/                        ← 산출물 루트 (git 추적)
│   ├── graph.json                    ← 노드+엣지 (기계용, ~100KB 목표)
│   ├── GRAPH_REPORT.md               ← 한 페이지 요약 (에이전트 첫 진입점)
│   ├── wiki/
│   │   ├── index.md                  ← 목차
│   │   ├── log.md                    ← append-only 빌드 기록
│   │   ├── agents/*.md               ← BQ/Notion/GWS/CS/Orchestrator
│   │   ├── api/*.md                  ← auth/admin/conversation
│   │   ├── infrastructure/*.md       ← MariaDB/LLM clients/Safety
│   │   ├── frontend/*.md             ← chat.js/auth.js/style.css
│   │   └── history/*.md              ← update_log_* 재구성 (분기별)
│   └── .cache/                       ← .gitignore
│       └── file_hashes.json          ← SHA256 + mtime, 증분 빌드용
│
├── app/knowledge/                    ← 빌더 모듈
│   ├── __init__.py
│   ├── builder.py                    ← 메인 오케스트레이터 (~150줄)
│   ├── ast_parser.py                 ← Python AST 추출 (~100줄)
│   ├── md_parser.py                  ← Markdown 구조 추출 (~60줄)
│   ├── semantic.py                   ← Gemini Flash 의미 추출 (~120줄)
│   ├── graph.py                      ← networkx 빌드 + Louvain 커뮤니티 (~80줄)
│   ├── exporters.py                  ← graph.json + wiki/*.md + REPORT 쓰기 (~150줄)
│   └── cache.py                      ← 해시/mtime 캐시 (~40줄)
│
├── prompts/knowledge/                ← Flash 프롬프트 분리 (프로젝트 기존 패턴)
│   ├── extract_concepts.txt
│   ├── extract_relations.txt
│   └── synthesize_wiki.txt
│
└── scripts/
    ├── build_knowledge_graph.py      ← CLI 엔트리포인트 (~50줄)
    ├── validate_graph.py             ← graph.json 스키마·무결성 검증
    └── register_knowledge_task.ps1   ← Task Scheduler 등록
```

## 인덱싱 범위

**포함**:
- `docs/*.md` 전체 (아키텍처, PRD, 업데이트 로그, changelog)
- `app/**/*.py` 전체 (Python 소스)

**제외 (명시적)**:
- `app/frontend/**` (HTML/CSS/JS — 스코프 밖)
- `app/static/**`
- `backup_before_custom_frontend/`, `open-webui-backup/`, `_docker_recovery_temp/`, `craver_design_clone/`
- `logs/`, `test_*.txt`, `temp_*`, `ss_*.png`, `qa_*.log`
- `docs/QA_*.md` 5개 (대용량 QA 리포트 — 노이즈)
- `tests/`, `scripts/` (일회성 스크립트, tests는 실행 대상)
- `prompts/` (지도 구성요소이지 인덱싱 대상 아님)

제외 패턴은 `app/knowledge/builder.py`의 `EXCLUDE_PATTERNS` 상수로 중앙 관리.

## graph.json 스키마

```json
{
  "version": "1.0",
  "generated_at": "2026-04-17T03:00:00+09:00",
  "source_commit": "33c6257",
  "stats": {
    "files": 187,
    "nodes": 542,
    "edges": 1284,
    "clusters": 18,
    "build_duration_sec": 42.3,
    "flash_calls": 23,
    "cache_hits": 164
  },
  "nodes": [
    {
      "id": "app.agents.bq_agent.BQAgent",
      "type": "class",
      "file": "app/agents/bq_agent.py",
      "lines": [42, 318],
      "summary": "LangGraph SQL agent with generate→validate→execute→format loop.",
      "tags": ["bigquery", "langgraph", "sql_agent"],
      "cluster": "agents",
      "confidence": 1.0,
      "wiki_page": "wiki/agents/bq_agent.md"
    },
    {
      "id": "concept:megawari_filter",
      "type": "concept",
      "summary": "큐텐 Q10 메가와리 기간 필터링 (2023-2026 분기별)",
      "mentioned_in": [
        "CLAUDE.md",
        "app/agents/bq_agent.py#L189",
        "docs/update_log_2026-02-25.md"
      ],
      "confidence": 0.85
    }
  ],
  "edges": [
    {"from": "app.main", "to": "app.agents.orchestrator.route", "type": "calls", "confidence": 1.0},
    {"from": "app.agents.bq_agent", "to": "concept:megawari_filter", "type": "implements", "confidence": 0.9},
    {"from": "docs/SKIN1004_PRD_v6", "to": "docs/SKIN1004_PRD_v5", "type": "supersedes", "confidence": 1.0}
  ]
}
```

**노드 타입**: `file / module / class / function / concept / doc / history_entry`

**엣지 타입**: `calls / imports / references / supersedes / implements / documented_in / related_to`

**신뢰도**:
- `1.0` — AST에서 직접 추출 (import, function call)
- `0.5~0.9` — Flash가 문서에서 추론한 관계
- `<0.5` — 플래그만, GRAPH_REPORT에 노출하지 않음

## 빌드 파이프라인

```
[scripts/build_knowledge_graph.py]
        │
        ▼
[1. File discovery]  docs/**/*.md + app/**/*.py  (제외 패턴 적용)
        │
        ▼
[2. Cache check]     SHA256 비교, 변경된 파일만 통과
        │
        ▼
[3. AST + MD parse]  struct 추출 (confidence 1.0)
        │
        ▼
[4. Flash semantic]  변경 파일 묶음별 병렬 호출 (asyncio.gather, N=10)
                     → 개념·관계·요약 추출
        │
        ▼
[5. NetworkX merge]  신규 결과 + 기존 graph.json 병합
        │
        ▼
[6. Louvain clustering]  python-louvain으로 커뮤니티 탐지
        │
        ▼
[7. Exporters]       graph.json / wiki/**/*.md / GRAPH_REPORT.md / log.md append
```

**병렬성**: Flash 호출은 `asyncio.gather`로 최대 10개 동시 실행 (프로젝트 기존 `asyncio.to_thread` 패턴과 일관). 레이트 리밋 히트 시 지수 백오프.

**실패 처리**:
- Flash 호출 실패 → AST만 있는 최소 노드 생성 (confidence=1.0 struct만), semantic 필드는 null
- 부분 실패 허용 — 빌드 전체 중단하지 않음
- 실패는 `log.md`에 기록 + stderr 요약

## GRAPH_REPORT.md 구조 (에이전트 첫 진입점)

```markdown
# SKIN1004 AI Agent — Knowledge Map
**Generated**: 2026-04-17 03:00 KST · **Files**: 187 · **Nodes**: 542 · **Commit**: 33c6257

## 🎯 What this project is
(Gemini Flash가 top-level README/PRD에서 합성한 1-2 문단 요약)

## 🏛️ Top-level Clusters
1. **agents/** (89 nodes) — BQ/Notion/GWS/CS/Orchestrator
2. **api/** (54 nodes) — auth/admin/conversation
...

## 🌟 God Nodes (엣지가 많은 중심 노드)
- `app.main` (67 edges)
- `app.agents.orchestrator.route` (54 edges)
...

## ❓ Suggested Questions This Map Can Answer
- "BQ agent의 SQL fallback 로직이 어디?"
- "2026-03-25 업데이트에서 뭐가 바뀜?"
...

## 📅 Recent Changes (from history/)
- 2026-04-15 · Notion 로컬 벡터 검색
- 2026-04-12 · chat.js 이벤트 리스너 정리
...

## 🔗 How to navigate
관련 노드만 읽는 워크플로우 설명.
```

## CLAUDE.md 트리거

프로젝트 루트 `CLAUDE.md` 최상단(배포 규칙 바로 아래)에 섹션 추가:

```markdown
## 🧠 Knowledge Map (먼저 읽기 — 필수)

**모든 작업 전에 다음 순서를 지켜라**:

1. **먼저** `knowledge/GRAPH_REPORT.md`를 읽는다. 한 페이지에 프로젝트 전체 구조·중심 노드·최근 변경이 요약돼 있다.
2. 필요하면 `knowledge/graph.json`을 읽어 관련 노드 2~3개만 골라낸다.
3. 골라낸 노드의 `wiki_page` 경로(`knowledge/wiki/**.md`)만 Read한다.
4. **그래도 부족할 때만** 원본 파일(`app/**`, `docs/**`)을 Read하거나 Grep한다.

**금지 행동**:
- GRAPH_REPORT.md를 건너뛰고 바로 Grep/Glob하지 마라. 토큰 낭비다.
- `knowledge/` 디렉토리를 무시하지 마라. 매일 03:00 자동 업데이트되는 신뢰 가능한 소스다.
- 지도가 낡았다고 판단되면 `python scripts/build_knowledge_graph.py --force` 실행을 제안하라.

**지도가 커버하지 못하는 영역**:
- `tests/`, `scripts/` 일회성 파일, `backup_*`, `logs/`, `temp_*` — 이들은 지도에 없다. 직접 탐색해야 한다.
```

## 스케줄러

**Windows Task Scheduler** (기존 `SKIN1004-AD-Sync-Daily` 패턴):

```
Name:        SKIN1004-Graphify-Daily
Trigger:     Daily at 03:00 KST
Action:      python scripts\build_knowledge_graph.py
WorkingDir:  C:\Users\DB_PC\Desktop\python_bcj\AI_Agent
Log:         logs/knowledge_build.log (append)
RunLevel:    Highest (user 로그인 시 백그라운드)
```

등록 스크립트: `scripts/register_knowledge_task.ps1` (기존 sync 패턴 미러링)

**빌드 종료 후**:
1. `git status --porcelain knowledge/` 확인
2. 변경이 있으면 `git add knowledge/ && git commit -m "chore: knowledge map daily rebuild ($(date +%Y-%m-%d))"`
3. push는 하지 않음 (사용자가 다음날 확인 후 수동 push)

## 첫 빌드 (bootstrap) vs 증분

**첫 빌드**:
- 187개 파일 전부 Flash 호출
- 병렬 N=10, 파일당 평균 2-3초
- 약 5-7분 소요
- Flash 비용 약 $1-2 (승인됨)
- 실행 명령: `python scripts/build_knowledge_graph.py --bootstrap`

**증분 빌드**:
- 일일 스케줄러 자동
- 하루 바뀌는 파일 1-10개 범위
- 30초 이내, 비용 무시 가능

**캐시 키**: `SHA256(file_content) + mtime`. 둘 다 일치하면 Flash 호출 스킵.

## git 정책

**추적**:
- `knowledge/graph.json`
- `knowledge/GRAPH_REPORT.md`
- `knowledge/wiki/**/*.md`

**무시** (`.gitignore`):
- `knowledge/.cache/`

커밋되는 graph.json은 diff가 클 수 있다. 완화책: exporters가 노드/엣지를 **id 기준으로 정렬**해서 쓰기 → diff 최소화 + 리뷰 가능성 확보.

## 테스트

**최소 필수**:
- `tests/test_knowledge_ast.py` — 샘플 Python 파일에서 기대 노드(class/function/import) 추출 검증
- `tests/test_knowledge_md.py` — 샘플 Markdown에서 H1/H2/링크 추출 검증
- `tests/test_knowledge_cache.py` — 파일 미변경 시 Flash 호출 안 되는지 (mock)
- `scripts/validate_graph.py` — 스키마 검증, 깨진 wiki_page 참조 탐지, 클러스터 무결성

**CI 없이**:
- 첫 빌드 후 validate_graph.py 수동 실행으로 검증
- 이후 일일 스케줄러가 실패하면 log에 기록

## 의존성 추가

`requirements.txt`에 2줄:
```
networkx>=3.0
python-louvain>=0.16
```

둘 다 순수 Python, Windows 설치 무난.

## 위험 & 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| Flash 비용 첫 빌드 $1-2 초과 | 예산 초과 | 드라이런 모드 `--dry-run`으로 파일 수·예상 호출 수 사전 확인 |
| graph.json diff 스팸 | PR 리뷰 방해 | 정렬된 직렬화 + 커밋 메시지로 통계만 노출 |
| Flash 호출 레이트 리밋 | 빌드 실패 | 지수 백오프, 부분 성공 허용 |
| 지도가 Claude 세션에서 실제로 읽히지 않음 | 효과 0 | CLAUDE.md에 **금지 행동** 명시 + 영상의 강조점 반영 |
| 파일 삭제가 그래프에 반영 안 됨 | 낡은 노드 잔존 | 빌더가 매 실행 시 discovery 결과와 기존 노드를 비교, 사라진 파일의 노드 제거 |
| wiki/*.md 자동 생성물이 낡음 | 잘못된 정보 제공 | 각 wiki 페이지 상단에 `Generated: YYYY-MM-DD` + `Source: file#commit` 메타데이터 |

## 성공 기준

- [ ] 첫 빌드 완료, `knowledge/GRAPH_REPORT.md` + `graph.json` + `wiki/` 생성
- [ ] `scripts/validate_graph.py` 통과 (스키마 + 무결성)
- [ ] 일일 Task Scheduler 등록 후 다음 날 03:00 자동 실행 확인
- [ ] CLAUDE.md 트리거 섹션 반영, 새 세션에서 Claude가 GRAPH_REPORT를 먼저 읽는지 검증
- [ ] 샘플 질문 3개에서 실제 토큰 절감 측정 (baseline 대비)
  - "orchestrator가 어떻게 라우팅하는지?"
  - "2026-03-25 업데이트 내용은?"
  - "BQ agent의 메가와리 필터 로직은?"

## Out of scope (후속 단계에서 검토)

- 런타임 챗 에이전트에 `self` 라우트 추가 (사용자가 사내 문서 질의)
- 프론트엔드(`.html`/`.js`/`.css`) 인덱싱
- 이미지 파일(`docs/*.png`, 스크린샷) OCR + 인덱싱
- Notion Craver 벡터와 graph.json 통합
- pre-commit hook 추가 (일일 스케줄러로 충분한지 검증 후 재고)

## 참고

- [karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) — 카파시 LLM 위키 개념
- [graphify](https://github.com/safishamsi/graphify) — 지식 그래프 자동 생성 도구
- 영상: [유튜브 해설](https://www.youtube.com/watch?v=YxraHvGzWTs) — 두 개념 결합 + CLAUDE.md 트리거 패턴
