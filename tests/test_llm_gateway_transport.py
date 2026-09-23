"""사내 LLM 게이트웨이 전송층 회귀 (2026-09-23).

게이트웨이는 아직 키 권한이 없어 실측할 수 없다 — 그래서 HTTP 층을 `httpx.MockTransport`
로 대신하고, **ClaudeClient 가 실제로 지나는 경로**(create / stream / _first_text /
usage 계측 / 재시도 분류)로 검사한다. 함수를 따로 불러 보고 "확인했다" 고 하지 않는다.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.core import llm as llm_module
from app.core import llm_gateway as gw
from app.core.llm import ClaudeClient, _is_retryable


# ---------------------------------------------------------------------------
# 가짜 게이트웨이
# ---------------------------------------------------------------------------


def _ok_json(text="서울", usage=None):
    return {
        "id": "chatcmpl-1", "model": "claude-opus-5",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": usage or {"prompt_tokens": 12, "completion_tokens": 3,
                           "prompt_tokens_details": {"cached_tokens": 5}},
    }


def _sse(chunks, usage=None):
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]}))
    lines.append("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                        "usage": usage or {"prompt_tokens": 7, "completion_tokens": 4}}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


class FakeGateway:
    """요청을 기록하고 미리 정해 둔 응답을 돌려준다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"headers": dict(request.headers), "json": body, "url": str(request.url)})
        r = self.responses.pop(0)
        if callable(r):
            return r(body)
        status, payload = r
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload, headers={"content-type": "text/event-stream"})
        return httpx.Response(status, json=payload)


def _transport(fake: FakeGateway, **kw) -> gw.GatewayTransport:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return gw.GatewayTransport("https://llmgw.example/", "sk-test", http_client=client, **kw)


def _client_with(fake: FakeGateway, monkeypatch, websearch=False) -> ClaudeClient:
    settings = SimpleNamespace(
        anthropic_api_key="", anthropic_opus_model="claude-opus-5", anthropic_effort="medium",
        llm_gateway_url="https://llmgw.example", llm_gateway_key="sk-test",
        llm_gateway_websearch=websearch, llm_gateway_timeout=30.0,
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    c = ClaudeClient()
    assert c.transport == "gateway"
    c.client = _transport(fake)
    return c


# ---------------------------------------------------------------------------
# 1. 요청 변환 — Anthropic kwargs → OpenAI payload
# ---------------------------------------------------------------------------


def test_payload_keeps_system_cache_blocks_and_maps_effort():
    kwargs = {
        "model": "claude-opus-5", "max_tokens": 16000,
        "system": [{"type": "text", "text": "너는 비서다", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "안녕"}],
        "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"},
    }
    p = gw.build_payload(kwargs)
    assert p["model"] == "claude-opus-5" and p["max_tokens"] == 16000
    assert p["messages"][0] == {"role": "system", "content": [
        {"type": "text", "text": "너는 비서다", "cache_control": {"type": "ephemeral"}}]}
    assert p["messages"][1] == {"role": "user", "content": "안녕"}
    assert p["reasoning_effort"] == "medium"
    # Anthropic 전용 키는 게이트웨이로 새지 않는다
    assert "thinking" not in p and "output_config" not in p and "system" not in p
    assert "stream" not in p and "tools" not in p


@pytest.mark.parametrize("effort,expected", [("low", "low"), ("high", "high"), ("xhigh", "high"), ("max", "high")])
def test_effort_vocabulary_folds_into_openai_words(effort, expected):
    p = gw.build_payload({"model": "m", "messages": [], "output_config": {"effort": effort}})
    assert p["reasoning_effort"] == expected


def test_payload_converts_images_to_data_urls():
    kwargs = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        {"type": "text", "text": "이 색은?"},
    ]}]}
    parts = gw.build_payload(kwargs)["messages"][0]["content"]
    assert parts == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "이 색은?"},
    ]


def test_stream_payload_asks_for_usage():
    p = gw.build_payload({"model": "m", "messages": []}, stream=True)
    assert p["stream"] is True and p["stream_options"] == {"include_usage": True}


def test_degraded_payload_drops_effort_and_flattens_system():
    kwargs = {"model": "m", "messages": [],
              "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
                         {"type": "text", "text": "B"}],
              "output_config": {"effort": "medium"}}
    p = gw.build_payload(kwargs, degraded={"reasoning_effort", "system_parts"})
    assert "reasoning_effort" not in p
    assert p["messages"][0] == {"role": "system", "content": "A\nB"}


# ---------------------------------------------------------------------------
# 2. 응답 — ClaudeClient 경로로 지나간다
# ---------------------------------------------------------------------------


def test_generate_goes_through_gateway_and_records_usage(monkeypatch):
    fake = FakeGateway([(200, _ok_json("서울"))])
    c = _client_with(fake, monkeypatch)
    recorded = []
    monkeypatch.setattr("app.core.usage_meter.record_claude",
                        lambda model, method, usage: recorded.append((model, method, usage)))

    assert c.generate("대한민국의 수도는?", system_instruction="한 단어로") == "서울"

    req = fake.requests[0]
    assert req["headers"]["authorization"] == "Bearer sk-test"
    assert req["url"].endswith("/v1/chat/completions")
    assert req["json"]["messages"][0]["role"] == "system"
    assert req["json"]["messages"][-1] == {"role": "user", "content": "대한민국의 수도는?"}
    # Opus 5 는 thinking 모델이라 _tune 이 max_tokens 를 올리고 effort 를 붙인다 — 그대로 실려야 한다
    assert req["json"]["max_tokens"] >= ClaudeClient._MIN_MAX_TOKENS
    assert req["json"]["reasoning_effort"] == "medium"
    # 토큰 계측은 Anthropic 속성 이름으로 받는다
    assert recorded and recorded[0][2].input_tokens == 12 and recorded[0][2].output_tokens == 3
    assert recorded[0][2].cache_read_input_tokens == 5


def test_empty_body_leaves_no_text_block_so_the_warning_fires(monkeypatch, caplog):
    """본문이 비면 `_first_text` 가 `claude_no_text_block` 을 남겨야 한다 (골든 run#88 의 자리)."""
    fake = FakeGateway([(200, _ok_json("", usage={"prompt_tokens": 5, "completion_tokens": 900}))])
    c = _client_with(fake, monkeypatch)
    monkeypatch.setattr("app.core.usage_meter.record_claude", lambda *a: None)
    warned = []
    monkeypatch.setattr(llm_module.logger, "warning", lambda event, **kw: warned.append((event, kw)))
    assert c.generate("x") == ""
    assert any(e == "claude_no_text_block" for e, _ in warned)


def test_stream_yields_deltas_and_final_usage(monkeypatch):
    fake = FakeGateway([(200, _sse(["서", "울", "입니다"]))])
    c = _client_with(fake, monkeypatch)
    recorded = []
    monkeypatch.setattr("app.core.usage_meter.record_claude",
                        lambda model, method, usage: recorded.append((method, usage)))

    chunks = list(c.generate_with_history_stream(
        [{"role": "user", "content": "수도는?"}], system_instruction="짧게"))

    assert chunks == ["서", "울", "입니다"]
    assert fake.requests[0]["json"]["stream"] is True
    assert recorded == [("history_stream", recorded[0][1])]
    assert recorded[0][1].input_tokens == 7 and recorded[0][1].output_tokens == 4
    assert "tools" not in fake.requests[0]["json"]  # 웹서치 기본 꺼짐


def test_websearch_tools_attach_only_to_chat_stream_when_enabled(monkeypatch):
    fake = FakeGateway([(200, _sse(["a"])), (200, _ok_json("b"))])
    c = _client_with(fake, monkeypatch, websearch=True)
    monkeypatch.setattr("app.core.usage_meter.record_claude", lambda *a: None)
    list(c.generate_with_history_stream([{"role": "user", "content": "뉴스"}]))
    c.generate("보고서 해석")
    assert fake.requests[0]["json"]["tools"] == gw.WEBSEARCH_TOOLS
    assert "tools" not in fake.requests[1]["json"]  # 비채팅 호출엔 안 붙는다


def test_anthropic_path_never_gets_websearch_tools(monkeypatch):
    settings = SimpleNamespace(anthropic_api_key="k", anthropic_opus_model="claude-opus-5",
                               anthropic_effort="medium", llm_gateway_url="", llm_gateway_key="",
                               llm_gateway_websearch=True, llm_gateway_timeout=30.0)
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    c = ClaudeClient()
    assert c.transport == "anthropic"
    assert c._websearch_tools() is None


# ---------------------------------------------------------------------------
# 3. 오류 — 분류와 자동 강등
# ---------------------------------------------------------------------------


def test_403_is_not_retryable_and_surfaces_message():
    fake = FakeGateway([(403, {"error": {"message": "team not allowed to access model", "code": "403"}})])
    t = _transport(fake)
    with pytest.raises(gw.GatewayError) as ei:
        t.messages.create(model="claude-opus-5", messages=[{"role": "user", "content": "hi"}])
    assert ei.value.status_code == 403
    assert "team not allowed" in str(ei.value)
    assert _is_retryable(ei.value) is False


def test_429_and_5xx_are_retryable():
    for status in (429, 502, 503):
        fake = FakeGateway([(status, {"error": {"message": "busy"}})])
        with pytest.raises(gw.GatewayError) as ei:
            _transport(fake).messages.create(model="m", messages=[])
        assert _is_retryable(ei.value) is True, status


def test_400_drops_reasoning_effort_once_and_remembers():
    fake = FakeGateway([
        (400, {"error": {"message": "Unsupported parameter: reasoning_effort"}}),
        (200, _ok_json("ok")),
        (200, _ok_json("ok2")),
    ])
    t = _transport(fake)
    kw = {"model": "m", "messages": [{"role": "user", "content": "x"}], "output_config": {"effort": "medium"}}
    assert t.messages.create(**kw).content[0].text == "ok"
    assert "reasoning_effort" in fake.requests[0]["json"]
    assert "reasoning_effort" not in fake.requests[1]["json"]
    t.messages.create(**kw)  # 두 번째 호출은 400 을 다시 맞지 않는다
    assert "reasoning_effort" not in fake.requests[2]["json"]
    assert t.degraded == {"reasoning_effort"}


def test_400_without_a_degradable_cause_raises():
    fake = FakeGateway([(400, {"error": {"message": "messages: required"}})])
    with pytest.raises(gw.GatewayError) as ei:
        _transport(fake).messages.create(model="m", messages=[])
    assert ei.value.status_code == 400


def test_stream_400_degrades_then_streams():
    fake = FakeGateway([
        (400, {"error": {"message": "reasoning_effort not supported"}}),
        (200, _sse(["ok"])),
    ])
    t = _transport(fake)
    with t.messages.stream(model="m", messages=[{"role": "user", "content": "x"}],
                           output_config={"effort": "low"}) as s:
        assert list(s.text_stream) == ["ok"]
    assert t.degraded == {"reasoning_effort"}


# ---------------------------------------------------------------------------
# 4. 배선 — 설정이 전송을 고른다
# ---------------------------------------------------------------------------


def test_half_configuration_falls_back_to_anthropic_with_warning(monkeypatch):
    settings = SimpleNamespace(anthropic_api_key="k", anthropic_opus_model="claude-opus-5",
                               anthropic_effort="medium", llm_gateway_url="https://x", llm_gateway_key="",
                               llm_gateway_websearch=False, llm_gateway_timeout=30.0)
    monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
    warned = []
    monkeypatch.setattr(llm_module.logger, "warning", lambda event, **kw: warned.append(event))
    assert ClaudeClient().transport == "anthropic"
    assert "llm_gateway_half_configured" in warned


def test_probe_reports_denial_as_text():
    fake = FakeGateway([(403, {"error": {"message": "team not allowed to access model"}})])
    monkeypatch_client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    orig = gw.GatewayTransport.__init__

    def patched(self, base_url, api_key, *, timeout=120.0, http_client=None):
        orig(self, base_url, api_key, timeout=timeout, http_client=monkeypatch_client)

    gw.GatewayTransport.__init__ = patched
    try:
        err = gw.probe("https://x", "k", "claude-opus-5")
    finally:
        gw.GatewayTransport.__init__ = orig
    assert err and "403" in err and "team not allowed" in err
