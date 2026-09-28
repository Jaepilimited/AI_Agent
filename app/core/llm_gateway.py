"""사내 LLM 게이트웨이(llmgw) 전송층 — Anthropic Messages 모양을 OpenAI 호환으로 바꿔 보낸다.

왜 이 모양인가 (2026-09-23, 노션 `llm-chatbot-integration` 가이드):
    보안 검토에서 "Claude 를 직접 호출해 프롬프트 감사·가드레일이 없다" 는 지적이 있었고,
    IT 가 그 답으로 게이트웨이(`https://llmgw.cravercorp.com/v1/chat/completions`)를 세웠다.
    게이트웨이를 거치면 가드레일·Langfuse 감사 로깅·키 단위 사용량 집계가 **코드 수정 없이**
    붙는다. 대신 요청 포맷이 OpenAI 호환이고 인증은 가상 키다.

설계 — **ClaudeClient 는 그대로 두고 `self.client` 만 바꿔 끼운다.**
    `ClaudeClient` 는 `self.client.messages.create(**kw)` 와
    `self.client.messages.stream(**kw)` 두 가지만 쓴다 (Anthropic SDK 모양). 이 모듈의
    `GatewayTransport` 가 정확히 그 두 메서드를 제공하고, 안에서 Anthropic 포맷 → OpenAI
    포맷 → 다시 Anthropic 모양의 응답 객체로 되돌린다. 그래서 `_first_text()`·`_tune()`·
    재시도·토큰 계측·Flash 폴백·회귀 테스트가 **한 줄도 안 바뀌고** 그대로 듣는다.
    (회귀 `tests/test_llm_overload_fallback.py` 가 이미 같은 모양의 가짜 client 를 꽂는다 —
    이 어댑터의 인터페이스는 그 테스트가 보증한다)

⛔ **켜는 조건은 설정 둘이 다 있을 때뿐이다** (`llm_gateway_url` + `llm_gateway_key`).
   하나라도 비면 예전처럼 Anthropic 직접 호출이다. `.env` 는 배포에서 제외되므로
   WAS 에서 직접 넣고 재기동해야 켜진다 (모델명이 `.env` 때문에 안 바뀌던 그 함정).

⛔ **2026-09-23 실측: 노션에 적힌 키는 아직 모델 접근 권한이 없다.**
   8가지 요청 전부 `403 team not allowed to access model … ['no-default-models']`.
   운영자가 키에 모델 접근(`claude-opus-5` 등)과 `default_tools` 그룹을 주기 전에는
   프로덕션에 키를 넣지 마라 — 넣는 순간 채팅 전체가 403 으로 죽는다. 403 은
   재시도 대상이 아니라 즉시 올라온다 (조용히 Flash 로 새지 않는다 — 그러면 "게이트웨이를
   탄다" 는 보안 전제가 소리 없이 무너진다). 자가 점검 `llm_gateway` 가 매일 확인한다.

⚠️ 게이트웨이가 무엇을 받는지는 키가 막혀 **실측하지 못했다.** 그래서 400 이 나면
   파라미터를 한 단계씩 내려 다시 보낸다 (`_DEGRADE_LADDER`) — `reasoning_effort` 를 빼고,
   그래도 안 되면 시스템 프롬프트를 캐시 블록 목록에서 평문으로 편다. 내린 단계는
   기억해 두어 매 요청마다 400 을 두 번 맞지 않는다. 내릴 때마다 WARNING 을 남긴다
   (INFO 는 프로덕션에서 통째로 버려진다).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

CHAT_PATH = "/v1/chat/completions"

#: `server_url` 은 리터럴이다 — 실제 주소·서명은 게이트웨이가 갖는다.
#: ⛔ 2026-09-28 실측 (키가 열린 날): 가이드에 적힌 `llm_proxy` 로 보내면 **200 이 오지만 도구가
#:    붙지 않는다** — 모델이 "검색 도구가 없다" 고 답한다 (에러가 아니라 조용한 실패).
#:    LiteLLM 의 예약어는 `litellm_proxy` 이고, `require_approval: "never"` 가 없으면 도구 호출이
#:    승인 대기(`finish_reason=tool_calls`)에서 멈춰 검색 결과 없이 끝난다. 둘 다 있어야
#:    실제 검색 결과(출처 URL 포함)가 온다. 게이트웨이의 `/v1/mcp/tools` 에 도구가
#:    `agentcore_websearch-websearch___WebSearch` 로 등록돼 있는 것도 같은 날 확인했다.
WEBSEARCH_TOOLS: List[Dict[str, str]] = [
    {"type": "mcp", "server_url": "litellm_proxy", "server_label": "agentcore_websearch",
     "require_approval": "never"},
]

#: Anthropic `output_config.effort` → OpenAI 호환 `reasoning_effort`.
#: xhigh·max 는 OpenAI 어휘에 없어 high 로 접는다.
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

#: 400 을 받았을 때 내려 볼 단계. 순서가 곧 우선순위다.
_DEGRADE_LADDER = ("reasoning_effort", "system_parts")


class GatewayError(RuntimeError):
    """게이트웨이 HTTP 오류. `status_code` 가 있어야 `llm._is_retryable` 이 분류한다."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"llm gateway HTTP {status_code}: {message}")
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 응답 객체 — Anthropic SDK 응답과 같은 속성 이름을 쓴다
# ---------------------------------------------------------------------------


@dataclass
class GatewayTextBlock:
    text: str
    type: str = "text"


@dataclass
class GatewayUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class GatewayMessage:
    content: List[GatewayTextBlock] = field(default_factory=list)
    usage: Optional[GatewayUsage] = None
    stop_reason: Optional[str] = None
    model: str = ""


def _usage_from(raw: Optional[Dict[str, Any]]) -> Optional[GatewayUsage]:
    if not raw:
        return None
    details = raw.get("prompt_tokens_details") or {}
    return GatewayUsage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cache_read_input_tokens=int(details.get("cached_tokens") or raw.get("cache_read_input_tokens") or 0),
        cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens") or 0),
    )


def _text_of(content: Any) -> str:
    """OpenAI 응답 `message.content` 는 문자열이거나 파트 목록이다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def response_from_openai(data: Dict[str, Any], model: str = "") -> GatewayMessage:
    """비스트리밍 응답 JSON → Anthropic 모양.

    ⚠️ 본문이 비면 `content` 를 **빈 목록**으로 둔다 — 그래야 `ClaudeClient._first_text()`
       가 `claude_no_text_block` WARNING 을 남긴다 (골든 run#88 의 "토큰은 썼는데 답이
       비었다" 를 잡는 자리). 빈 문자열 블록을 만들어 두면 그 경고가 조용히 죽는다.
    """
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    text = _text_of(message.get("content"))
    blocks = [GatewayTextBlock(text=text)] if text else []
    return GatewayMessage(
        content=blocks,
        usage=_usage_from(data.get("usage")),
        stop_reason=choice.get("finish_reason"),
        model=data.get("model") or model,
    )


# ---------------------------------------------------------------------------
# 요청 변환 — Anthropic Messages kwargs → OpenAI chat/completions payload
# ---------------------------------------------------------------------------


def _system_to_parts(system: Any) -> Any:
    """Anthropic `system`(문자열 또는 캐시 블록 목록) → OpenAI system 메시지 content."""
    if isinstance(system, str):
        return system
    parts = []
    for block in system or []:
        if not isinstance(block, dict):
            continue
        part: Dict[str, Any] = {"type": "text", "text": block.get("text", "")}
        if block.get("cache_control"):
            part["cache_control"] = block["cache_control"]
        parts.append(part)
    return parts


def _flatten_system(system: Any) -> str:
    if isinstance(system, str):
        return system
    return "\n".join(
        b.get("text", "") for b in (system or []) if isinstance(b, dict)
    )


def _content_to_openai(content: Any) -> Any:
    """Anthropic 콘텐츠 블록 → OpenAI 파트. 문자열은 그대로."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            parts.append({"type": "text", "text": str(block)})
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif kind == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts or ""


def build_payload(
    kwargs: Dict[str, Any],
    *,
    stream: bool = False,
    degraded: Optional[set] = None,
) -> Dict[str, Any]:
    """`ClaudeClient` 가 넘기는 kwargs(Anthropic 모양)를 게이트웨이 payload 로 바꾼다.

    받는 키: model · max_tokens · messages · system · temperature · thinking ·
    output_config · tools. 모르는 키는 버린다 (게이트웨이에 400 을 만들지 않으려고).
    """
    degraded = degraded or set()
    messages: List[Dict[str, Any]] = []
    system = kwargs.get("system")
    if system:
        content = _flatten_system(system) if "system_parts" in degraded else _system_to_parts(system)
        messages.append({"role": "system", "content": content})
    for msg in kwargs.get("messages") or []:
        messages.append({"role": msg.get("role", "user"), "content": _content_to_openai(msg.get("content", ""))})

    payload: Dict[str, Any] = {"model": kwargs["model"], "messages": messages}
    if kwargs.get("max_tokens"):
        payload["max_tokens"] = int(kwargs["max_tokens"])
    if "temperature" in kwargs:
        payload["temperature"] = kwargs["temperature"]

    effort = (kwargs.get("output_config") or {}).get("effort")
    if effort and "reasoning_effort" not in degraded:
        payload["reasoning_effort"] = _EFFORT_MAP.get(str(effort), "medium")

    if kwargs.get("tools"):
        payload["tools"] = kwargs["tools"]

    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


# ---------------------------------------------------------------------------
# 스트림 — Anthropic SDK 의 `with client.messages.stream(...) as s:` 모양
# ---------------------------------------------------------------------------


def iter_sse_json(lines: Iterator[str]) -> Iterator[Dict[str, Any]]:
    """`data: {...}` 줄만 JSON 으로 낸다. `[DONE]` 에서 멈춘다."""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            logger.warning("llm_gateway_sse_bad_json", head=data[:120])


class GatewayStream:
    def __init__(self, transport: "GatewayTransport", kwargs: Dict[str, Any]) -> None:
        self._t = transport
        self._kwargs = kwargs
        self._resp: Optional[httpx.Response] = None
        self._cm = None
        self._text_parts: List[str] = []
        self._usage: Optional[GatewayUsage] = None
        self._finish: Optional[str] = None
        self.text_stream: Iterator[str] = iter(())

    def __enter__(self) -> "GatewayStream":
        self._cm, self._resp = self._t._open_stream(self._kwargs)
        self.text_stream = self._iter_text()
        return self

    def __exit__(self, *_exc) -> bool:
        if self._cm is not None:
            self._cm.__exit__(*_exc)
        return False

    def _iter_text(self) -> Iterator[str]:
        assert self._resp is not None
        for chunk in iter_sse_json(self._resp.iter_lines()):
            if chunk.get("usage"):
                self._usage = _usage_from(chunk["usage"])
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    self._finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                text = _text_of(delta.get("content"))
                if text:
                    self._text_parts.append(text)
                    yield text

    def get_final_message(self) -> GatewayMessage:
        text = "".join(self._text_parts)
        return GatewayMessage(
            content=[GatewayTextBlock(text=text)] if text else [],
            usage=self._usage,
            stop_reason=self._finish,
            model=self._kwargs.get("model", ""),
        )


class _Messages:
    """`client.messages.create / .stream` — ClaudeClient 가 쓰는 두 메서드."""

    def __init__(self, transport: "GatewayTransport") -> None:
        self._t = transport

    def create(self, **kwargs: Any) -> GatewayMessage:
        return self._t._create(kwargs)

    def stream(self, **kwargs: Any) -> GatewayStream:
        return GatewayStream(self._t, kwargs)


class GatewayTransport:
    """Anthropic SDK `Anthropic()` 자리에 꽂는 객체. `.messages` 만 갖는다."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))
        self.degraded: set = set()  # 400 을 맞고 내려 둔 단계들 (프로세스 수명 동안 기억)
        self.messages = _Messages(self)

    # -- 오류 ---------------------------------------------------------------

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                return str(err.get("message") or err)[:300]
            return str(body)[:300]
        except Exception:
            return resp.text[:300]

    def _raise_for(self, resp: httpx.Response) -> None:
        msg = self._error_message(resp)
        if resp.status_code in (401, 403):
            # ⛔ 키 문제는 재시도로 안 풀린다. ERROR 로 크게 남긴다 — 이 상태면 채팅이 통째로 죽는다.
            logger.error("llm_gateway_denied", status=resp.status_code, message=msg)
        else:
            logger.warning("llm_gateway_http_error", status=resp.status_code, message=msg)
        raise GatewayError(resp.status_code, msg)

    def _next_degrade(self, resp: httpx.Response, payload: Dict[str, Any]) -> Optional[str]:
        """400 이면 아직 안 내린 단계 중 이 요청에 실제로 쓰인 것을 하나 고른다."""
        if resp.status_code != 400:
            return None
        for step in _DEGRADE_LADDER:
            if step in self.degraded:
                continue
            if step == "reasoning_effort" and "reasoning_effort" in payload:
                return step
            if step == "system_parts" and payload.get("messages") \
                    and payload["messages"][0].get("role") == "system" \
                    and isinstance(payload["messages"][0].get("content"), list):
                return step
        return None

    # -- 비스트리밍 -----------------------------------------------------------

    def _create(self, kwargs: Dict[str, Any]) -> GatewayMessage:
        while True:
            payload = build_payload(kwargs, degraded=self.degraded)
            t0 = time.time()
            resp = self._http.post(self.base_url + CHAT_PATH, headers=self._headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                msg = response_from_openai(data, kwargs.get("model", ""))
                logger.info("llm_gateway_response", model=msg.model, ms=int((time.time() - t0) * 1000),
                            finish=msg.stop_reason)
                return msg
            step = self._next_degrade(resp, payload)
            if step is None:
                self._raise_for(resp)
            self.degraded.add(step)
            logger.warning("llm_gateway_degraded", dropped=step, message=self._error_message(resp))

    # -- 스트리밍 -------------------------------------------------------------

    def _open_stream(self, kwargs: Dict[str, Any]):
        while True:
            payload = build_payload(kwargs, stream=True, degraded=self.degraded)
            cm = self._http.stream("POST", self.base_url + CHAT_PATH, headers=self._headers, json=payload)
            resp = cm.__enter__()
            if resp.status_code == 200:
                return cm, resp
            try:
                resp.read()
                step = self._next_degrade(resp, payload)
                if step is None:
                    self._raise_for(resp)
                self.degraded.add(step)
                logger.warning("llm_gateway_degraded", dropped=step, message=self._error_message(resp))
            finally:
                cm.__exit__(None, None, None)


def probe(base_url: str, api_key: str, model: str, timeout: float = 30.0) -> Optional[str]:
    """아주 작은 완성 요청 한 번. 성공이면 None, 실패면 원인 문자열. (자가 점검용)

    ⛔ 처음엔 `"hi"` + `max_tokens=5` 였다. 2026-09-28 개통 당일 자가 점검이 **"응답은 200 인데
       본문이 비었다"** 로 실패했다 — 실측 5회 중 2회가 `finish_reason=length` 로 빈 본문이었다
       (Opus 5 는 thinking 이 기본이라 5토큰이 답 전에 소진된다). 실제 서비스 호출은
       `ClaudeClient._tune()` 이 최소 토큰을 보장해 무관하지만, 매일 40% 로 실패하는 점검은
       곧 아무도 안 읽는다. 한 낱말을 지목하고 여유를 준다 (실측 10/10 `stop`, 4~5토큰).
    """
    try:
        t = GatewayTransport(base_url, api_key, timeout=timeout)
        msg = t.messages.create(model=model, max_tokens=64,
                                messages=[{"role": "user", "content": "Reply with the single word: pong"}])
        if not msg.content:
            return "응답은 200 인데 본문이 비었다"
        return None
    except Exception as e:  # noqa: BLE001 — 원인 문자열이 곧 진단이다
        return f"{type(e).__name__}: {str(e)[:200]}"
