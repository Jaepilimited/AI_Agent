from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime

import pytest

from app.core import saved_questions


def _row(
    question_id: int,
    cadence: str,
    *,
    user_id: int = 7,
    weekday: int | None = None,
    last_run_at: datetime | None = None,
) -> dict:
    return {
        "id": question_id,
        "user_id": user_id,
        "question": f"질문 {question_id}",
        "cadence": cadence,
        "weekday": weekday,
        "enabled": 1,
        "created_at": datetime(2026, 7, 1, 9, 0),
        "last_run_at": last_run_at,
        "last_answer": None,
        "last_error": None,
        "last_status": None,
    }


def test_add_rejects_the_sixth_question_with_a_reason(monkeypatch):
    """⛔ 상한 초과가 INSERT까지 가면 아침 조회 비용이 사용자 수와 함께 조용히 불어난다."""

    monkeypatch.setattr(saved_questions, "fetch_one", lambda *_a, **_k: {"c": 5})
    monkeypatch.setattr(
        saved_questions,
        "execute_lastid",
        lambda *_a, **_k: pytest.fail("상한을 넘은 질문을 INSERT하면 안 된다"),
    )

    result = saved_questions.add(7, "쇼피 매출 알려줘", "daily")

    assert result["ok"] is False
    assert result["id"] is None
    assert "5" in result["reason"]


def test_add_inserts_the_fifth_question(monkeypatch):
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(saved_questions, "fetch_one", lambda *_a, **_k: {"c": 4})
    monkeypatch.setattr(
        saved_questions,
        "execute_lastid",
        lambda sql, params=(): calls.append((sql, params)) or 55,
    )

    result = saved_questions.add(7, "  이번 달 매출  ", "weekly", weekday=2)

    assert result == {"ok": True, "id": 55, "reason": None}
    assert calls[-1][1] == (7, "이번 달 매출", "weekly", 2)


def test_owner_scope_is_inside_list_delete_and_toggle_sql(monkeypatch):
    """⛔ 호출부 선확인은 빠질 수 있으므로 쓰기 SQL 자체가 소유자를 증명해야 한다."""

    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        saved_questions,
        "fetch_all",
        lambda sql, params=(): calls.append((sql, params)) or [],
    )
    monkeypatch.setattr(
        saved_questions,
        "execute",
        lambda sql, params=(): calls.append((sql, params)) or 0,
    )

    assert saved_questions.list_for(7) == []
    assert "WHERE user_id = %s" in calls[-1][0]
    assert calls[-1][1] == (7,)

    assert saved_questions.remove(7, 91) is False
    assert "DELETE FROM saved_questions WHERE id = %s AND user_id = %s" in calls[-1][0]
    assert calls[-1][1] == (91, 7)

    assert saved_questions.set_enabled(7, 91, True) is False
    assert "WHERE id = %s AND user_id = %s" in calls[-1][0]
    assert calls[-1][1] == (1, 91, 7)

    source = inspect.getsource(saved_questions)
    assert "DELETE FROM saved_questions WHERE id = %s AND user_id = %s" in source
    assert "UPDATE saved_questions SET enabled = %s WHERE id = %s AND user_id = %s" in source


@pytest.mark.parametrize(
    ("today", "expected_ids"),
    [
        # 2026-08-01은 토요일이므로 월간 질문은 다음 근무일인 8/3에 실행된다.
        (date(2026, 8, 3), {1, 2, 4}),
        # 2026-09-01은 화요일이자 월 첫 근무일이다.
        (date(2026, 9, 1), {1, 3, 4}),
    ],
)
def test_due_selects_daily_weekly_and_monthly(monkeypatch, today, expected_ids):
    rows = [
        _row(1, "daily"),
        _row(2, "weekly", weekday=0),
        _row(3, "weekly", weekday=1),
        _row(4, "monthly"),
        _row(5, "daily", last_run_at=datetime.combine(today, datetime.min.time())),
    ]
    monkeypatch.setattr(saved_questions, "fetch_all", lambda *_a, **_k: rows)

    assert {row["id"] for row in saved_questions.due(today)} == expected_ids


def test_due_returns_nothing_on_weekends_without_touching_db(monkeypatch):
    monkeypatch.setattr(
        saved_questions,
        "fetch_all",
        lambda *_a, **_k: pytest.fail("⚠️ 주말에는 DB 조회 자체가 필요 없다"),
    )

    assert saved_questions.due(date(2026, 8, 8)) == []


def test_ensure_tables_uses_the_declared_idempotent_schema(monkeypatch):
    statements: list[str] = []
    monkeypatch.setattr(saved_questions, "execute", lambda sql, params=(): statements.append(sql) or 0)

    saved_questions.ensure_tables()

    ddl = statements[0]
    assert "CREATE TABLE IF NOT EXISTS saved_questions" in ddl
    assert "question VARCHAR(500)" in ddl
    assert "ENUM('daily','weekly','monthly')" in ddl
    assert "last_answer MEDIUMTEXT" in ddl


def test_record_result_persists_success_empty_and_error_status(monkeypatch):
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        saved_questions,
        "execute",
        lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    saved_questions.record_result(1, answer="답변")
    saved_questions.record_result(2, answer="   ")
    saved_questions.record_result(3, error="RuntimeError: 실패")

    assert calls[0][1] == ("답변", None, "success", 1)
    assert calls[1][1] == ("", None, "empty", 2)
    assert calls[2][1] == (None, "RuntimeError: 실패", "error", 3)
    assert all("last_run_at = NOW()" in sql for sql, _params in calls)


@pytest.mark.asyncio
async def test_run_continues_after_one_failure_and_records_the_error(monkeypatch):
    rows = [_row(1, "daily", user_id=11), _row(2, "daily", user_id=12), _row(3, "daily", user_id=13)]
    monkeypatch.setattr(saved_questions, "due", lambda _today: rows)
    monkeypatch.setattr(
        saved_questions,
        "fetch_one",
        lambda _sql, params=(): {
            "email": f"user{params[0]}@example.com",
            "can_view_fi": 1 if params[0] == 13 else 0,
        },
    )

    routed: list[tuple[str, dict]] = []

    class FakeOrchestrator:
        async def route_and_execute(self, query, messages, model, **kwargs):
            routed.append((query, kwargs))
            if query == "질문 2":
                raise RuntimeError("실패" + "x" * 300)
            return {"answer": "" if query == "질문 3" else "정상 답변"}

    recorded: list[tuple[int, str | None, str | None]] = []
    monkeypatch.setattr(saved_questions, "_new_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(
        saved_questions,
        "record_result",
        lambda question_id, answer=None, error=None: recorded.append((question_id, answer, error)),
    )

    result = await saved_questions.run_saved_questions(datetime(2026, 8, 3, 9, 0))

    assert {query for query, _kwargs in routed} == {"질문 1", "질문 2", "질문 3"}
    assert result == {"selected": 3, "succeeded": 2, "failed": 1, "empty": 1}
    errors = {question_id: error for question_id, _answer, error in recorded if error}
    assert errors[2].startswith("RuntimeError: 실패")
    assert len(errors[2].split(": ", 1)[1]) == 200
    assert (1, "정상 답변", None) in recorded
    assert (3, "", None) in recorded


@pytest.mark.asyncio
async def test_run_reads_each_users_fi_permission_from_directory_users(monkeypatch):
    rows = [_row(1, "daily", user_id=21), _row(2, "daily", user_id=22)]
    monkeypatch.setattr(saved_questions, "due", lambda _today: rows)
    db_calls: list[tuple[str, tuple]] = []

    def fetch_permission(sql, params=()):
        db_calls.append((sql, params))
        return {"email": f"u{params[0]}@example.com", "can_view_fi": params[0] == 22}

    monkeypatch.setattr(saved_questions, "fetch_one", fetch_permission)
    observed: dict[str, bool] = {}

    class FakeOrchestrator:
        async def route_and_execute(self, query, messages, model, **kwargs):
            observed[query] = kwargs["can_view_fi"]
            return {"answer": "ok"}

    monkeypatch.setattr(saved_questions, "_new_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(saved_questions, "record_result", lambda *_a, **_k: None)

    await saved_questions.run_saved_questions(datetime(2026, 8, 3, 9, 0))

    assert observed == {"질문 1": False, "질문 2": True}
    assert all("directory_users" in sql and "can_view_fi" in sql for sql, _params in db_calls)
    source = inspect.getsource(saved_questions.run_saved_questions)
    assert "can_view_fi=False" not in source
    assert "can_view_fi=is_admin or permission" in source


@pytest.mark.asyncio
async def test_run_limits_concurrent_questions_to_three(monkeypatch):
    rows = [_row(question_id, "daily", user_id=question_id) for question_id in range(1, 8)]
    monkeypatch.setattr(saved_questions, "due", lambda _today: rows)
    monkeypatch.setattr(
        saved_questions,
        "fetch_one",
        lambda _sql, params=(): {"email": f"u{params[0]}@example.com", "can_view_fi": 0},
    )
    active = 0
    maximum = 0

    class FakeOrchestrator:
        async def route_and_execute(self, query, messages, model, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"answer": "ok"}

    monkeypatch.setattr(saved_questions, "_new_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(saved_questions, "record_result", lambda *_a, **_k: None)

    await saved_questions.run_saved_questions(datetime(2026, 8, 3, 9, 0))

    # ⛔ `== 3` 으로 단언하지 마라 — **부하에 따라 흔들린다.** 전체 스위트를 돌릴 때
    #    이벤트 루프가 바쁘면 먼저 들어간 작업이 끝난 뒤에야 다음이 시작돼 최대 동시
    #    수가 2나 1로 관측된다 (2026-08-27 실제로 한 번 실패했다). 불안정한 테스트는
    #    없는 것보다 나쁘다 — 실패를 무시하는 습관이 생기고, 그러면 진짜 회귀도 묻힌다.
    #    지켜야 할 성질은 "3을 넘지 않는다" 이지 "정확히 3에 닿는다" 가 아니다.
    assert maximum <= saved_questions.MAX_CONCURRENT_RUNS, (
        f"동시 실행이 상한을 넘었다: {maximum} > {saved_questions.MAX_CONCURRENT_RUNS}"
    )
    assert maximum >= 1
    # 상한 자체는 타이밍과 무관하게 확인한다.
    assert saved_questions.MAX_CONCURRENT_RUNS == 3


@pytest.mark.asyncio
async def test_run_skips_weekends_before_loading_due_questions(monkeypatch):
    monkeypatch.setattr(
        saved_questions,
        "due",
        lambda _today: pytest.fail("⛔ 주말 잡은 저장 질문을 조회하지 않는다"),
    )

    result = await saved_questions.run_saved_questions(datetime(2026, 8, 8, 9, 0))

    assert result == {"selected": 0, "succeeded": 0, "failed": 0, "empty": 0, "skipped": "weekend"}


def test_compose_omits_saved_section_when_there_are_no_saved_questions():
    from app.core import work_briefing

    document = work_briefing.compose(
        day=date(2026, 8, 3),
        now=datetime(2026, 8, 3, 9, 0),
        events=[],
        mails=[],
        window={},
        raw={},
        saved=[],
    )

    assert "saved" not in document
    assert "\uc800\uc7a5\ud55c \uc9c8\ubb38" not in document["markdown"]


def test_compose_truncates_saved_answers_and_adds_the_continuation_link():
    from app.core import work_briefing

    document = work_briefing.compose(
        day=date(2026, 8, 3),
        now=datetime(2026, 8, 3, 9, 0),
        events=[],
        mails=[],
        window={},
        raw={},
        saved=[{
            "question": "Shopee Indonesia sales",
            "last_answer": "a" * 320,
            "last_run_at": datetime(2026, 8, 3, 8, 58),
            "link": "https://cella.example.test",
        }],
    )

    # ⛔ 자른 것은 **자른 티가 나야 한다** (2026-09-02). 표시 없이 끊으면 읽는 사람은
    #    그게 답의 전부인 줄 안다 — 실제로 잔디에 `2. 원인 분석 * *` 이 그대로 나갔다.
    # ⚠️ **카드 요약은 그대로다** — 화면은 훑어보는 자리라 계속 짧게 둔다.
    #    2026-09-07 에 잔디만 전문으로 바꿨고(`answer_full`), 카드는 손대지 않았다.
    row = document["saved"][0]
    assert row["question"] == "Shopee Indonesia sales"
    assert row["answer"] == "a" * 299 + "…"
    assert row["last_run_at"] == "2026-08-03T08:58:00"
    assert row["link"] == "https://cella.example.test"
    # ⛔ 잔디용 전문이 함께 만들어져야 한다 (요약만 실어 표가 사라진다는 제보)
    assert len(row["answer_full"]) > len(row["answer"])
    # \uc81c\ubaa9\uc740 "\ub0b4\uac00 \uc800\uc7a5\ud55c \ubcf4\uace0" \ub2e4 (2026-08-31 \uc0ac\uc6a9\uc790 \uc9c0\uc815). \ub2e4\ub978 \uc808\uacfc \uac19\uc774 \uc774\ubaa8\uc9c0\ub85c \uc5f0\ub2e4.
    assert "\ub0b4\uac00 \uc800\uc7a5\ud55c \ubcf4\uace0" in document["markdown"]

    # \u26d4 \ud56d\ubaa9\ub9c8\ub2e4 \ub9c1\ud06c\ub97c \ubc18\ubcf5\ud558\uc9c0 \uc54a\ub294\ub2e4 \u2014 \uc794\ub514 \ubcf8\ubb38 **\ub9e8 \uc544\ub798\uc5d0 \ud55c \ubc88**\ub9cc \ubd99\ub294\ub2e4
    #    (`personal_briefing._enqueue_jandi`). \ub2e4\uc12f \uac1c\ub97c \uc800\uc7a5\ud558\uba74 \uac19\uc740 \uc8fc\uc18c\uac00 \ub2e4\uc12f \ubc88
    #    \ucc0d\ud600 \ubcf8\ubb38\uc774 \ub9c1\ud06c\ub85c \ub4a4\ub36e\uc778\ub2e4 (2026-08-31 \uac00\ub3c5\uc131 \uc815\ub9ac).
    assert "[\uc140\ub77c\uc5d0\uc11c \uc774\uc5b4\ubcf4\uae30]" not in document["markdown"]

    # \uc2e4\ud589 \uc2dc\uac01\uc740 \uc0ac\ub78c\uc774 \uc77d\ub294 \ud615\uc2dd\uc774\ub2e4 \u2014 ISO \uc6d0\ubb38\uc744 \uadf8\ub300\ub85c \uc2e3\uc9c0 \uc54a\ub294\ub2e4.
    assert "2026-08-03T08:58:00" not in document["markdown"]


def test_personal_briefing_builds_the_document_with_owner_saved_rows():
    from app.core import personal_briefing

    document = personal_briefing.build_document(
        {"items": []},
        {"items": []},
        {},
        date(2026, 8, 3),
        datetime(2026, 8, 3, 9, 0),
        saved=[{
            "question": "Channel top five",
            "last_answer": "answer",
            "last_run_at": datetime(2026, 8, 3, 8, 59),
            "link": "https://cella.example.test",
        }],
    )

    assert document["saved"][0]["question"] == "Channel top five"


def test_personal_briefing_loads_only_the_owners_saved_rows(monkeypatch):
    from app.core import personal_briefing

    observed: list[int] = []
    monkeypatch.setattr(
        personal_briefing.saved_questions,
        "list_for",
        lambda user_id: observed.append(user_id) or [{
            "id": 1,
            "question": "Monthly sales",
            "last_answer": "answer",
        }],
    )
    monkeypatch.setattr("app.core.jandi_notify.base_url", lambda: "https://cella.example.test")

    rows = personal_briefing._safe_saved_for_user(77)

    assert observed == [77]
    assert rows[0]["link"] == "https://cella.example.test"


def test_api_uses_the_authenticated_owner_for_listing(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import saved_questions_api
    from app.db.models import User

    observed: list[int] = []
    monkeypatch.setattr(
        saved_questions_api.saved_questions,
        "list_for",
        lambda user_id: observed.append(user_id) or [],
    )
    app = FastAPI()
    app.include_router(saved_questions_api.router)
    app.dependency_overrides[saved_questions_api.get_current_user] = lambda: User(id=42)

    response = TestClient(app).get("/api/saved-questions")

    assert response.status_code == 200
    assert response.json() == {"questions": []}
    assert observed == [42]


def test_api_does_not_leak_internal_exception_text(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import saved_questions_api
    from app.db.models import User

    monkeypatch.setattr(
        saved_questions_api.saved_questions,
        "add",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("database-password-secret")),
    )
    app = FastAPI()
    app.include_router(saved_questions_api.router)
    app.dependency_overrides[saved_questions_api.get_current_user] = lambda: User(id=42)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/saved-questions",
        json={"question": "sales", "cadence": "daily"},
    )

    assert response.status_code == 500
    assert "database-password-secret" not in response.text


@pytest.mark.asyncio
async def test_personal_briefing_job_runs_saved_questions_before_precompute(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app import main
    from app.core import personal_briefing, self_check

    events: list[str] = []

    class JobRun:
        def set_note(self, _note):
            return None

    @contextmanager
    def track(job_id):
        events.append("track:" + job_id)
        yield JobRun()

    async def run_saved(now=None):
        events.append("saved")
        return {"selected": 0, "succeeded": 0, "failed": 0, "empty": 0}

    async def run_briefing(now=None):
        events.append("briefing")
        return {"selected": 0, "succeeded": 0, "failed": 0, "queued": 0}

    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(personal_briefing_enabled=True))
    monkeypatch.setattr(self_check, "track_job", track)
    monkeypatch.setattr(saved_questions, "run_saved_questions", run_saved)
    monkeypatch.setattr(personal_briefing, "run_morning_precompute", run_briefing)

    await main._personal_briefing_job()

    assert "track:saved_questions_daily" in events
    assert events.index("saved") < events.index("briefing")


def test_saved_questions_job_is_monitored_by_self_check():
    from app.core.self_check import EXPECTED_JOBS

    assert "saved_questions_daily" in EXPECTED_JOBS


# ── 표로 답한 결과를 사람이 읽을 수 있게 ──────────────────────────────────────

def test_table_answers_collapse_to_lead_sentence_and_row_count():
    """⛔ 표를 300자로 자르면 파이프 문자 더미가 된다 — 실리지만 읽히지 않는다.

    2026-08-27 프로덕션 실측에서 실제로 이렇게 나갔다:
        `### 📦 재고 조회 결과 **'센텔라 앰플'** 으로 15개 품목을… | SKU | 품목명 | |---|---|…`
    반복 질문 상위가 재고·채널별 TOP5·검색 순위라 **대부분 표로 답한다**.
    쓸모 있는 것은 표 앞 문장 하나뿐이고 전체는 이어보기 링크가 맡는다.
    """
    from app.core.work_briefing import summarize_answer

    answer = (
        "### 📦 재고 조회 결과\n\n"
        "**'센텔라 앰플'** 으로 15개 품목을 찾았습니다.\n\n"
        "| SKU | 품목명 | 총 재고 |\n"
        "|---|---|---:|\n"
        "| KRSKA022 | 마다가스카르센텔라앰플100ml | 885,075 |\n"
        "| KRSKA010 | (미니)마다가스카르센텔라앰플30ml | 780,441 |\n"
    )
    out = summarize_answer(answer)

    assert "15개 품목을 찾았습니다" in out, "표 앞 문장이 사라졌다"
    assert "(표 2행)" in out, f"행수를 밝히지 않는다: {out!r}"
    assert "|" not in out, f"표 조각이 남았다: {out!r}"
    assert "###" not in out and "**" not in out, "평문 매체에 마크다운 장식이 남았다"


def test_sentence_answers_are_left_alone():
    """⚠️ 문장형 답변까지 건드리지 마라 — 매출·ROAS 답은 지금도 읽힌다."""
    from app.core.work_briefing import summarize_answer

    plain = "일본 8월 매출은 55.1억으로 전월 대비 12.4% 늘었습니다."
    assert summarize_answer(plain) == plain


def test_table_only_answer_states_the_fact_without_inventing_a_sentence():
    """⚠️ 앞 문장이 없으면 지어내지 않는다 — 사실만 적는다."""
    from app.core.work_briefing import summarize_answer

    assert summarize_answer("| A | B |\n|---|---|\n| 1 | 2 |\n") == "(표 1행)"
    assert summarize_answer("") == ""


def test_saved_rows_use_the_summarizer_not_a_raw_slice():
    """⛔ 화면과 잔디가 **같은 함수**를 쓴다 — 한쪽만 요약하면 매체마다 다른 말을 한다."""
    import inspect

    from app.core import work_briefing

    src = inspect.getsource(work_briefing._saved_rows)
    assert "summarize_answer(" in src
    assert '_clean(row.get("last_answer"' not in src


def test_markdown_headings_are_dropped_not_inlined():
    """⛔ 목차를 문장 사이에 끼워 넣지 마라 — 읽는 흐름이 끊긴다.

    2026-08-27 프로덕션 실측에서 이렇게 나왔다:
        `📊 쇼피 인도네시아 8월 매출 분석 #### 요약 2026년 8월 기준 … #### 상세 데이터 (표)`
    원인 둘: ① 줄을 합친 **뒤에** 장식을 지워 `^#` 이 안 맞았다 ② 제목의 `#` 만 떼고
    글자는 남겼다. 사람이 원하는 것은 **숫자가 든 문장**이다.
    """
    from app.core.work_briefing import summarize_answer

    answer = (
        "## 📊 쇼피 인도네시아 8월 매출 분석\n\n"
        "#### 요약\n"
        "2026년 8월 기준 총매출은 약 28.5억원입니다.\n\n"
        "#### 상세 데이터\n"
        "| 국가 | 매출 |\n|---|---:|\n| 인도네시아 | 2,850,000,000 |\n"
    )
    out = summarize_answer(answer)

    assert "28.5억원" in out, "숫자가 든 문장이 사라졌다"
    assert "#" not in out, f"마크다운 제목 기호가 남았다: {out!r}"
    assert "요약" not in out and "상세 데이터" not in out, \
        f"목차가 문장 사이에 끼었다: {out!r}"
    assert out.endswith("(표 1행)")


# ── 잔디에서 답변이 문장 중간에 끊기던 것 (2026-09-02 사용자 제보) ────────────
#
# 실제로 나간 줄:
#   > ⚠️ 최근 날짜는 아직 채워지는 중일 수 있습니다 — … 확정 수치가 필요하면 하루 뒤에
#   다시 확인해 주세요. 요청하신 조건에 대한 분석 결과입니다. 1. 데이터 조회 결과
#   선택하신 조건(…)에 해당하는 데이터가 존재하지 않습니다. 2. 원인 분석 * *
#
# 세 가지가 겹쳤다:
#   ① 표가 없는 답변은 `_clean(raw, limit)` 로 **손질 없이 통째로 하드 슬라이스**됐다
#   ② 코드가 붙인 공시(`> ⚠️ …`) 167자가 300자 예산을 먹어 답은 116자만 남았다
#   ③ 남은 자리마저 낱말 중간에서 **표시 없이** 끊겨 `* *` 부스러기로 끝났다

_ZERO_ROW_ANSWER = "\n".join([
    "> ⚠️ **최근 날짜는 아직 채워지는 중일 수 있습니다** — 이 답은 최근 2일 이내를 "
    "포함합니다. 매출 데이터는 매체·채널마다 적재 시각이 달라 마지막 1~2일치가 나중에 "
    "더 늘어날 수 있습니다 (실제로 하루 뒤 값이 8배가 된 사례가 있습니다). 확정 수치가 "
    "필요하면 하루 뒤에 다시 확인해 주세요.",
    "",
    "요청하신 조건에 대한 분석 결과입니다.",
    "",
    "### 1. 데이터 조회 결과",
    "선택하신 조건(국가: 인도네시아, 몰 구분: Shopee, 기간: 2026년 9월)에 해당하는 "
    "데이터가 존재하지 않습니다.",
    "",
    "### 2. 원인 분석",
    "*   **적재 시점**: 9월 매출은 아직 집계 전일 수 있습니다.",
    "*   **몰 구분 표기**: Shopee 표기가 다를 수 있습니다.",
])


def test_a_notice_block_does_not_eat_the_answers_budget():
    """⛔ 공시는 답이 아니다 — 담으면 정작 답이 밀려 나간다."""
    from app.core.work_briefing import summarize_answer

    out = summarize_answer(_ZERO_ROW_ANSWER, 300)

    assert "채워지는 중" not in out and ">" not in out, f"공시가 실렸다: {out!r}"
    assert "데이터가 존재하지 않습니다" in out, f"정작 답이 빠졌다: {out!r}"
    assert "적재 시점" in out, "공시가 예산을 먹어 원인 분석이 잘렸다"


def test_a_table_less_answer_is_cleaned_like_every_other():
    """⛔ 표가 없다고 손질을 건너뛰지 마라 — 0건 답변이 그 경로였다."""
    from app.core.work_briefing import summarize_answer

    out = summarize_answer(_ZERO_ROW_ANSWER, 300)

    assert "#" not in out and "**" not in out, f"마크다운이 남았다: {out!r}"
    assert "* " not in out and not out.rstrip().endswith("*"), \
        f"목록 표식 부스러기가 남았다: {out!r}"
    assert "1. 데이터 조회 결과" not in out, "목차가 문장 사이에 끼었다"


def test_truncation_is_visible_and_lands_on_a_boundary():
    """⛔ 표시 없이 끊으면 읽는 사람은 **그게 답의 전부인 줄 안다.**"""
    from app.core.work_briefing import summarize_answer

    long_answer = ("첫 문장입니다. " + "가나다라마바사아자차 " * 40).strip()
    out = summarize_answer(long_answer, 120)

    assert len(out) <= 120, f"상한을 넘었다: {len(out)}"
    assert out.endswith("…"), f"잘린 티가 없다: {out!r}"
    assert not out.rstrip("…").endswith(" "), "낱말 중간/공백에서 끊겼다"

    # ⚠️ 상한 안에 들면 아무것도 붙이지 않는다 — 안 자른 것을 자른 척하지 않는다.
    assert not summarize_answer("일본 8월 매출은 55.1억입니다.", 300).endswith("…")


def test_the_row_count_survives_truncation():
    """⚠️ 잘릴 때 `(표 N행)` 부터 날아가면 표가 있었다는 사실이 사라진다."""
    from app.core.work_briefing import summarize_answer

    answer = ("가나다라마바사아자차 " * 60).strip() + "\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = summarize_answer(answer, 150)

    assert out.endswith("(표 1행)"), f"행수가 잘려 나갔다: {out!r}"
    assert "…" in out, "앞 문장이 잘렸는데 표시가 없다"
    assert len(out) <= 150
