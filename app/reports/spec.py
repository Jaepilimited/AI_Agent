# -*- coding: utf-8 -*-
"""보고서 스펙 — 무엇을 조회하고, 무엇을 점검하고, 무엇을 계산할지 선언한다.

설계 원칙 (2026-08-12, FOC 보고서를 일반화하며 확정):

1. **숫자는 코드가, 문장은 템플릿이 만든다.** LLM 이 숫자를 쓰는 경로를 아예 두지 않는다.
   기존 규칙("성분 SQL 을 LLM 에 맡기지 마라")의 연장이다.
2. **파생 지표는 SQL 이 아니라 파이썬에서** 계산한다. 나눗셈·비율을 LLM 이나 생성 SQL 에
   맡기면 검산할 수 없다.
3. **품질 게이트가 서술보다 먼저 돈다.** 쓰레기 컬럼을 포함한 채 그럴듯한 비율을 내는 것이
   가장 위험한 실패다 — 조용하기 때문이다. 제외했으면 제외 사실과 그 영향을 payload 에 남긴다.
4. **모든 fact 는 기대값(`expect`)을 달고 다닌다.** 골든셋이 답변에 하는 일을 보고서 수치에 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

Rows = List[Dict[str, Any]]


@dataclass
class Fact:
    """보고서가 필요로 하는 조회 하나.

    sql 은 `{param}` 형식으로 ReportSpec.params 를 참조할 수 있다.
    """

    id: str
    sql: str
    expect: str = ""          # 재실행 시 사람이 대조할 기대값 서술. 비워두지 말 것
    note: str = ""            # 왜 이 조회가 필요한가
    scalar: bool = False      # 한 행짜리 결과를 dict 로 펼칠지


@dataclass
class Gate:
    """데이터 품질 게이트.

    verdict 는 (통과?, 사람이 읽을 설명) 을 돌려준다. 실패는 예외가 아니다 —
    보고서를 멈추는 대신 `impact` 를 payload 에 남겨 본문에서 공시한다.
    """

    id: str
    label: str
    fact: str
    verdict: Callable[[Rows], Tuple[bool, str]]
    impact: str = ""          # 실패 시 보고서에 실릴 문구 (제외 사실·영향)
    blocking: bool = False    # True 면 통과 못 할 때 보고서 생성을 중단한다


@dataclass
class ReportSpec:
    id: str
    title: str
    params: Dict[str, Any]
    facts: List[Fact]
    gates: List[Gate] = field(default_factory=list)
    derive: Optional[Callable[[Dict[str, Rows], Dict[str, Any]], Dict[str, Any]]] = None
    template: str = ""
    # 본문에 남겨도 되는 숫자 리터럴 (연도 표기 등). 그 외 숫자는 린터가 잡는다.
    allow_literals: List[str] = field(default_factory=list)

    def fact(self, fact_id: str) -> Fact:
        for f in self.facts:
            if f.id == fact_id:
                return f
        raise KeyError(f"알 수 없는 fact: {fact_id}")
