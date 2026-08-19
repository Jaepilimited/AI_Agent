# Cluster 18

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 1

## Purpose
본 클러스터는 SKIN1004 AI Agent 프로젝트에서 웹 라우팅의 신뢰성을 극대화하기 위한 Grounded Web Routing 구현 계획을 다룹니다. LLM의 환각(Hallucination) 현상을 방지하고, 실제 웹 페이지 구조와 데이터에 기반하여 정확한 경로로 사용자를 안내하거나 작업을 수행하는 설계 방향을 제시합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/docs/superpowers/plans/2026-07-16-grounded-web-routing.md` — 실제 웹 환경의 데이터와 구조를 기반으로 신뢰할 수 있는 웹 라우팅을 수행하기 위한 상세 구현 계획서입니다.

## Key Concepts
- **Grounded Web Routing (근거 기반 웹 라우팅)** — LLM이 임의로 웹 URL이나 경로를 생성하는 대신, 실제 SKIN1004 쇼핑몰 사이트의 사이트맵, DOM 구조, 또는 API 응답 등 검증된 데이터(Grounding Source)를 바탕으로 정확한 웹 페이지 경로를 탐색하고 이동하는 기술입니다.
- **Hallucination Mitigation (환각 완화)** — 존재하지 않는 메가와리(Megawari) 이벤트 페이지나 잘못된 상품 상세 페이지 URL을 AI Agent가 생성하지 않도록 제어하는 메커니즘입니다.
- **Superpowers Plans** — AI Agent의 핵심 기능(Superpowers)을 확장하기 위해 작성된 아키텍처 및 실행 로드맵입니다.

## How It Fits In
본 클러스터는 독립적인 구현 계획 문서로 구성되어 있으며, AI Agent가 SKIN1004 공식몰 및 메가와리 행사 페이지 등 실제 웹 환경과 상호작용할 때 필요한 라우팅 안정성 기준을 정의합니다. 추후 웹 크롤러, 브라우저 자동화 도구(Playwright 등), 그리고 LLM 도구 호출(Tool Calling) 레이어가 구현될 때 이 계획서의 설계 원칙이 직접적으로 반영됩니다.

## Common Questions This Page Answers
- AI Agent가 SKIN1004 웹사이트 내에서 잘못된 URL로 이동하는 환각 현상을 어떻게 방지하나요?
- Grounded Web Routing을 구현하기 위해 어떤 데이터 소스를 기반으로 경로 검증을 수행하나요?