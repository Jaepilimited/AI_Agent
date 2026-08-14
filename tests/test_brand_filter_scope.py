# -*- coding: utf-8 -*-
"""묻지 않은 브랜드 필터가 매출을 깎지 않는지 지킨다.

사고 원문 (2026-08-06 제보 → 2026-08-14 확인): "26년 7월 미국·인도네시아·
말레이시아·호주·멕시코·캐나다 매출" 질문에 LLM 이 `Brand IN ('SK','CL','CBT')`
를 스스로 붙여 **우마(UM)를 통째로 뺐다.**

    멕시코   8.15억 답변 /   9.32억 실제  (UM  1.17억 누락)
    미국    86.55억 답변 / 219.34억 실제  (UM 132.79억 누락 · 61%)
    캐나다   6.56억 답변 /  17.83억 실제  (UM 11.27억 누락 · 63%)

답변은 조회 조건에 "대상 브랜드: SK, CL, CBT"라고 적었지만, 국가별 매출을 물은
사람이 그 줄을 브랜드 한정으로 읽을 이유가 없다 — **틀린 티가 안 나는 실패다.**

원인은 프롬프트가 자기 자신과 모순이었던 것이다. 그래서 이 테스트는 두 가지를
함께 본다: ① 후처리가 실제로 걷어내는가 ② 프롬프트에 모순이 다시 생기지 않는가.
"""
from pathlib import Path

import pytest

from app.agents.sql_agent import _strip_unrequested_brand_filter as strip

_SQL = ("SELECT Country, SUM(Sales1_R) AS s "
        "FROM `p.d.SALES_ALL_Backup` "
        "WHERE Date >= '2026-07-01' AND Brand IN ('SK', 'CL', 'CBT') "
        "AND Country IN ('미국','멕시코') GROUP BY Country")


class TestStripsWhenNotAsked:
    @pytest.mark.parametrize("question", [
        "26년 7월 미국 , 인도네시아, 말레이시아, 호주,멕시코,캐나다에서 발생한 매출 알려줘.",  # 원문
        "7월 국가별 매출 알려줘",
        "일본 팀별 매출 알려줘",
        "2026년 월별 총매출 보여줘",
        "인도네시아 B2C 매출",
    ])
    def test_removed(self, question):
        out = strip(_SQL, question)
        assert "Brand IN" not in out, f"우마가 빠진 채로 남았다: {out}"
        # 다른 조건은 건드리지 않는다
        assert "Country IN" in out and "Date >=" in out


class TestKeepsWhenAsked:
    """⚠️ 제품·브랜드 질문에서는 이 필터가 **맞다** (UM·CBT 는 제품명이 비어 있다)."""

    @pytest.mark.parametrize("question", [
        "7월 제품별 매출 top10",
        "브랜드별 매출 비교해줘",
        "인기 라인 알려줘",
        "스킨천사 7월 매출",
        "센텔라 앰플 매출 얼마야",
        "카테고리별 매출 추이",
        "우마 매출 알려줘",
    ])
    def test_kept(self, question):
        assert "Brand IN" in strip(_SQL, question)


class TestNoOp:
    def test_untouched_without_filter(self):
        sql = "SELECT SUM(Sales1_R) FROM t WHERE Country = '멕시코'"
        assert strip(sql, "7월 멕시코 매출") == sql

    def test_full_brand_list_untouched(self):
        """전 브랜드를 명시한 필터는 축소가 아니므로 그대로 둔다."""
        sql = "SELECT 1 FROM t WHERE Brand IN ('SK','CL','CBT','UM')"
        assert strip(sql, "7월 국가별 매출") == sql


class TestPromptHasNoContradiction:
    """⛔ 프롬프트가 자기 자신과 모순이면 후처리가 있어도 다른 곳에서 샌다."""

    def test_no_bare_sk_cl_filter(self):
        text = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")
        # 'SK','CL' 만 남기는 지시는 CBT(=스킨천사)까지 빠뜨린다
        assert "Brand IN ('SK', 'CL')" not in text
        assert "Brand IN ('SK','CL')" not in text

    def test_country_rule_still_stated(self):
        text = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")
        assert "국가별 매출" in text and "Brand 필터 없이 전체 포함" in text
