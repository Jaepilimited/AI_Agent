"""Shared prompt fragments used across multiple agents."""

LANGUAGE_DETECTION_RULE = """## ⚠️ 답변 언어 규칙 (최우선)
사용자의 질문 언어를 감지하여 **반드시 같은 언어로** 답변하세요.
- 한국어 질문 → 한국어 답변
- English question → English answer
- Pregunta en español → Respuesta en español
- 日本語の質問 → 日本語で回答
- 기타 언어도 동일하게 해당 언어로 답변
아래 규칙에서 "한국어"라고 된 부분은 감지된 질문 언어로 대체 적용하세요."""
