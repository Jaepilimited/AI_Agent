# Cluster 09

> Auto-generated 2026-08-19T03:00:36.499205+09:00 · Files: 1

## Purpose
이 클러스터는 SKIN1004 AI Agent 시스템 내에서 얼굴 이미지를 분석하여 고유한 벡터 표현으로 변환하는 얼굴 임베딩(Face Embedding) 기능을 담당합니다. 외부 무거운 라이브러리에 의존하지 않고, 경량화된 ONNX 런타임을 통해 직접 추론을 수행하여 서버 환경의 제약을 극복하고 일관된 성능을 보장합니다.

## Key Files
- `C:/Users/DB_PC/Desktop/python_bcj/AI_Agent/app/core/face_embed.py` — `insightface` 패키지 설치 없이 `buffalo_l` ONNX 모델을 직접 로드하여 얼굴 임베딩을 추출하는 핵심 모듈입니다.

## Key Concepts
- **buffalo_l ONNX 직접 추론**: 일반적으로 Python 환경에서 얼굴 분석을 위해 `insightface` 라이브러리를 사용하지만, 이는 C++ 컴파일러 등 복잡한 빌드 도구를 요구합니다. 본 프로젝트의 WAS 환경(Python 3.12) 및 서버 제약을 해결하기 위해, 동일한 `buffalo_l` 모델 파일(.onnx)을 `onnxruntime`으로 직접 로드하여 추론을 수행합니다.
- **얼굴 임베딩 (Face Embedding)**: 입력된 얼굴 이미지로부터 개인의 고유한 특징을 나타내는 고차원 수치 벡터를 추출하는 과정입니다. 이 벡터는 향후 사용자 식별이나 유사도 비교 등에 활용됩니다.

## How It Fits In
이 클러스터는 외부 의존성을 최소화한 독립적인 코어 유틸리티 역할을 합니다. 별도의 복잡한 크로스 클러스터 의존성 없이, 이미지 데이터를 입력받아 임베딩 벡터를 반환하는 단방향 파이프라인을 제공합니다. 추출된 임베딩 데이터는 추후 데이터베이스 저장 및 사용자 매칭 비즈니스 로직에서 활용될 수 있습니다.

## Common Questions This Page Answers
- **왜 `insightface` 패키지를 직접 설치하여 사용하지 않나요?**
  - 현재 WAS 서버 환경은 Python 3.12를 사용하고 있으며, `insightface 0.7.3` 설치 시 C++ 컴파일을 위한 빌드 도구가 필요합니다. 서버 환경에 이러한 빌드 도구가 없으므로, 동일한 `buffalo_l` ONNX 모델을 직접 로드하여 추론하는 방식으로 우회 구현하였습니다.
- **임베딩 추출에 사용되는 모델은 무엇인가요?**
  - `insightface`에서 표준으로 사용하는 `buffalo_l` 모델과 동일한 ONNX 모델 파일을 사용하므로, 기존 모델과 완전히 동일한 품질의 임베딩 결과를 얻을 수 있습니다.