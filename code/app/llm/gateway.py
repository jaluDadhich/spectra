"""Model gateway abstraction (spec 6.2).

One reusable client behind an interface so another provider can be added
without rewriting domain logic. Retries are bounded with exponential
backoff + jitter and apply only to retryable failures. Secrets are never
logged or exposed to the browser.
"""
from __future__ import annotations

import logging
import json
import random
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from app.config import Settings

logger = logging.getLogger("spectra.llm")


def _estimate_tokens(text: str) -> int:
    """Conservative local token estimate without requiring a tokenizer package."""
    byte_estimate = math.ceil(len(text.encode("utf-8")) / 3.0)
    lexical_estimate = sum(
        max(1, math.ceil(len(piece) / 4.0)) if piece.isalnum() or "_" in piece else 1
        for piece in re.findall(r"[A-Za-z0-9_]+|[^\w\s]", text)
    )
    return max(byte_estimate, lexical_estimate)


def estimate_request_tokens(system_prompt: str, user_prompt: str,
                            json_schema: Dict[str, Any]) -> int:
    material = (system_prompt + user_prompt
                + json.dumps(json_schema, ensure_ascii=False))
    return _estimate_tokens(material) + 256  # framing/tool metadata allowance


def _safe_exception_diagnostics(error: BaseException) -> Dict[str, Any]:
    """Summarize a provider/transport exception without logging credentials.

    SDK connection exceptions often wrap the useful socket, TLS, or DNS error
    several levels down. Keep a short type chain and the deepest available
    message/code so a failed pipeline stage can be diagnosed from local logs.
    """
    chain: List[BaseException] = []
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(current)
        nested = current.__cause__ or current.__context__
        if nested is None:
            reason = getattr(current, "reason", None)
            nested = reason if isinstance(reason, BaseException) else None
        current = nested

    root = chain[-1] if chain else error
    message = " ".join(str(root).split())
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", message)
    message = re.sub(r"\b(?:sk|rk)-[A-Za-z0-9_-]{8,}\b", "[REDACTED_KEY]", message)
    message = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", message)
    # Keep a host useful for diagnosis but omit URL credentials, paths and query values.
    message = re.sub(
        r"(?i)(https?://)(?:[^/@\s]+@)?([^/\s?#]+)(?:[^\s]*)",
        r"\1\2/[REDACTED_PATH]", message)
    message = message[:240] or type(root).__name__

    code_owner = next(
        (item for item in reversed(chain)
         if getattr(item, "winerror", None) is not None
         or getattr(item, "errno", None) is not None),
        None,
    )
    code = (getattr(code_owner, "winerror", None)
            if code_owner is not None else None)
    if code is None and code_owner is not None:
        code = getattr(code_owner, "errno", None)
    return {
        "exception_type": type(error).__name__,
        "cause_chain": ">".join(type(item).__name__ for item in chain),
        "root_cause": type(root).__name__,
        "root_message": message,
        "error_code": code if code is not None else "none",
        "http_status": getattr(error, "status_code", "none"),
        "request_id": getattr(error, "request_id", None) or "none",
    }


class LLMError(Exception):
    """Non-retryable or exhausted LLM failure."""

    def __init__(self, message: str, category: str = "service"):
        super().__init__(message)
        self.category = category  # auth | rate_limit | network | invalid | context | service


@dataclass
class LLMResponse:
    content: Dict[str, Any]
    model: str
    request_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    retries: int = 0


class ModelGateway(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str,
                      json_schema: Dict[str, Any], schema_name: str) -> LLMResponse:
        ...


class OpenAIGateway:
    def __init__(self, settings: Settings):
        if not settings.llm_available:
            raise LLMError("OpenAI gateway requires SPECTRA_LLM_ENABLED=true and "
                           "OPENAI_API_KEY", category="auth")
        try:
            from openai import OpenAI  # imported lazily so tests need no key

            kwargs: Dict[str, Any] = {
                "api_key": settings.openai_api_key.get_secret_value(),
                "timeout": settings.openai_timeout_seconds,
                "max_retries": 0,  # retries handled explicitly below
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = OpenAI(**kwargs)
        except Exception as exc:
            raise LLMError(
                f"Could not initialize OpenAI client: {type(exc).__name__}",
                category="service") from exc
        self._settings = settings

    def complete_json(self, system_prompt: str, user_prompt: str,
                      json_schema: Dict[str, Any], schema_name: str) -> LLMResponse:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            RateLimitError,
        )

        attempt, retries = 0, 0
        while True:
            started = time.monotonic()
            budget_retry_usage = None
            try:
                is_fast_task = schema_name in {
                    "finding_explanation", "document_triage",
                    "quality_severity_scoring",
                }
                params: Dict[str, Any] = {
                    # Full cross-document review needs the stronger model.
                    # Short explanation/triage tasks use the low-cost model.
                    "model": (
                        (self._settings.openai_fast_model or self._settings.openai_model)
                        if is_fast_task
                        else (self._settings.openai_reasoning_model or self._settings.openai_model)
                    ),
                    "reasoning_effort": (
                        self._settings.openai_fast_reasoning_effort
                        if is_fast_task else self._settings.openai_reasoning_effort
                    ),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": json_schema,
                        },
                    },
                }
                model_name = params["model"]
                # Keep every provider request under the configured 25k-token
                # ceiling. Estimate conservatively at 3 UTF-8 bytes/token and
                # include schema and message framing; leave completion room
                # rather than silently sending an oversized context.
                estimated_input_tokens = estimate_request_tokens(
                    system_prompt, user_prompt, json_schema)
                request_token_limit = self._settings.openai_max_request_tokens
                completion_room = request_token_limit - estimated_input_tokens
                if completion_room < 1_000:
                    raise LLMError(
                        "Estimated prompt exceeds the per-request token budget; "
                        "reduce evidence or candidate batch size",
                        category="context")
                if schema_name == "scope_adjudication":
                    output_limit = self._settings.openai_scope_max_output_tokens
                elif schema_name == "document_triage":
                    # Specialist agents may summarize multiple correlated
                    # records. Give their bounded discovery schema more room;
                    # the 25k request ceiling below still caps each call.
                    output_limit = max(self._settings.openai_max_output_tokens, 4_000)
                elif is_fast_task:
                    output_limit = self._settings.openai_max_output_tokens
                else:
                    output_limit = max(self._settings.openai_max_output_tokens,
                                       self._settings.openai_reasoning_max_output_tokens)
                output_limit = min(output_limit, completion_room)
                if output_limit:
                    params["max_completion_tokens"] = output_limit
                if self._settings.openai_temperature is not None:
                    params["temperature"] = self._settings.openai_temperature
                request_timeout = (
                    self._settings.openai_scope_timeout_seconds
                    if schema_name == "scope_adjudication"
                    else self._settings.openai_timeout_seconds)
                logger.info("event=llm_call_started task=%s model=%s attempt=%d timeout_seconds=%.1f estimated_input_tokens=%d output_token_limit=%d request_token_limit=%d",
                            schema_name, model_name, attempt + 1, request_timeout,
                            estimated_input_tokens, output_limit, request_token_limit)
                client = self._client.with_options(timeout=request_timeout)
                response = client.chat.completions.create(**params)
                latency = (time.monotonic() - started) * 1000.0
                choice = response.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason == "length" and output_limit < completion_room:
                    # A truncated strict-JSON response cannot be salvaged.
                    # Retry once with the remaining request budget, rather
                    # than immediately failing the whole agent graph.
                    budget_retry_usage = response.usage
                    expanded_limit = completion_room
                    logger.warning(
                        "event=llm_call_retry task=%s category=context reason=completion_length "
                        "previous_output_limit=%d expanded_output_limit=%d",
                        schema_name, output_limit, expanded_limit)
                    params["max_completion_tokens"] = expanded_limit
                    logger.info(
                        "event=llm_call_started task=%s model=%s attempt=%d timeout_seconds=%.1f "
                        "estimated_input_tokens=%d output_token_limit=%d request_token_limit=%d",
                        schema_name, model_name, attempt + 2, request_timeout,
                        estimated_input_tokens, expanded_limit, request_token_limit)
                    retry_started = time.monotonic()
                    response = client.chat.completions.create(**params)
                    latency += (time.monotonic() - retry_started) * 1000.0
                    choice = response.choices[0]
                    finish_reason = getattr(choice, "finish_reason", None)
                    retries += 1
                if finish_reason == "length":
                    raise LLMError("Structured response exceeded its allocated completion budget",
                                   category="context")
                if getattr(choice.message, "refusal", None):
                    raise LLMError("Model refused the request", category="invalid")
                raw_content = choice.message.content
                if not raw_content:
                    finish_reason = getattr(choice, "finish_reason", "unknown")
                    raise LLMError(
                        f"Model returned no structured JSON content "
                        f"(finish_reason={finish_reason})", category="invalid")
                content = json.loads(raw_content)
                usage = response.usage
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0
                if budget_retry_usage is not None:
                    input_tokens += getattr(budget_retry_usage, "prompt_tokens", 0) or 0
                    output_tokens += getattr(budget_retry_usage, "completion_tokens", 0) or 0
                output = LLMResponse(
                    content=content,
                    model=response.model,
                    request_id=getattr(response, "id", None),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency,
                    retries=retries,
                )
                logger.info("event=llm_call_completed task=%s model=%s request_id=%s latency_ms=%.1f input_tokens=%d output_tokens=%d retries=%d",
                            schema_name, output.model, output.request_id or "unknown",
                            output.latency_ms, output.input_tokens,
                            output.output_tokens, output.retries)
                return output
            except LLMError as exc:
                diagnostics = _safe_exception_diagnostics(exc.__cause__ or exc)
                logger.error(
                    "event=llm_call_failed task=%s category=%s attempt=%d retryable=false "
                    "exception_type=%s cause_chain=%s root_cause=%s error_code=%s "
                    "http_status=%s request_id=%s root_message=%r",
                    schema_name, exc.category, attempt + 1,
                    diagnostics["exception_type"], diagnostics["cause_chain"],
                    diagnostics["root_cause"], diagnostics["error_code"],
                    diagnostics["http_status"], diagnostics["request_id"],
                    diagnostics["root_message"],
                )
                raise
            except RateLimitError as exc:
                retryable, category = True, "rate_limit"
                err = exc
            except APITimeoutError as exc:
                retryable, category = True, "timeout"
                err = exc
            except APIConnectionError as exc:
                retryable, category = True, "network"
                err = exc
            except APIStatusError as exc:
                status = getattr(exc, "status_code", 500)
                if status in (401, 403):
                    raise LLMError("OpenAI authentication failed", category="auth") from exc
                if status == 400:
                    raise LLMError(f"Invalid request: {exc}", category="invalid") from exc
                if status == 413 or "context" in str(exc).lower():
                    raise LLMError(f"Context length error: {exc}", category="context") from exc
                retryable = status in (408, 409, 425, 429) or status >= 500
                category = "rate_limit" if status == 429 else (
                    "timeout" if status == 408 else "service")
                err = exc
            except ValueError as exc:  # JSON decode of model output
                raise LLMError(f"Invalid structured output: {exc}", category="invalid") from exc
            except Exception as exc:
                # SDK/provider variants occasionally raise errors outside the
                # narrow transport classes. Convert them to a safe, bounded
                # failure so the caller can record it and enter deterministic
                # fallback instead of crashing the review worker.
                logger.exception("event=llm_call_unexpected_error task=%s attempt=%d",
                                 schema_name, attempt + 1)
                raise LLMError(
                    f"Unexpected provider error: {type(exc).__name__}",
                    category="service") from exc

            if not retryable or attempt >= self._settings.openai_max_retries:
                diagnostics = _safe_exception_diagnostics(err)
                logger.error(
                    "event=llm_call_failed task=%s category=%s attempt=%d retryable=%s "
                    "exception_type=%s cause_chain=%s root_cause=%s error_code=%s "
                    "http_status=%s request_id=%s root_message=%r",
                    schema_name, category, attempt + 1, retryable,
                    diagnostics["exception_type"], diagnostics["cause_chain"],
                    diagnostics["root_cause"], diagnostics["error_code"],
                    diagnostics["http_status"], diagnostics["request_id"],
                    diagnostics["root_message"],
                )
                raise LLMError(f"OpenAI call failed ({category}): {err}", category=category) from err
            attempt += 1
            retries += 1
            # Full jitter over an exponential ceiling avoids synchronized
            # retry waves across concurrent account runs.
            ceiling = min(2 ** attempt, 30)
            backoff = random.uniform(0.25, max(0.25, ceiling))
            diagnostics = _safe_exception_diagnostics(err)
            logger.warning(
                "event=llm_call_retry task=%s category=%s retry=%d delay_seconds=%.1f "
                "exception_type=%s cause_chain=%s root_cause=%s error_code=%s "
                "http_status=%s request_id=%s root_message=%r",
                schema_name, category, attempt, backoff,
                diagnostics["exception_type"], diagnostics["cause_chain"],
                diagnostics["root_cause"], diagnostics["error_code"],
                diagnostics["http_status"], diagnostics["request_id"],
                diagnostics["root_message"],
            )
            time.sleep(backoff)


class MockGateway:
    """Deterministic gateway for tests and deterministic-only runs.

    Returns schema-valid empty candidate sets (or scripted responses), and
    can be configured to misbehave (bad evidence IDs) to exercise
    validation paths. Never calls a network.
    """

    def __init__(self, scripted: Optional[List[Dict[str, Any]]] = None):
        self.scripted = list(scripted or [])
        self.calls: List[Dict[str, Any]] = []

    def complete_json(self, system_prompt: str, user_prompt: str,
                      json_schema: Dict[str, Any], schema_name: str) -> LLMResponse:
        self.calls.append({"system": system_prompt, "user": user_prompt,
                           "schema": schema_name})
        if self.scripted:
            content = self.scripted.pop(0)
        elif schema_name == "finding_explanation":
            content = {"explanation": "Mock explanation (deterministic-only mode)."}
        else:
            content = {"task_id": "mock", "candidate_findings": [],
                       "no_issue_reason": "Mock gateway: no semantic analysis performed"}
        return LLMResponse(content=content, model="mock-gateway",
                           input_tokens=len(user_prompt) // 4,
                           output_tokens=len(str(content)) // 4)


def get_gateway(settings: Settings, deterministic_only: bool = False) -> Optional[ModelGateway]:
    """Returns None when GPT-assisted review is disabled/unavailable; the
    caller must then mark semantic checks incomplete rather than pretend
    they ran."""
    if deterministic_only or not settings.llm_available:
        return None
    return OpenAIGateway(settings)
