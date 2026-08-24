# 아키텍처 캔버스 설계 (2026-08-24)

> Dify 식 캔버스로 **요청이 지나는 흐름**을 보고, 노드를 눌러 그 자리에서 고친다.
> admin(임재필) 전용.

## 왜 만드나

지금 시스템이 불투명하다. 어떤 질문이 어느 경로로 가고 어디서 느려지는지 **코드를 읽어야** 안다.

실제로 이번 세션에서 겪은 것:
- `"안녕? 오늘 뭐 도와줄 수 있어?"` 가 22.8초 걸린 원인(`_gather_search_context` 가 Pro 사용 + `"오늘"` 이 검색을 켬)을 찾는 데 조사가 필요했다
- `"그래프로 그려줘"` 가 direct 로 떨어져 가짜 ASCII 차트를 만든 것도, 경로를 눈으로 볼 수 없어서 늦게 드러났다

관측 데이터는 이미 쌓고 있는데 **하나로 꿰어지지 않았다**:

| 있는 것 | 한계 |
|---|---|
| `audit_logs` (`route`·`first_token_ms`·`total_ms`·`model`·`context_len`) | 기록 지점이 `routes.py:356` **한 곳**. 종단 시간만. **비스트리밍은 아예 안 남는다** |
| `llm_usage` (토큰·캐시) | 요청과 연결돼 있지 않다 |
| BigQuery 바이트 · 실행 SQL · 캐시 히트 | 흩어져 있다 |

## 왜 Dify 도입이 아니라 자체 구축인가

Dify 는 **Dify 안에서 만든 워크플로우**만 보여준다. 지금 시스템을 캔버스로 보려면 전체를 Dify 로 재구현해야 하고, 그러면 `semantic.py`·`answer_check`·`judge`·`_localize_*`·FI 방어선 5겹을 전부 코드 노드로 다시 짜야 한다. **이 시스템의 정확도는 대부분 LLM 뒤에 붙은 결정적 코드에 있다** — 브로슈어가 "Before(Pain-point) 복잡한 코드"라 부르는 그것이 여기서는 부채가 아니라 본체다.

인프라도 막는다 (2026-08-24 실측):

| 후보 | Dify 구동 | 근거 |
|---|---|---|
| WAS 10.1.150.5 / APP 10.1.150.105 | ❌ | 2 vCPU · **RAM 2GB(여유 1GB)** · LXC 컨테이너 · Docker 없음 · `sudo` 제한 |
| DB_PC | ✅ | 32코어 · RAM 63.7GB(여유 34.2GB) · Docker 29.1.3 |

사내 서버에 올리려면 **IT 에 새 VM 요청이 선행**이다.

→ **결론**: 캔버스는 기존 앱의 `/admin` 탭으로 만든다. 인증·DB·배포를 재사용하고 Docker 가 필요 없다.
Dify 는 *"틀려도 피해가 작은 신규 워크플로우를 몇 시간에 만든다"* 는 **별개 문제**로 따로 판단한다 (명함 등록·티켓 분류 등). 이 문서의 범위가 아니다.

## 절대 원칙 — 그래프는 생성된다, 그려지지 않는다

⛔ **손으로 그린 다이어그램은 반드시 낡고, 낡으면 거짓말을 한다.**

이 프로젝트가 같은 사고를 세 번 겪었다:
- direct 시스템 프롬프트가 **두 벌** → 한쪽만 고쳐 *"보고서라는 별도 메뉴는 없습니다"* 라고 답했다
- `@@` 데이터소스 목록이 프론트·서버 **두 벌** → 질문이 조용히 오염됐다
- `Continent1` 값 목록이 자동 주입본과 손 목록 **두 벌** → *"남미·중미 데이터가 없다"* 는 오답 (2026-08-24 수정)

따라서:
1. 그래프는 **선언 하나**(`app/flow/spec.py`)에서 나오고, 선언은 **실제 실행 함수·레지스트리를 가리킨다**
2. 하위 그래프 중 LangGraph 로 된 것은 **선언조차 하지 않고 런타임 객체에서 추출**한다
3. **정적 검사가 어긋남을 잡는다** — 선언에 있는데 코드에 없는 노드 / 코드에 있는데 선언에 없는 라우트
4. 편집은 **사본이 아니라 런타임이 실제로 읽는 값**을 고친다

⚠️ 기존 `knowledge_map/`(파일 의존 그래프, 1,832 노드)과는 **다른 층위**다. 이건 **요청 실행 흐름**(약 25~30 노드)이다. 섞지 않는다.

## 노드 모델

### 상위 캔버스 (항상 보임, 14 노드)

```
USER INPUT
  → @@ 소스 파싱
  → 후속경로 상속 (_inherit_route_for_followup)
  → 라우터: 키워드 (_keyword_classify_ex)
       ├─ 확신   ─────────────┐
       └─ 불확신 → 라우터: LLM ┤
                              ↓
   direct · bigquery · notion · cs · gws · team · model_rights · report · multi
                              ↓
   답변 수치검증 (answer_check)
                              ↓
                           응답
```

라우트 9종은 실측 확인함 (`orchestrator.py` 출현 빈도: bigquery 57 · direct 47 · notion 38 · cs 22 · multi 18 · gws 14 · team 13 · model_rights 7 · report 4).

### 하위 그래프 (노드 더블클릭 시 펼침)

**`bigquery` — LangGraph 에서 자동 추출.** 손으로 적지 않는다:

```python
workflow.add_node("generate_sql", generate_sql)
workflow.add_node("validate_sql", validate_sql_node)
workflow.add_node("execute_sql", execute_sql)
workflow.add_node("format_answer", format_answer)
workflow.set_entry_point("generate_sql")
workflow.add_edge("generate_sql", "validate_sql")
workflow.add_conditional_edges("validate_sql", should_execute,
    {"execute_sql": "execute_sql", "format_answer": "format_answer"})
workflow.add_edge("execute_sql", "format_answer")
```

`compile()` 전 `workflow` 객체를 읽으면 노드·엣지가 그대로 나온다. **선언이 곧 실행이라 어긋날 수 없다.**

이것만으로 지금 안 보이는 사실이 드러난다 — **검증에 실패하면 실행을 건너뛰고 바로 답변 포맷으로 간다.**

**`report`** (모듈 호출 순서라 선언 필요):
`registry(필터 추출) → intent(유형 판정) → planner(계획·Gemini) → semantic(SQL 생성) → blocks(서술) → judge(판정) → insight(해석·Claude) → render(HTML)`

**`direct`**: `검색 그라운딩 판정 → (선택)Flash 검색 → 시스템 프롬프트 조립 → Claude 스트리밍`

### 흐름 선언 형식

```python
Node("router.keyword", fn="orchestrator._keyword_classify_ex",
     knobs=["_DATA_KEYWORDS", "_STRONG_DATA", "_GUARDED"]),
Node("bq", subgraph="langgraph:sql_agent.create_sql_agent"),
Node("report.plan", fn="reports.planner.plan",
     knobs=["intent.INTENTS", "blocks.BLOCKS"]),
```

## 단계

**v1 = 1단계만 만든다.** 화면이 나오면 2·3단계에서 무엇이 필요한지가 훨씬 구체적으로 보인다.

### 1단계 — 흐름 그리기 (v1)

- `app/flow/spec.py` 흐름 선언 + LangGraph 추출기
- `GET /api/admin/flow` → 노드·엣지 JSON
- `/admin` 캔버스 탭: **`vis-network` 재사용**, 노드 클릭 → 우측 패널에 그 노드의 파일·함수·현재 설정값(읽기 전용)

  ✅ **선례가 이미 있다.** Knowledge Wiki 그래프(`chat.html:4` 로드 → `chat.js:4456`,
  컨테이너 `wiki-graph-visual`)가 `new vis.Network(container, {nodes, edges}, ...)` 로
  그리고 `network.on("click")` → 상세 패널을 띄운다. **캔버스+우측패널 패턴이 이 앱에서
  이미 동작 중이다.** 새 라이브러리도, 새 렌더 기법도 필요 없다.

  ⚠️ 단 **레이아웃은 다르다.** 위키 그래프는 물리엔진(`barnesHut`)으로 뭉치게 그리는데,
  흐름도는 **좌→우 계층 배치**여야 한다 → `layout: { hierarchical: { direction: 'LR' } }`.
  설정 차이지 새 기계가 아니다.

  ⛔ 초안에 *"프로덕션은 CDN 접근이 제한적이므로 외부 라이브러리 금지"* 라고 적었는데
  **틀렸다** (2026-08-24 실측으로 정정). 프론트는 이미 `cdn.jsdelivr.net`(marked,
  highlight.js) · `cdnjs.cloudflare.com` · `fonts.googleapis.com` · `unpkg.com`(vis-network)
  을 쓴다. **서버 egress(프록시 허용목록)와 사용자 브라우저의 인터넷 접근은 다른 경로다** —
  이걸 혼동해 없는 제약을 만들 뻔했다. 문서를 믿지 말고 찔러 보라는 원칙이 여기에도 적용됐다.
- 정적 검사 `static_flow_spec_matches_code` 를 `static_checks.ALL` 에 추가

### 2단계 — 실행 얹기

- `request_id` 를 요청 전체에 관통시키고 단계별 span 기록 (`request_steps`)
- `llm_usage`·BigQuery 바이트·캐시 히트를 `request_id` 로 연결
- **비스트리밍 경로도 기록** (현재 누락)
- 캔버스 노드에 호출 수 · p50/p95 · 실패율 오버레이, 느린 요청 클릭 → 그 요청의 타임라인

### 3단계 — 노드 편집

| 등급 | 대상 | 관문 |
|---|---|---|
| 🟢 | 라우팅 키워드 · `@@` 소스 · 용어사전 · `INTENTS` 절 순서 · `BLOCKS` on/off · 모델·effort | **정적 검사** (초 단위, 특히 키워드 충돌) |
| 🟡 | 프롬프트 섹션 · 검색 그라운딩 키워드 · 되묻기 규칙 | **골든셋 관련 문항** (수십 초) |
| 🔴 | `semantic.py` SQL 생성 · `validate_sql` · FI 방어선 5겹 · `answer_check` · `judge` 임계값 · `_localize_*` | **편집 불가** (보이되 읽기 전용) |

⛔ 🔴 을 여는 순간 캔버스가 사고 발생기가 된다. **2026-08-24 에 고친 두 오답이 전부 이 층에서 났다.**
다만 **보이게는 한다** — 어떤 방어선이 어디 걸려 있는지 보는 것 자체가 불투명함 해소다.

**저장 — 트레이드오프를 명시한다.**

편집값은 파이썬 소스가 아니라 **DB 오버레이**에 두고 런타임이 `DB > 코드 기본값` 순으로 읽는다 (배포 없이 반영·되돌리기 쉬움).

이건 **두 번째 저장소**이고, 그게 이 프로젝트가 반복해 당한 실패 모양이다. 그래서:

1. **캔버스가 항상 표시한다** — 기본값과 다른 노드에 배지. 숨은 오버라이드가 없다
2. **고아 오버라이드를 자가 점검이 잡는다** — 코드에서 사라진 노브를 가리키면 조용히 무시되지 않고 실패로 뜬다
3. **전 변경이 이력에 남고 한 번에 되돌아간다**

관문에 걸리면 저장은 되되 **draft 로 남고 활성화되지 않는다.** 어느 골든 문항이 실패했는지 함께 보여준다.

## 권한

admin 전용. **판정은 서버에서 DB 조회로** 한다 — JWT·프론트 값은 stale 위험이 있어 신뢰하지 않는다 (FI 권한과 같은 사상).

## 안 만드는 것 (YAGNI)

- **노드 연결 재배선** — 지금 흐름은 파이썬 분기(`if route == "bigquery"`)라 캔버스로 재배선하려면 런타임을 그래프 해석기로 바꿔야 한다. `orchestrator.py` 3,400줄이 가드레일과 얽혀 있어 이전이 아니라 재설계다. **별개 결정으로 미룬다**
- **빈 캔버스에서 새 워크플로우 만들기** — Dify 가 제일 잘하는 일이다. 직접 만들지 않는다
- 다중 사용자 편집 · 협업 · 권한 세분화 — admin 전용이므로 불필요
- 외부 관측 도구(Langfuse 등) 연동 — 2단계 span 을 표준 모양으로 남기면 나중에 표시 계층만 바꾸면 된다. 지금 결정하지 않는다

## 리스크

| 리스크 | 방어 |
|---|---|
| 그림이 코드와 어긋난다 | 정적 검사 + LangGraph 자동 추출. **이게 실패하면 기능 전체가 무의미하다** |
| 캔버스가 느려진다 | 노드 30개 수준. 실행 통계는 집계 테이블에서 읽는다 |
| 편집이 조용히 라우팅을 깬다 | 🟢 정적 검사 / 🟡 골든셋 관문, draft 상태, 되돌리기 |
| 2단계 계측이 응답을 느리게 한다 | span 기록은 비동기(`_record_async` 와 같은 방식), 실패해도 서비스에 영향 없음 |

## 검증

- `static_flow_spec_matches_code` — 선언 ↔ 코드 일치 (pytest + 자가 점검 **같은 함수**)
- 캔버스 노드 수가 라우트 9종을 모두 포함하는지
- LangGraph 추출 결과가 `create_sql_agent()` 의 실제 노드·엣지와 같은지
- 2단계: 한 요청의 span 합이 `audit_logs.total_ms` 와 오차 범위 내인지
