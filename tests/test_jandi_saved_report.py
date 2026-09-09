# -*- coding: utf-8 -*-
"""잔디로 가는 '내가 저장한 보고'는 **전문**이다 — 2026-09-07.

⛔ **사용자 제보**: *"잔디에서 내가 저장한 보고가 전체를 보고싶은데 잘려서 나와"*

실측: 전문 **1,091자 → 요약 123자**. `summarize_answer(…, 300)` 이 앞 문장만 남기고
**표를 통째로 버렸다.** 그 규칙 자체는 옳았다 — *잘린* 마크다운 표는 파이프 더미라
읽히지 않는다 (2026-08-27). 하지만 그건 **자를 때** 이야기고, 전문을 싣지 말라는
뜻은 아니었다.

⟹ 화면 카드는 요약(훑어보는 자리), 잔디는 전문(읽는 자리)으로 갈랐다.
"""
import pytest

ANSWER = """### 📊 쇼피 인도네시아 9월 매출 현황

#### 요약
2026년 9월 총매출은 **약 7.5억원**입니다.

#### 상세 데이터 (표)
| 구분 | 매출 금액 (원) |
| :--- | ---: |
| 쇼피 인도네시아 (Shopee) | 750,296,972 |
| 라자다 인도네시아 | 120,000,000 |

#### 분석 및 인사이트
* **진행 월 매출 규모**: 약 7.5억원.

---
*조회 기준: 2026-09-07 | 내부 데이터베이스*

> 💡 **이런 것도 물어보세요**
> - [지난달 최종 매출은?]
> - [다른 채널도 보여줘]
<details><summary>실행된 쿼리</summary>

```sql
SELECT SUM(Sales1_R) FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup`
```
</details>

> ⚠️ 이 조회 기간은 오늘 이후를 포함합니다."""


def _full():
    from app.core.work_briefing import full_answer_for_jandi

    return full_answer_for_jandi(ANSWER)


def test_table_rows_survive_as_readable_lines():
    """⛔ 원래 불만이 이것이다 — 표가 통째로 사라졌다."""
    out = _full()
    assert "750,296,972" in out and "120,000,000" in out
    assert "쇼피 인도네시아 (Shopee)" in out
    # 파이프 더미로 싣지 않는다
    assert "| :---" not in out and "| 구분 |" not in out


def test_internal_table_path_never_reaches_jandi():
    """⛔ `<details>실행된 쿼리</details>` 는 앱 화면에서만 접힌 근거다.
    잔디에서는 평문으로 펼쳐져 **내부 경로가 채널에 노출**된다."""
    out = _full()
    assert "skin1004-319714" not in out
    assert "SELECT" not in out
    assert "Sales_Integration" not in out


def test_followup_chips_are_dropped():
    """앱에서는 누르는 칩이고 잔디에서는 그냥 글자다."""
    out = _full()
    assert "이런 것도 물어보세요" not in out
    assert "지난달 최종 매출은?" not in out


def test_disclosures_are_kept():
    """⚠️ 미래 기간 공시 같은 경고는 남아야 한다 — 그게 답의 신뢰도다."""
    assert "오늘 이후를 포함" in _full()


def test_full_is_longer_than_the_card_summary():
    from app.core.work_briefing import summarize_answer

    assert len(_full()) > len(summarize_answer(ANSWER, 300))


def test_long_answer_says_it_was_trimmed():
    """⚠️ 조용히 자르는 것이 원래 불만이었다 — 자를 땐 밝힌다."""
    from app.core.work_briefing import full_answer_for_jandi

    out = full_answer_for_jandi("### 제목\n" + ("가나다라마바사 " * 800), limit=300)
    assert "앞부분만 실었습니다" in out


def test_card_keeps_the_short_summary():
    """⛔ 화면 카드까지 전문으로 바꾸면 첫 화면이 스크롤 지옥이 된다."""
    from app.core.work_briefing import _saved_rows

    rows = _saved_rows([{"question": "쇼피 매출", "last_answer": ANSWER,
                         "last_run_at": None, "link": ""}])
    assert rows and len(rows[0]["answer"]) <= 320
    assert len(rows[0]["answer_full"]) > len(rows[0]["answer"])


def test_renderer_prefers_the_full_text():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "app" / "core" / "work_briefing.py").read_text(encoding="utf-8")
    assert 'row.get("answer_full") or row.get("answer")' in src


def test_jandi_body_truncation_is_disclosed():
    """⛔ `body[:9000]` 로 조용히 자르면 '왜 일부만 오지' 가 된다."""
    from app.core.jandi_briefing import jandi_payload

    payload = jandi_payload("가" * 12000)
    assert "여기까지만 보냅니다" in payload["body"]
    assert len(payload["body"]) <= 9000


def test_short_body_is_untouched():
    from app.core.jandi_briefing import jandi_payload

    assert jandi_payload("짧은 본문")["body"] == "짧은 본문"


def test_regex_uses_phrases_not_emoji_escapes():
    """⛔ raw 문자열 안의 `\\uXXXX` 는 이모지가 아니라 리터럴 백슬래시다 —
    실제로 그렇게 써서 후속 제안이 안 지워졌다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "app" / "core" / "work_briefing.py").read_text(encoding="utf-8")
    body = src[src.index("def full_answer_for_jandi"):src.index("def summarize_answer")]
    assert "\\ud83d" not in body


def test_chart_config_json_is_dropped():
    """⛔ 잔디는 차트를 그리지 않는다 — 설정 JSON 을 실으면 수백 자 글자 더미다
    (실측: 전사 매출 답변에서 chart-config 가 통째로 실렸다)."""
    from app.core.work_briefing import full_answer_for_jandi

    text = ("#### 상세\n내용\n\n#### 시각화\n\n```chart-config\n"
            '{"type":"bar","data":{"labels":["영업1팀"],"datasets":[{"data":[1]}]}}\n'
            "```\n\n#### 끝\n마무리")
    out = full_answer_for_jandi(text)
    assert "chart-config" not in out and "datasets" not in out
    assert "시각화" not in out, "차트를 뺐으면 그 제목도 홀로 남기지 않는다"
    assert "마무리" in out


def test_emphasis_inside_table_cells_is_stripped():
    """⚠️ 합계 행이 `**합계**` 로 나온다 — 셀 안의 강조기호도 걷는다."""
    from app.core.work_briefing import full_answer_for_jandi

    text = ("| 팀명 | 매출액 |\n| :--- | ---: |\n"
            "| 영업1팀 | 100 |\n| **합계** | **59,965,661,014** |")
    out = full_answer_for_jandi(text)
    assert "**" not in out
    assert "합계" in out and "59,965,661,014" in out


# ── 글자 막대 (2026-09-07) ────────────────────────────────────────────────────
# 사용자: *"차트 같은경우는 캡쳐해서 보내주면 안되냥?"*
#
# ⛔ **그림으로는 못 보낸다.** 잔디는 이미지를 URL 로만 받고(`connectInfo[].imageUrl`)
#    그 URL 이 잔디 쪽에서 열려야 하는데 우리 앱은 내부 전용이다
#    (`public_base_url = http://ai.cravercorp.internal`). 공개 자리에 올리면
#    매출 차트가 링크 하나로 새어 나간다 — 보고서 공유를 "링크 아는 사람 다 열람"
#    으로 만들지 않은 그 이유다. 프로덕션 WAS 에는 렌더 수단도 없다
#    (실측 2026-09-07: playwright ✗ · matplotlib ✗).
# ⟹ 그래서 **글자로 그린다.** 표를 줄로 펴는 것과 같은 계열이다.

def _bars(text):
    from app.core.work_briefing import full_answer_for_jandi

    return [ln for ln in full_answer_for_jandi(text).splitlines() if "█" in ln]


TEAM_TABLE = (
    "| 팀명 | 매출액 |\n| :--- | ---: |\n"
    "| 영업1팀 | 21,242,671,658 |\n"
    "| 유통1팀 | 14,000,000,000 |\n"
    "| 일본사업팀 | 2,591,877,419 |\n"
    "| **합계** | **37,834,549,077** |"
)


def test_bars_are_drawn_and_scaled_to_the_largest_row():
    lines = _bars(TEAM_TABLE)
    assert len(lines) == 3, "합계를 뺀 본문 3행에만 붙는다"
    widths = [ln.count("█") for ln in lines]
    assert widths == sorted(widths, reverse=True), "큰 값이 긴 막대여야 한다"
    assert widths[0] > widths[-1]


def test_the_total_row_gets_no_bar():
    """⛔ 합계가 최댓값이 되면 나머지가 전부 한 칸이 되어 그림이 아무 말도 못 한다."""
    from app.core.work_briefing import full_answer_for_jandi

    total = [ln for ln in full_answer_for_jandi(TEAM_TABLE).splitlines() if "합계" in ln]
    assert total and all("█" not in ln for ln in total)


def test_the_bar_starts_the_line_so_lengths_can_be_compared():
    """⚠️ 잔디 본문은 **고정폭 글꼴이 아니다** — 라벨을 앞에 두면 라벨 길이만큼
    막대 시작점이 어긋나 길이를 견줄 수 없다."""
    for line in _bars(TEAM_TABLE):
        assert line.lstrip().startswith("█")
        assert line.index("█") == len(line) - len(line.lstrip())


def test_units_are_converted_before_scaling():
    """⛔ **가장 조용한 오답**: `212.4억` 과 `4,860만` 을 적힌 그대로 세면
    만 쪽이 더 긴 막대가 된다. 표의 숫자는 맞는데 그림만 뒤집힌다."""
    text = ("| 팀 | 매출 |\n| :--- | ---: |\n"
            "| 큰팀 | 212.4억 |\n| 중간팀 | 50.0억 |\n| 작은팀 | 4,860만 |")
    widths = [ln.count("█") for ln in _bars(text)]
    assert widths[0] > widths[1] > widths[2]


def test_mixed_currency_tables_get_no_bars():
    """⛔ USD 와 KRW 를 한 축에 세우면 거짓말이다 (실측: USD+KRW+JPY 를 더해
    '266.8억원' 이 나간 사고와 같은 계열)."""
    text = ("| 국가 | 통화 | 금액 |\n| :--- | :--- | ---: |\n"
            "| 미국 | USD | 90,992 |\n| 한국 | KRW | 17,449,032 |\n"
            "| 일본 | JPY | 571,831 |")
    assert _bars(text) == []


def test_negative_values_get_no_bars():
    """⛔ 막대 길이로는 방향을 말할 수 없다."""
    text = ("| 국가 | 증감액 |\n| :--- | ---: |\n"
            "| 영국 | 1,080,000 |\n| 미국 | -1,290,000 |\n| 호주 | 220,000 |")
    assert _bars(text) == []


def test_percent_columns_are_never_the_bar():
    """⛔ 비중과 증감률이 표에서 같은 모양이라, 섞이면 음수·100% 초과가 함께 온다."""
    text = ("| 팀 | 비중 |\n| :--- | ---: |\n"
            "| 가팀 | 50.0% |\n| 나팀 | 30.0% |\n| 다팀 | 20.0% |")
    assert _bars(text) == []


def test_percent_column_is_skipped_but_the_amount_column_still_draws():
    text = ("| 팀 | 비중 | 매출액 |\n| :--- | ---: | ---: |\n"
            "| 가팀 | 50.0% | 5,000 |\n| 나팀 | 30.0% | 3,000 |\n| 다팀 | 20.0% | 2,000 |")
    widths = [ln.count("█") for ln in _bars(text)]
    assert widths == sorted(widths, reverse=True) and widths[0] > widths[-1]


def test_two_row_tables_do_get_bars():
    """⚠️ 2026-09-07 사용자 결정으로 하한을 3행 → **2행**으로 낮췄다.
    쇼피 vs 라자다처럼 둘만 견주는 표가 실제로 흔하다."""
    text = "| 채널 | 매출 |\n| :--- | ---: |\n| 쇼피 | 750,296,972 |\n| 라자다 | 120,000,000 |"
    widths = [ln.count("\u2588") for ln in _bars(text)]
    assert len(widths) == 2 and widths[0] > widths[1]


def test_one_row_tables_get_no_bars():
    """⛔ 견줄 대상이 없는데 꽉 찬 막대 하나를 그리면 '제일 크다' 로 읽힌다 —
    그림이 없는 것보다 나쁘다. 실제로 저장 보고 한 건이 이 모양이었다
    (쇼피 인도네시아 단일 행 · 앱에서도 차트가 없었다)."""
    text = "| 채널 | 매출 |\n| :--- | ---: |\n| 쇼피 인도네시아 (Shopee) | 750,296,972 |"
    assert _bars(text) == []


def test_a_non_numeric_cell_disables_the_column():
    """⛔ 빠진 칸이 0으로 보이면 안 된다."""
    text = ("| 국가 | 입고일 | 수량 |\n| :--- | :--- | ---: |\n"
            "| 미국 | 2026-09-01 | 100 |\n| 호주 | 미정 | 50 |\n| 일본 | 2026-09-03 | 20 |")
    widths = [ln.count("█") for ln in _bars(text)]
    assert widths == [20, 10, 4], "날짜·'미정' 열은 건너뛰고 수량으로 그린다"


def test_tiny_values_keep_at_least_one_block():
    """⚠️ 작아서 안 보이는 것과 값이 없는 것은 다르다."""
    text = ("| 팀 | 매출 |\n| :--- | ---: |\n"
            "| 큰팀 | 1,000,000 |\n| 중간팀 | 500,000 |\n| 작은팀 | 1 |")
    assert _bars(text)[-1].count("█") == 1


def test_every_bar_fills_the_same_track_width():
    """⛔ **잔디 실측(2026-09-07)에서 왼쪽 끝이 어긋났다.** 찬 칸만 그리고 줄 맨 앞에
    세우면 시작점이 맞을 줄 알았는데, 잔디는 앞쪽 공백을 그대로 그리지 않는다.

    빈 칸까지 **폭이 같은 블록 문자**로 채우면 글꼴·정렬과 무관하게 경계가 같은
    자리에서 비교되고, 라벨도 모두 같은 x 에서 시작한다.
    """
    from app.core.work_briefing import _BAR_WIDTH

    tracks = [ln.strip().split(" ")[0] for ln in _bars(TEAM_TABLE)]
    assert {len(t) for t in tracks} == {_BAR_WIDTH}, "모든 막대가 같은 폭을 채워야 한다"
    assert all(t.count("█") + t.count("░") == _BAR_WIDTH for t in tracks)
    # 찬 칸은 값 순서를 지킨다
    filled = [t.count("█") for t in tracks]
    assert filled == sorted(filled, reverse=True)


def test_labels_all_start_at_the_same_column():
    """트랙 폭이 같으니 라벨 시작 위치도 같아야 한다 — 그게 트랙을 쓰는 이유다.

    ⛔ 이 회귀가 진짜 결함을 잡았다: 본문을 `.strip()` 으로 마무리해서 **첫 줄의
       들여쓰기가 통째로 걷혔다.** 표가 답변 맨 앞에 오면 첫 막대만 왼쪽으로
       튀어나간다 — 화면에서는 "정렬이 안 맞네" 로만 보이는 조용한 실패다.
    """
    offsets = {ln.index(" ", ln.index("█")) for ln in _bars(TEAM_TABLE)}
    assert len(offsets) == 1, f"라벨 시작이 어긋난다: {offsets}"
