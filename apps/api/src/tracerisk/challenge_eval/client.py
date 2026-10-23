"""One DeepSeek HTTP attempt; retry and validation budgets belong to the runner."""

import time
from collections.abc import Sequence
from typing import Literal

import httpx
from pydantic import ConfigDict, Field, SecretStr

from tracerisk.ai.schemas import Key
from tracerisk.challenge_eval.schemas import StrictModel

TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class DisabledThinking(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["disabled"]


class JsonObjectResponseFormat(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["json_object"]


class DeepSeekRequestSettings(StrictModel):
    """The request settings selected from the checked-in evaluation config."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    base_url: str
    model: Literal["deepseek-flash"]
    thinking: DisabledThinking
    temperature: Literal[0]
    max_tokens: Literal[2000]
    response_format: JsonObjectResponseFormat


class CompletionAttempt(StrictModel):
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded", "failed"]
    retryable: bool
    requested_model: Key
    request_parameters: dict[str, object]
    http_status: int | None = None
    error_category: Key | None = None
    returned_model: Key | None = None
    system_fingerprint: Key | None = None
    finish_reason: Key | None = None
    response_content: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_hit_tokens: int | None = Field(default=None, ge=0)
    cache_miss_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class DeepSeekClient:
    def __init__(
        self,
        *,
        settings: DeepSeekRequestSettings,
        api_key: SecretStr,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.api_key = api_key
        self.client = client

    async def complete_once(
        self, messages: Sequence[dict[str, str]]
    ) -> CompletionAttempt:
        parameters = self.settings.model_dump(exclude={"base_url"})
        started = time.monotonic()
        attempt = CompletionAttempt(
            latency_ms=0,
            status="failed",
            retryable=False,
            requested_model=self.settings.model,
            request_parameters=parameters,
        )
        try:
            if self.client is None:
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await self._post(client, messages, parameters)
            else:
                response = await self._post(self.client, messages, parameters)
            attempt.http_status = response.status_code
            if response.status_code == 429:
                attempt.error_category, attempt.retryable = "rate_limit", True
            elif 500 <= response.status_code < 600:
                attempt.error_category, attempt.retryable = "server", True
            elif response.status_code != 200:
                attempt.error_category = (
                    "authentication"
                    if response.status_code in (401, 403)
                    else "http_permanent"
                )
            else:
                self._parse_response(response, attempt)
        except httpx.TimeoutException:
            attempt.error_category, attempt.retryable = "timeout", True
        except httpx.DecodingError:
            attempt.error_category = "malformed_response"
        except httpx.TransportError:
            attempt.error_category, attempt.retryable = "network", True
        finally:
            attempt.latency_ms = max(0, int((time.monotonic() - started) * 1000))
        return attempt

    async def _post(
        self,
        client: httpx.AsyncClient,
        messages: Sequence[dict[str, str]],
        parameters: dict[str, object],
    ) -> httpx.Response:
        return await client.post(
            self.settings.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + self.api_key.get_secret_value()},
            json={**parameters, "messages": list(messages)},
            timeout=TIMEOUT,
            follow_redirects=False,
        )

    def _safe_text(self, value: str) -> str:
        secret = self.api_key.get_secret_value()
        return value.replace(secret, "[REDACTED]") if secret else value

    def _parse_response(
        self, response: httpx.Response, attempt: CompletionAttempt
    ) -> None:
        attempt.error_category = "malformed_response"
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return
        if not isinstance(body, dict):
            return
        malformed = False
        for source, target in (
            ("model", "returned_model"),
            ("system_fingerprint", "system_fingerprint"),
        ):
            value = body.get(source)
            if value is not None:
                if isinstance(value, str) and 0 < len(value) <= 256 and value.strip():
                    setattr(attempt, target, self._safe_text(value))
                else:
                    malformed = True
        usage = body.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                malformed = True
            else:
                for source, target in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                    ("total_tokens", "total_tokens"),
                    ("prompt_cache_hit_tokens", "cache_hit_tokens"),
                    ("prompt_cache_miss_tokens", "cache_miss_tokens"),
                ):
                    value = usage.get(source)
                    if value is not None:
                        if type(value) is int and value >= 0:
                            setattr(attempt, target, value)
                        else:
                            malformed = True
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            return
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and 0 < len(reason) <= 256 and reason.strip():
            attempt.finish_reason = self._safe_text(reason)
        else:
            malformed = True
        message = choice.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if isinstance(content, str):
            attempt.response_content = self._safe_text(content)
        elif content is not None or "content" not in message:
            malformed = True
        if malformed:
            return
        if reason != "stop":
            attempt.error_category = "non_stop_finish"
        elif content is None:
            attempt.error_category = "null_content"
        else:
            attempt.error_category = None
            attempt.status = "succeeded"
