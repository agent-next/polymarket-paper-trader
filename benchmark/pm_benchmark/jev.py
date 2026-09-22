"""Jev (TypeSafe System One) provider.

Jev is a decision model, not a chat model: it evaluates a ``state`` against a
map of typed questions and returns probabilities/values that callers consume
directly. For prediction-market evaluation we ask a ``noul`` (yes/no) question
per market and use the returned value as the model's probability forecast.

Model ids may carry an optional provider prefix; the API receives the bare id::

    jev-1.13-free            # limited-time free
    jev-1.13                 # paid
    opencode/jev-1.13-free   # prefix stripped
    typesafe/jev-1.13        # prefix stripped

The endpoint defaults to OpenCode Zen's System One route
(``https://opencode.ai/zen/v1/systemone``) and may be overridden with the
``JEV_BASE_URL`` environment variable. Auth is optional on the free model:
``JEV_API_KEY`` or ``OPENCODE_API_KEY`` is sent as a bearer token when present.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://opencode.ai/zen/v1/systemone"
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30.0
RETRY_STATUSES = (429, 529)


class JevError(Exception):
    """Raised on Jev call or response parsing failures."""


def is_jev_model(model: str) -> bool:
    """Return True for a Jev model id, with or without a provider prefix."""
    name = model.rsplit("/", 1)[-1].strip().lower()
    return name == "jev" or name.startswith("jev-")


def _bare_model(model: str) -> str:
    """Strip an optional ``provider/`` prefix from a model id."""
    return model.rsplit("/", 1)[-1].strip()


def query_jev(
    model: str,
    state: str,
    questions: dict[str, dict[str, Any]],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    http_client: httpx.Client | None = None,
) -> dict[str, dict[str, Any]]:
    """Evaluate ``state`` against typed ``questions`` and return the answers map.

    Args:
        model: Jev model id, optionally prefixed (``opencode/jev-1.13-free``).
        state: The text to evaluate (market question and context).
        questions: Map of question id to a System One question object, e.g.
            ``{"probability": {"type": "noul", "instructions": "..."}}``.
        base_url: Endpoint override (defaults to ``JEV_BASE_URL`` or Zen).
        api_key: Bearer token (defaults to ``JEV_API_KEY``/``OPENCODE_API_KEY``).
        timeout: Per-request timeout in seconds.
        max_retries: Attempts before giving up on 429/529 or transport errors.
        http_client: Optional pre-built ``httpx.Client`` (for tests).

    Returns:
        The ``answers`` map from the response, keyed by the supplied question ids.

    Raises:
        JevError: on transport failure, non-retryable HTTP error, or a response
            without an ``answers`` object.
    """
    url = base_url or os.environ.get("JEV_BASE_URL") or DEFAULT_BASE_URL
    key = api_key or os.environ.get("JEV_API_KEY") or os.environ.get("OPENCODE_API_KEY")
    payload = {"model": _bare_model(model), "state": state, "questions": questions}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        last_error: str = "unknown error"
        for attempt in range(max_retries):
            try:
                response = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as e:
                last_error = str(e)
                if attempt + 1 < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError(f"Jev request failed: {e}") from e

            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                if attempt + 1 < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError(f"Jev request failed after retries: {last_error}")

            if response.status_code >= 400:
                raise JevError(f"Jev HTTP {response.status_code}: {response.text[:200]}")

            data = response.json()
            answers = data.get("answers")
            if not isinstance(answers, dict):
                raise JevError(f"Jev response missing 'answers': {str(data)[:200]}")
            return answers

        raise JevError(f"Jev request failed: {last_error}")  # pragma: no cover
    finally:
        if owns_client:
            client.close()


def noul_probability(answers: dict[str, dict[str, Any]], question_id: str) -> float:
    """Extract and validate a ``noul`` probability from an answers map."""
    answer = answers.get(question_id)
    if not isinstance(answer, dict) or "noul" not in answer:
        raise JevError(f"Missing noul answer for question '{question_id}'")
    try:
        value = float(answer["noul"])
    except (TypeError, ValueError) as e:
        raise JevError(f"Non-numeric noul for '{question_id}': {answer['noul']!r}") from e
    if not 0.0 <= value <= 1.0:
        raise JevError(f"noul out of range for '{question_id}': {value}")
    return value


def choice_value(answers: dict[str, dict[str, Any]], question_id: str) -> str:
    """Extract a ``choice`` option from an answers map."""
    answer = answers.get(question_id)
    if not isinstance(answer, dict) or not isinstance(answer.get("choice"), str):
        raise JevError(f"Missing choice answer for question '{question_id}'")
    return answer["choice"]
