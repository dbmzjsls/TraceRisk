import inspect
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from tests.unit.ai.test_mock_adapter import chunk
from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.openai_compatible import OpenAICompatibleAdapter
from tracerisk.ai.protocol import ModelAdapter, ModelAdapterError
from tracerisk.ai.schemas import RiskContext
from tracerisk.config import Settings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "failure,attempts",
    [
        ("timeout", 2),
        ("network", 2),
        ("decoding", 2),
        (429, 2),
        (500, 2),
        (401, 1),
        (400, 1),
        ("json", 2),
        ("schema", 2),
    ],
)
async def test_bounded_transport_failures(failure, attempts):
    records = []
    calls = []

    async def record(row):
        records.append(row)

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret response", request=request)
        if failure == "network":
            raise httpx.ConnectError("secret key", request=request)
        if failure == "decoding":
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=b"secret corrupt compressed response",
            )
        if isinstance(failure, int):
            return httpx.Response(failure, text="secret body")
        if failure == "json":
            return httpx.Response(200, text="secret non-json")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"extra": "secret"}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(
            client,
            base_url="http://localhost/v1",
            model="local",
            api_key=SecretStr("secret"),
            run_id=uuid4(),
            recorder=record,
        )
        assert isinstance(adapter, ModelAdapter)
        with pytest.raises(ModelAdapterError) as error:
            await adapter.analyze_risk(RiskContext(company_id=uuid4()))
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert len(calls) == len(records) == attempts
    assert [r.attempt_number for r in records] == list(range(1, attempts + 1))
    assert len({r.logical_call_id for r in records}) == 1
    assert all(r.status == "failed" and r.error_category for r in records)


async def test_retry_then_success_and_new_logical_id():
    records = []
    calls = []

    async def record(row):
        records.append(row)

    def handler(request):
        calls.append(request)
        assert request.url == "http://localhost/v1/chat/completions"
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 30.0,
            "write": 10.0,
            "pool": 5.0,
        }
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(
            client,
            base_url="http://localhost/v1",
            model="local",
            api_key=SecretStr("secret"),
            run_id=uuid4(),
            recorder=record,
        )
        context = RiskContext(company_id=uuid4())
        assert not (await adapter.analyze_risk(context)).findings
        assert not (await adapter.analyze_risk(context)).findings
    assert len(records) == 3
    assert (
        records[0].logical_call_id
        == records[1].logical_call_id
        != records[2].logical_call_id
    )
    assert records[1].input_tokens == 12 and records[1].output_tokens == 3
    assert records[1].latency_ms >= 0
    assert records[1].status == "succeeded"
    assert inspect.iscoroutinefunction(MockModelAdapter.analyze_risk)
    assert isinstance(MockModelAdapter(), ModelAdapter)
    assert not (await MockModelAdapter().analyze_risk(context)).findings


async def test_recorder_failure_prevents_retry():
    calls = []

    async def record(row):
        raise RuntimeError("credentials and raw response")

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(
            client,
            base_url="http://localhost/v1",
            model="local",
            api_key=SecretStr("secret"),
            run_id=uuid4(),
            recorder=record,
        )
        with pytest.raises(ModelAdapterError, match="recorder") as error:
            await adapter.analyze_risk(RiskContext(company_id=uuid4()))
    assert len(calls) == 1
    assert error.value.__cause__ is None


@pytest.mark.parametrize("damage", ["boolean_coercion", "extra_field"])
async def test_nested_output_is_strict_and_failed_usage_is_recorded(damage):
    records = []
    context = RiskContext(company_id=chunk().company_id, evidence_chunks=[chunk()])
    draft = (await MockModelAdapter().analyze_risk(context)).model_dump(mode="json")
    if damage == "boolean_coercion":
        draft["findings"][0]["review_recommended"] = "true"
    else:
        draft["findings"][0]["score"] = 99

    async def record(row):
        records.append(row)

    import json

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(draft)}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(
            client,
            base_url="http://localhost/v1",
            model="local",
            api_key=SecretStr("secret"),
            run_id=uuid4(),
            recorder=record,
        )
        with pytest.raises(ModelAdapterError, match="structured_output"):
            await adapter.analyze_risk(context)
    assert len(records) == 2
    assert all((r.input_tokens, r.output_tokens) == (8, 4) for r in records)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"model_base_url": "http://localhost/v1"},
        {"model_base_url": "http://localhost/v1", "model_name": "local"},
        {
            "model_base_url": "http://secret@localhost/v1",
            "model_name": "local",
            "model_api_key": "secret",
        },
    ],
)
def test_http_configuration_requires_explicit_safe_credentials(config):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, model_adapter="openai_compatible", **config)
    assert "secret" not in str(error.value)


def test_runtime_import_does_not_load_optional_embedding_packages():
    script = """
import sys
class BlockEmbeddingImports:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'sentence_transformers', 'torch', 'transformers'}:
            raise AssertionError('optional embedding import: ' + fullname)
sys.meta_path.insert(0, BlockEmbeddingImports())
from tracerisk.ai.openai_compatible import OpenAICompatibleAdapter
from tracerisk.ai.service import generate_validate_findings
from tracerisk.config import Settings
from pydantic import SecretStr
assert Settings(_env_file=None).model_adapter == 'mock'
settings = Settings(
    _env_file=None, model_adapter='openai_compatible',
    model_base_url='http://localhost/v1', model_name='local',
    model_api_key=SecretStr('test'),
)
assert settings.model_api_key.get_secret_value() == 'test'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
