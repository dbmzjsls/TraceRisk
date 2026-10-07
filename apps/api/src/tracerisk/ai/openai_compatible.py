import time
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from tracerisk.ai.invocations import InvocationAttempt, InvocationRecorder
from tracerisk.ai.prompts import PROMPT_VERSION, build_messages
from tracerisk.ai.protocol import ModelAdapterError
from tracerisk.ai.schemas import RiskContext, StructuredRiskFindings

TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class Usage(BaseModel):
    model_config = ConfigDict(strict=True)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class Message(BaseModel):
    model_config = ConfigDict(strict=True)
    content: str


class Choice(BaseModel):
    message: Message


class Completion(BaseModel):
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: Usage | None = None


class OpenAICompatibleAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        model: str,
        api_key: SecretStr,
        run_id: UUID,
        recorder: InvocationRecorder,
    ) -> None:
        self.client = client
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.run_id = run_id
        self.recorder = recorder

    async def analyze_risk(self, context: RiskContext) -> StructuredRiskFindings:
        logical_call_id = uuid4()
        payload = {
            "model": self.model,
            "messages": build_messages(context),
            "response_format": {"type": "json_object"},
        }
        for attempt_number in (1, 2):
            started = time.monotonic()
            category: str | None = None
            retryable = True
            usage: Usage | None = None
            result: StructuredRiskFindings | None = None
            try:
                response = await self.client.post(
                    self.url,
                    headers={
                        "Authorization": "Bearer " + self.api_key.get_secret_value()
                    },
                    json=payload,
                    timeout=TIMEOUT,
                    follow_redirects=False,
                )
                if response.status_code == 429:
                    category = "rate_limit"
                elif response.status_code >= 500:
                    category = "server"
                elif response.status_code != 200:
                    category = (
                        "authentication"
                        if response.status_code in (401, 403)
                        else "http_permanent"
                    )
                    retryable = False
                else:
                    completion = Completion.model_validate_json(
                        response.content, strict=True
                    )
                    usage = completion.usage
                    result = StructuredRiskFindings.model_validate_json(
                        completion.choices[0].message.content, strict=True
                    )
            except httpx.TimeoutException:
                category = "timeout"
            except httpx.TransportError:
                category = "network"
            except httpx.DecodingError:
                category = "structured_output"
            except (ValidationError, ValueError):
                category = "structured_output"
            # Finish the HTTP attempt before opening the independent audit transaction.
            try:
                await self.recorder(
                    InvocationAttempt(
                        run_id=self.run_id,
                        logical_call_id=logical_call_id,
                        attempt_number=attempt_number,
                        model=self.model,
                        prompt_version=PROMPT_VERSION,
                        input_tokens=usage.prompt_tokens if usage else None,
                        output_tokens=usage.completion_tokens if usage else None,
                        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                        status="succeeded" if result is not None else "failed",
                        error_category=category,
                    )
                )
            except Exception:
                raise ModelAdapterError("Model invocation recorder failed") from None
            if result is not None:
                return result
            if not retryable or attempt_number == 2:
                raise ModelAdapterError(f"Model invocation failed: {category}")
        raise AssertionError("Unreachable attempt")
