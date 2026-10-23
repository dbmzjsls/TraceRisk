import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from tracerisk.challenge_eval.client import DeepSeekClient, DeepSeekRequestSettings
from tracerisk.challenge_eval.schemas import AttemptRecord

CONFIG = (
    Path(__file__).resolve().parents[4]
    / "datasets/evaluation/model_challenge_config.json"
)
MESSAGES = [{"role": "user", "content": "evidence"}]
SECRET = "private-api-key"


def settings() -> DeepSeekRequestSettings:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return DeepSeekRequestSettings.model_validate(
        {key: config[key] for key in DeepSeekRequestSettings.model_fields}
    )


def completion(**updates: object) -> dict[str, object]:
    return {
        "model": "deepseek-flash-20261023",
        "system_fingerprint": "fp-test",
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"findings": []}'}}
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 20,
        },
        **updates,
    }


def invoke(handler: object):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DeepSeekClient(
                settings=settings(), api_key=SecretStr(SECRET), client=http
            )
            return await client.complete_once(MESSAGES)

    return asyncio.run(run())


def test_success_records_usage_and_exact_request() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + SECRET
        assert json.loads(request.content) == {
            "messages": MESSAGES,
            "model": "deepseek-flash",
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        }
        return httpx.Response(200, json=completion())

    result = invoke(handle)
    assert len(requests) == 1
    assert result.status == "succeeded" and not result.retryable
    assert result.http_status == 200 and result.error_category is None
    assert result.returned_model == "deepseek-flash-20261023"
    assert result.system_fingerprint == "fp-test" and result.finish_reason == "stop"
    assert result.response_content == '{"findings": []}'
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (
        50,
        10,
        60,
    )
    assert (result.cache_hit_tokens, result.cache_miss_tokens) == (30, 20)
    assert result.latency_ms >= 0
    attempt = AttemptRecord(
        case_id="case-1",
        attempt_number=1,
        request_kind="original",
        **result.model_dump(),
    )
    assert SECRET not in attempt.model_dump_json()
    assert attempt.request_parameters == settings().model_dump(exclude={"base_url"})


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (429, "rate_limit", True),
        (500, "server", True),
        (503, "server", True),
        (401, "authentication", False),
        (403, "authentication", False),
        (400, "http_permanent", False),
        (302, "http_permanent", False),
    ],
)
def test_http_errors_never_retry_or_record_sensitive_bodies(
    status, category, retryable, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            text=SECRET + " environment-secret",
            headers={"Authorization": SECRET},
        )

    result = invoke(handle)
    assert len(calls) == 1
    assert (
        result.status,
        result.http_status,
        result.error_category,
        result.retryable,
    ) == ("failed", status, category, retryable)
    assert SECRET not in result.model_dump_json()
    assert "environment-secret" not in result.model_dump_json()
    assert result.response_content is None


@pytest.mark.parametrize(
    ("exception", "category"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")],
)
def test_transport_failures_are_sanitized_single_attempts(exception, category) -> None:
    calls = []

    def handle(request):
        calls.append(request)
        raise exception("Authorization: Bearer " + SECRET, request=request)

    result = invoke(handle)
    assert len(calls) == 1
    assert result.status == "failed" and result.retryable
    assert result.error_category == category and result.http_status is None
    assert SECRET not in result.model_dump_json()
    assert result.latency_ms >= 0


@pytest.mark.parametrize(
    "body", [b"not-json", b"null", b"{}", b'{"choices": []}', b'{"choices": [null]}']
)
def test_malformed_response_is_preserved_as_failure(body) -> None:
    result = invoke(lambda _: httpx.Response(200, content=body))
    assert result.status == "failed" and not result.retryable
    assert result.error_category == "malformed_response" and result.http_status == 200


@pytest.mark.parametrize(
    ("content", "reason", "category"),
    [
        (None, "stop", "null_content"),
        ("partial", "length", "non_stop_finish"),
        ("partial", "content_filter", "non_stop_finish"),
    ],
)
def test_unusable_outputs_preserve_returned_metadata(content, reason, category) -> None:
    result = invoke(
        lambda _: httpx.Response(
            200,
            json=completion(
                choices=[{"finish_reason": reason, "message": {"content": content}}]
            ),
        )
    )
    assert result.status == "failed" and not result.retryable
    assert result.error_category == category
    assert result.response_content == content and result.finish_reason == reason
    assert (
        result.returned_model == "deepseek-flash-20261023" and result.total_tokens == 60
    )


def test_missing_usage_is_unknown_not_zero() -> None:
    result = invoke(lambda _: httpx.Response(200, json=completion(usage=None)))
    assert result.status == "succeeded"
    assert (
        result.input_tokens,
        result.output_tokens,
        result.total_tokens,
        result.cache_hit_tokens,
        result.cache_miss_tokens,
    ) == (None,) * 5


def test_owned_http_client_disables_environment_and_closes(monkeypatch) -> None:
    original = httpx.AsyncClient
    created = []

    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        instance = original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=completion())
            ),
            **kwargs,
        )
        created.append(instance)
        return instance

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = asyncio.run(
        DeepSeekClient(settings=settings(), api_key=SecretStr(SECRET)).complete_once(
            MESSAGES
        )
    )
    assert result.status == "succeeded" and created[0].is_closed


@pytest.mark.parametrize("field", ["thinking", "response_format"])
def test_settings_reject_missing_required_frozen_parameter(field) -> None:
    values = settings().model_dump()
    values[field] = {}
    with pytest.raises(ValidationError):
        DeepSeekRequestSettings.model_validate(values)


def test_monotonic_latency_is_recorded(monkeypatch) -> None:
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(
        "tracerisk.challenge_eval.client.time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    result = invoke(lambda _: httpx.Response(200, json=completion()))
    assert result.latency_ms == 125


def test_malformed_response_retains_available_metadata_and_valid_usage() -> None:
    result = invoke(
        lambda _: httpx.Response(
            200,
            json=completion(
                choices=[
                    {"finish_reason": "stop", "message": {"content": ["invalid"]}}
                ],
                usage={
                    "prompt_tokens": 50,
                    "completion_tokens": -1,
                    "total_tokens": 60,
                },
            ),
        )
    )
    assert result.status == "failed" and result.error_category == "malformed_response"
    assert result.returned_model == "deepseek-flash-20261023"
    assert result.system_fingerprint == "fp-test" and result.finish_reason == "stop"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (
        50,
        None,
        60,
    )


def test_key_echo_is_redacted_and_injected_client_remains_open() -> None:
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json=completion(
                        choices=[
                            {"finish_reason": "stop", "message": {"content": SECRET}}
                        ]
                    ),
                )
            )
        ) as http:
            result = await DeepSeekClient(
                settings=settings(), api_key=SecretStr(SECRET), client=http
            ).complete_once(MESSAGES)
            assert not http.is_closed
            assert SECRET not in result.model_dump_json()

    asyncio.run(run())
