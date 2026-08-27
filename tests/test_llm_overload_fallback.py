"""Regression coverage for provider overloads on the direct chat stream."""

import ast
import inspect
import json
import textwrap
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from app.agents import orchestrator as orchestrator_module
from app.agents.orchestrator import OrchestratorAgent
from app.api import routes as routes_module
from app.core import llm as llm_module
from app.core.llm import ClaudeClient, _is_retryable


class ProviderOverloadedError(RuntimeError):
    status_code = 529


class ProviderBadRequestError(RuntimeError):
    status_code = 400


OVERLOAD = ProviderOverloadedError(
    "{'type': 'error', 'error': {'type': 'overloaded_error', "
    "'message': 'Overloaded'}, 'request_id': 'req_sensitive'}"
)


class _Stream:
    def __init__(self, action):
        self._action = action
        self.text_stream = iter(())

    def __enter__(self):
        if isinstance(self._action, Exception):
            raise self._action
        self.text_stream = iter(self._action)
        return self

    def __exit__(self, *_args):
        return False

    def get_final_message(self):
        return SimpleNamespace(usage=None)


class _MessagesAPI:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def stream(self, **_kwargs):
        action = self.actions[self.calls]
        self.calls += 1
        return _Stream(action)

    def create(self, **_kwargs):
        action = self.actions[self.calls]
        self.calls += 1
        if isinstance(action, Exception):
            raise action
        return action


def _claude_client(actions):
    client = object.__new__(ClaudeClient)
    client.client = SimpleNamespace(messages=_MessagesAPI(actions))
    client.model = "claude-opus-test"
    client._use_temperature = False
    client._thinking = False
    client._effort = "medium"
    return client


def _raising_stream(error=OVERLOAD):
    def _generate(*_args, **_kwargs):
        raise error
        yield  # pragma: no cover - keeps this a generator

    return _generate


class _Fallback:
    def __init__(self, answer="대체 모델 답변", error=None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer

    def generate_with_history(self, *_args, **_kwargs):
        return self.generate(*_args, **_kwargs)


class _StreamingFallback:
    def __init__(self, answer="Flash 답변", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def generate_stream(self, *args):
        self.calls.append(("stream", args))
        if self.error:
            raise self.error
        yield self.answer

    def generate_with_history_stream(self, *args):
        self.calls.append(("history_stream", args))
        if self.error:
            raise self.error
        yield self.answer


class _NonStreamingFallback:
    def __init__(self, answer="Flash 답변", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def _call(self, method, args):
        self.calls.append((method, args))
        if self.error:
            raise self.error
        return self.answer

    def generate(self, *args):
        return self._call("generate", args)

    def generate_with_history(self, *args):
        return self._call("generate_with_history", args)

    def generate_json(self, *args):
        return self._call("generate_json", args)

    def generate_with_images(self, *args):
        return self._call("generate_with_images", args)


class _CapturingLogger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, event, **fields):
        self.errors.append((event, fields))

    def warning(self, event, **fields):
        self.warnings.append((event, fields))

    def info(self, *_args, **_kwargs):
        pass


class _FailingOrchestrator:
    def __init__(self, events):
        self.events = events

    async def route_and_stream(self, *_args, **_kwargs):
        for event in self.events:
            yield event
        raise OVERLOAD


def _sse_contents(frames):
    contents = []
    for frame in frames:
        payload = frame.removeprefix("data: ").strip()
        if payload == "[DONE]" or not payload.startswith("{"):
            continue
        data = json.loads(payload)
        if not data.get("choices"):
            continue
        content = data["choices"][0]["delta"].get("content")
        if content:
            contents.append(content)
    return contents


def test_overloaded_error_and_529_are_retryable():
    assert _is_retryable(OVERLOAD)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("generate_stream", ("질문",)),
        (
            "generate_with_history_stream",
            ([{"role": "user", "content": "질문"}],),
        ),
    ],
)
def test_claude_stream_retries_before_first_token(monkeypatch, method_name, args):
    client = _claude_client([OVERLOAD, ["정상 답변"]])
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)

    chunks = list(getattr(client, method_name)(*args))

    assert chunks == ["정상 답변"]
    assert client.client.messages.calls == 2
    assert sleeps == [0.5]


def test_claude_stream_does_not_restart_after_first_token(monkeypatch):
    def interrupted():
        yield "첫 토큰"
        raise OVERLOAD

    client = _claude_client([interrupted(), ["중복 답변"]])
    fallback = _StreamingFallback()
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    chunks = []
    with pytest.raises(ProviderOverloadedError):
        for chunk in client.generate_stream("질문"):
            chunks.append(chunk)

    assert chunks == ["첫 토큰"]
    assert client.client.messages.calls == 1
    assert sleeps == []
    assert fallback.calls == []


@pytest.mark.parametrize(
    ("method_name", "primary_input", "fallback_kind"),
    [
        ("generate_stream", "질문", "stream"),
        (
            "generate_with_history_stream",
            [{"role": "user", "content": "질문"}],
            "history_stream",
        ),
    ],
)
def test_claude_stream_exhaustion_falls_back_with_original_arguments(
    monkeypatch, method_name, primary_input, fallback_kind
):
    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    fallback = _StreamingFallback()
    logger = _CapturingLogger()
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)
    monkeypatch.setattr(llm_module, "logger", logger)

    chunks = list(
        getattr(client, method_name)(primary_input, "시스템", 0.7, 1234)
    )

    assert chunks == ["Flash 답변"]
    assert fallback.calls == [
        (fallback_kind, (primary_input, "시스템", 0.7, 1234))
    ]
    assert client.client.messages.calls == llm_module._MAX_RETRIES
    assert sleeps == [0.5, 1.5, 4.0]
    assert (
        "claude_stream_fell_back_to_flash",
        {
            "usage_kind": fallback_kind,
            "attempts": llm_module._MAX_RETRIES,
        },
    ) in logger.warnings


def test_non_retryable_stream_error_does_not_fall_back(monkeypatch):
    bad_request = ProviderBadRequestError("invalid request")
    client = _claude_client([bad_request])
    fallback = _StreamingFallback()
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    with pytest.raises(ProviderBadRequestError) as raised:
        list(client.generate_stream("질문"))

    assert raised.value is bad_request
    assert client.client.messages.calls == 1
    assert fallback.calls == []
    assert sleeps == []


def test_flash_failure_preserves_original_claude_error(monkeypatch):
    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    fallback = _StreamingFallback(error=RuntimeError("flash internal failure"))
    monkeypatch.setattr(llm_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    with pytest.raises(ProviderOverloadedError) as raised:
        list(client.generate_stream("질문"))

    assert raised.value is OVERLOAD
    assert fallback.calls == [("stream", ("질문", None, 0.3, 8192))]


@pytest.mark.parametrize(
    ("method_name", "original_args"),
    [
        ("generate", ("질문", "시스템", 0.7, 1234)),
        (
            "generate_with_history",
            ([{"role": "user", "content": "질문"}], "시스템", 0.7, 1234),
        ),
        ("generate_json", ("JSON 질문", "JSON 시스템", 0.0, 2345)),
        (
            "generate_with_images",
            (
                "이미지 질문",
                [{"data": b"raw-image-bytes", "mime_type": "image/png"}],
                "이미지 시스템",
                0.6,
                3456,
            ),
        ),
    ],
)
def test_claude_non_streaming_exhaustion_falls_back_with_original_arguments(
    monkeypatch, method_name, original_args
):
    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    fallback = _NonStreamingFallback()
    logger = _CapturingLogger()
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)
    monkeypatch.setattr(llm_module, "logger", logger)

    result = getattr(client, method_name)(*original_args)

    assert result == "Flash 답변"
    assert fallback.calls == [(method_name, original_args)]
    assert client.client.messages.calls == llm_module._MAX_RETRIES
    assert sleeps == [0.5, 1.5, 4.0]
    assert (
        "claude_fell_back_to_flash",
        {"method": method_name, "attempts": llm_module._MAX_RETRIES},
    ) in logger.warnings


@pytest.mark.parametrize(
    ("method_name", "original_args"),
    [
        ("generate", ("질문",)),
        ("generate_with_history", ([{"role": "user", "content": "질문"}],)),
        ("generate_json", ("JSON 질문",)),
        (
            "generate_with_images",
            ("이미지 질문", [{"data": b"image", "mime_type": "image/png"}]),
        ),
    ],
)
def test_non_retryable_non_streaming_error_does_not_fall_back(
    monkeypatch, method_name, original_args
):
    bad_request = ProviderBadRequestError("invalid request")
    client = _claude_client([bad_request])
    fallback = _NonStreamingFallback()
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    with pytest.raises(ProviderBadRequestError) as raised:
        getattr(client, method_name)(*original_args)

    assert raised.value is bad_request
    assert client.client.messages.calls == 1
    assert fallback.calls == []
    assert sleeps == []


def test_non_streaming_flash_failure_preserves_original_claude_error(monkeypatch):
    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    fallback = _NonStreamingFallback(error=RuntimeError("flash internal failure"))
    monkeypatch.setattr(llm_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    with pytest.raises(ProviderOverloadedError) as raised:
        client.generate("질문")

    assert raised.value is OVERLOAD
    assert fallback.calls == [("generate", ("질문", None, 0.1, 8192))]


def test_streaming_and_non_streaming_share_flash_fallback_eligibility_helper():
    def called_names(function):
        source = textwrap.dedent(inspect.getsource(function))
        tree = ast.parse(source)
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    helper_name = "_should_fall_back_to_flash"
    assert helper_name in called_names(ClaudeClient._stream_with_first_token_retry)
    assert helper_name in called_names(ClaudeClient._fallback_to_flash)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "expected", "unexpected", "streamed_live"),
    [
        (
            [],
            "죄송합니다. AI 서비스가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요.",
            "답변이 중간에 끊겼습니다",
            False,
        ),
        (
            [("chunk", "부분 답변")],
            "\n\n---\n⚠️ 답변이 중간에 끊겼습니다. 위 내용은 완결되지 않았으니 같은 질문을 다시 보내 주세요.",
            "AI 서비스가 일시적으로 혼잡합니다",
            True,
        ),
    ],
)
async def test_stream_boundary_message_depends_on_live_output(
    monkeypatch, events, expected, unexpected, streamed_live
):
    logger = _CapturingLogger()
    monkeypatch.setattr(
        routes_module, "_get_orchestrator", lambda: _FailingOrchestrator(events)
    )
    monkeypatch.setattr(routes_module, "logger", logger)
    monkeypatch.setattr(
        routes_module.asyncio,
        "get_event_loop",
        lambda: SimpleNamespace(run_in_executor=lambda *_args, **_kwargs: None),
    )

    frames = [
        frame
        async for frame in routes_module._stream_response(
            "질문", [], "claude", SimpleNamespace(model="test-model")
        )
    ]
    contents = _sse_contents(frames)

    assert expected in contents
    assert all(unexpected not in content for content in contents)
    boundary_logs = [
        fields
        for event, fields in logger.errors
        if event == "chat_stream_boundary_failed"
    ]
    assert boundary_logs == [
        {
            "error_type": "ProviderOverloadedError",
            "error": str(OVERLOAD)[:200],
            "route": "direct",
            "streamed_live": streamed_live,
        }
    ]


def test_api_http_exception_details_do_not_reference_caught_exception():
    root = Path(__file__).resolve().parents[1]
    api_files = (
        "app/api/routes.py",
        "app/api/reports_api.py",
        "app/api/admin_api.py",
        "app/api/admin_group_api.py",
    )
    unsafe_details = []

    for relative_path in api_files:
        source = (root / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "HTTPException":
                continue
            detail = next((kw.value for kw in node.keywords if kw.arg == "detail"), None)
            if detail is not None and any(
                isinstance(part, ast.Name) and part.id == "e"
                for part in ast.walk(detail)
            ):
                unsafe_details.append((relative_path, node.lineno))

    assert unsafe_details == []


def test_partial_stream_is_not_restarted_or_fallen_back():
    def primary_stream(*_args, **_kwargs):
        yield "이미 받은 답변"
        raise OVERLOAD

    primary = SimpleNamespace(generate_stream=primary_stream)
    fallback = _Fallback()

    chunks = list(
        orchestrator_module._stream_direct_with_fallback(
            primary,
            "질문",
            system_instruction="시스템",
            fallback_client=fallback,
        )
    )
    answer = "".join(chunks)

    assert "이미 받은 답변" in answer
    assert "응답 생성이 일시적으로 중단" in answer
    assert fallback.calls == 0
    assert "Overloaded" not in answer
    assert "req_sensitive" not in answer


def test_direct_stream_falls_back_without_exposing_provider_error():
    primary = SimpleNamespace(generate_stream=_raising_stream())
    fallback = _Fallback()

    answer = "".join(
        orchestrator_module._stream_direct_with_fallback(
            primary,
            "질문",
            system_instruction=[
                {"type": "text", "text": "정적 지침"},
                {"type": "text", "text": "최신 검색 정보"},
            ],
            fallback_client=fallback,
        )
    )

    assert answer == "대체 모델 답변"
    assert fallback.calls == 1
    assert "Overloaded" not in answer
    assert "req_sensitive" not in answer


def test_strip_model_claim_removes_model_and_adds_disclosure_guidance(monkeypatch):
    monkeypatch.setattr(
        orchestrator_module, "_model_display_name", lambda: "Claude Opus 5"
    )
    prompt = (
        "당신은 Craver의 AI 어시스턴트입니다. (Claude Opus 5 기반)\n"
        "다음 지침"
    )

    result = orchestrator_module._strip_model_claim(prompt)

    assert result.startswith("당신은 Craver의 AI 어시스턴트입니다.\n")
    assert "Claude Opus 5 기반" not in result
    assert (
        "어떤 모델로 동작 중인지 답변에서 밝히지 마세요. "
        "물으면 확인해 드릴 수 없다고 답하세요."
    ) in result
    assert result.endswith("다음 지침")


def test_strip_model_claim_leaves_unrelated_prompt_unchanged():
    prompt = "당신은 다른 업무를 처리하는 도우미입니다.\n원래 지침"

    assert orchestrator_module._strip_model_claim(prompt) == prompt


def test_strip_model_claim_removes_current_display_name():
    model_name = orchestrator_module._model_display_name()
    prompt = f"당신은 Craver의 AI 어시스턴트입니다. ({model_name} 기반)"

    result = orchestrator_module._strip_model_claim(prompt)

    assert model_name not in result


def test_direct_stream_fallback_receives_neutral_system_prompt(monkeypatch):
    monkeypatch.setattr(
        orchestrator_module, "_model_display_name", lambda: "Claude Opus 5"
    )
    captured = {}

    class CapturingFallback:
        def generate(self, *_args, **kwargs):
            captured.update(kwargs)
            return "Flash 답변"

    system_instruction = [
        {
            "type": "text",
            "text": "당신은 Craver의 AI 어시스턴트입니다. (Claude Opus 5 기반)",
        },
        {"type": "text", "text": "추가 지침"},
    ]

    answer = "".join(
        orchestrator_module._stream_direct_with_fallback(
            SimpleNamespace(generate_stream=_raising_stream()),
            "질문",
            system_instruction=system_instruction,
            fallback_client=CapturingFallback(),
        )
    )

    fallback_system = captured["system_instruction"]
    assert answer == "Flash 답변"
    assert "Claude Opus 5" not in fallback_system
    assert "어떤 모델로 동작 중인지 답변에서 밝히지 마세요." in fallback_system
    assert "추가 지침" in fallback_system


@pytest.mark.parametrize(
    ("method_name", "required_args", "expected_fallback_args"),
    [
        ("generate", ("질문",), ("질문", "중립 시스템", 0.1, 8192)),
        (
            "generate_with_history",
            ([{"role": "user", "content": "질문"}],),
            ([{"role": "user", "content": "질문"}], "중립 시스템", 0.1, 8192),
        ),
        ("generate_json", ("JSON 질문",), ("JSON 질문", "중립 시스템", 0.0, 4096)),
        (
            "generate_with_images",
            ("이미지 질문", [{"data": b"image", "mime_type": "image/png"}]),
            (
                "이미지 질문",
                [{"data": b"image", "mime_type": "image/png"}],
                "중립 시스템",
                0.3,
                8192,
            ),
        ),
    ],
)
def test_non_streaming_fallback_uses_supplied_system_instruction(
    monkeypatch, method_name, required_args, expected_fallback_args
):
    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    fallback = _NonStreamingFallback()
    monkeypatch.setattr(llm_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: fallback)

    result = getattr(client, method_name)(
        *required_args,
        system_instruction="원래 시스템",
        fallback_system_instruction="중립 시스템",
    )

    assert result == "Flash 답변"
    assert fallback.calls == [(method_name, expected_fallback_args)]


@pytest.mark.parametrize(
    "method_name",
    ["generate", "generate_with_history", "generate_json", "generate_with_images"],
)
def test_non_streaming_client_method_signatures_match(method_name):
    claude_signature = inspect.signature(getattr(llm_module.ClaudeClient, method_name))
    gemini_signature = inspect.signature(getattr(llm_module.GeminiClient, method_name))

    assert "fallback_system_instruction" in claude_signature.parameters
    assert claude_signature == gemini_signature


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["generate", "generate_with_history", "generate_with_images"])
async def test_handle_direct_supplies_neutral_fallback_system(monkeypatch, mode):
    captured = {}

    class CapturingClient:
        def _capture(self, method, **kwargs):
            captured.update(method=method, **kwargs)
            return "답변"

        def generate(self, _query, **kwargs):
            return self._capture("generate", **kwargs)

        def generate_with_history(self, **kwargs):
            return self._capture("generate_with_history", **kwargs)

        def generate_with_images(self, _text, _images, **kwargs):
            return self._capture("generate_with_images", **kwargs)

    model_name = orchestrator_module._model_display_name()
    system = f"당신은 Craver의 AI 어시스턴트입니다. ({model_name} 기반)\n기본 지침"
    agent = object.__new__(OrchestratorAgent)
    agent._build_direct_system_prompt = MethodType(lambda _self: system, agent)
    agent._needs_web_search = MethodType(lambda _self, _query: False, agent)
    monkeypatch.setattr(
        orchestrator_module, "get_llm_client", lambda *_args, **_kwargs: CapturingClient()
    )

    messages = []
    images = []
    if mode == "generate_with_history":
        messages = [
            {"role": "user", "content": "첫 질문"},
            {"role": "assistant", "content": "첫 답변"},
            {"role": "user", "content": "후속 질문"},
        ]
    elif mode == "generate_with_images":
        images = [{"data": b"image", "mime_type": "image/png"}]

    result = await agent._handle_direct(
        "질문",
        messages=messages,
        conversation_context="",
        model_type="claude",
        images=images,
    )

    fallback_system = captured["fallback_system_instruction"]
    assert result == {"source": "direct", "answer": "답변"}
    assert captured["method"] == mode
    assert model_name not in fallback_system
    assert "어떤 모델로 동작 중인지 답변에서 밝히지 마세요." in fallback_system


def test_double_provider_failure_returns_only_safe_message():
    primary = SimpleNamespace(generate_stream=_raising_stream())
    fallback = _Fallback(error=RuntimeError("gemini secret response"))

    answer = "".join(
        orchestrator_module._stream_direct_with_fallback(
            primary,
            "질문",
            system_instruction="시스템",
            fallback_client=fallback,
        )
    )

    assert "AI 서비스가 일시적으로 혼잡" in answer
    assert "Overloaded" not in answer
    assert "req_sensitive" not in answer
    assert "gemini secret" not in answer


@pytest.mark.asyncio
async def test_route_and_stream_uses_safe_fallback(monkeypatch):
    primary = SimpleNamespace(generate_stream=_raising_stream())
    fallback = _Fallback()
    monkeypatch.setattr(
        orchestrator_module, "get_llm_client", lambda *_args, **_kwargs: primary
    )
    monkeypatch.setattr(orchestrator_module, "get_flash_client", lambda: fallback)

    from app.knowledge import wiki_search
    from app.agents import skill_memory

    async def no_wiki(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(wiki_search, "search_with_pages", no_wiki)
    monkeypatch.setattr(skill_memory, "load_skill_context", lambda *_args, **_kwargs: "")

    agent = object.__new__(OrchestratorAgent)
    agent._needs_web_search = MethodType(lambda _self, _query: False, agent)
    agent._build_direct_system_prompt = MethodType(lambda _self: "시스템", agent)

    events = [
        event
        async for event in agent.route_and_stream(
            "안녕하세요", messages=[], enabled_sources=[]
        )
    ]
    answer = "".join(data for kind, data in events if kind == "chunk")

    assert answer == "대체 모델 답변"
    assert ("source", "direct") in events
    assert "Overloaded" not in answer
    assert "req_sensitive" not in answer


def test_every_declared_delay_is_actually_used(monkeypatch):
    """⛔ 정의와 실제 대기가 어긋나면 안 된다 — 에러가 아니라 오해를 만든다.

    2026-08-27 실측에서 잡혔다: `_RETRY_DELAYS = [0.5, 1.5, 4.0, 10.0]` 에
    `_MAX_RETRIES = 4` 였는데 **마지막 10.0초는 한 번도 쓰이지 않았다.** 4번째 시도는
    실패하면 대기 없이 바로 올라오기 때문이다. 코드를 읽으면 16초를 기다릴 것 같은데
    실제로는 6초였고, 재보기 전까지 아무도 몰랐다.
    """
    slept: list[float] = []
    monkeypatch.setattr(llm_module.time, "sleep", slept.append)
    monkeypatch.setattr(llm_module, "get_flash_client", lambda: _Fallback())

    client = _claude_client([OVERLOAD] * llm_module._MAX_RETRIES)
    client.generate("질문")

    assert client.client.messages.calls == llm_module._MAX_RETRIES
    # 대기는 시도보다 하나 적다. 선언한 값이 전부, 순서대로, 한 번씩 쓰여야 한다.
    assert slept == llm_module._RETRY_DELAYS, (
        f"선언 {llm_module._RETRY_DELAYS} 인데 실제로 잔 것은 {slept}"
    )
    assert llm_module._MAX_RETRIES == len(llm_module._RETRY_DELAYS) + 1


def test_max_retries_is_derived_not_hand_written():
    """따로 적으면 언젠가 어긋난다 — 파생시켜 구조적으로 막는다."""
    import inspect

    source = inspect.getsource(llm_module)
    head = source.split("def _is_retryable")[0]
    assert "_MAX_RETRIES = len(_RETRY_DELAYS) + 1" in head, (
        "_MAX_RETRIES 를 손으로 적으면 대기 목록과 어긋난다"
    )
