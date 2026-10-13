# Cluster 30

> Auto-generated 2026-10-13T03:01:36.793771+09:00 · Files: 1

## Purpose
이 클러스터는 SKIN1004 AI Agent 프론트엔드에서 인터랙티브하게 시각화할 수 있는 차트 설정을 생성하는 역할을 담당합니다. 서버에서 정적 PNG 이미지를 렌더링하는 대신, 프론트엔드가 직접 렌더링할 수 있는 Chart.js configuration JSON 객체를 빌드하여 응답 속도와 상호작용성을 개선합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/chart.py` — Chart.js 설정 JSON을 구성하고 반환하는 차트 생성 모듈

## Key Concepts
- **Chart.js Configuration JSON** — 서버 측 렌더링 대신 클라이언트에서 동적으로 렌더링할 수 있도록 차트 타입, 데이터셋, 옵션 등을 정의한 JSON 객체
- **Client-Side Interactive Rendering** — 서버 부하 및 정적 이미지 생성 지연(slow, static PNG)을 방지하고 프론트엔드에서 인터랙티브한 시각화를 지원하는 방식

## How It Fits In
이 클러스터는 분석 결과 및 지표를 시각화해야 하는 시나리오에서 프론트엔드가 차트를 그릴 수 있도록 데이터 규격을 제공합니다. 현재 감지된 직접적인 크로스 클러스터 관계는 없으나, 독립적인 핵심 차트 생성 유틸리티로서 프론트엔드 시각화 인터페이스를 지원합니다.

## Common Questions This Page Answers
- 차트 이미지를 서버에서 PNG로 직접 렌더링하지 않고 Chart.js config JSON 형태로 전달하는 이유는 무엇인가요?
- Chart.js 설정 객체를 생성하는 핵심 모듈 파일 경로는 어디인가요?