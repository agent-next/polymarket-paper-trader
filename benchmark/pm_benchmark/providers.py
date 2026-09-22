"""LLM provider abstraction via litellm."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import litellm

from pm_benchmark.config import LLMConfig
from pm_benchmark.jev import (
    JevError,
    choice_value,
    is_jev_model,
    noul_probability,
    query_jev,
)

# Suppress litellm's noisy logging
litellm.suppress_debug_info = True

VALID_ACTIONS = ("buy_yes", "buy_no", "skip")
VALID_CONFIDENCES = ("low", "medium", "high")

# Jev (TypeSafe System One) asks typed questions instead of emitting JSON text.
# The size choice is mapped to a fraction of this notional cap; deterministic
# position sizing in agent mode remains the runner's job.
JEV_MAX_AMOUNT_USD = 500.0
JEV_SIZE_FRACTIONS = {"none": 0.0, "small": 0.25, "medium": 0.5, "large": 1.0}

JEV_QUESTIONS: dict[str, dict] = {
    "probability": {
        "type": "noul",
        "instructions": "Will this market resolve YES?",
    },
    "action": {
        "type": "choice",
        "instructions": "What trade should be placed on this market?",
        "criteria": {
            "buy_yes": "The YES outcome is underpriced.",
            "buy_no": "The NO outcome is underpriced.",
            "skip": "No edge, or insufficient information.",
        },
    },
    "confidence": {
        "type": "choice",
        "instructions": "How confident is this decision?",
        "criteria": {"low": "Weak signal.", "medium": "Moderate.", "high": "Strong."},
    },
    "size": {
        "type": "choice",
        "instructions": "How large should the position be?",
        "criteria": {
            "none": "No position.",
            "small": "Small position.",
            "medium": "Medium position.",
            "large": "Large position.",
        },
    },
}


def _query_jev_decision(
    llm_config: LLMConfig,
    prompt: str,
    system_prompt: str,
    *,
    timeout: float | None,
) -> str:
    """Ask Jev typed questions and return a decision as JSON text.

    Reuses the same JSON envelope the litellm path produces so downstream
    parsing stays identical.
    """
    state = f"{system_prompt}\n\n{prompt}"
    try:
        answers = query_jev(
            llm_config.model,
            state,
            JEV_QUESTIONS,
            timeout=timeout if timeout is not None else 30.0,
        )
        probability = noul_probability(answers, "probability")
        action = choice_value(answers, "action")
        confidence = choice_value(answers, "confidence")
        size = choice_value(answers, "size")
    except JevError as e:
        raise LLMError(f"Jev call failed: {e}") from e

    amount_usd = JEV_MAX_AMOUNT_USD * JEV_SIZE_FRACTIONS.get(size, 0.0)
    if action != "skip" and amount_usd <= 0:
        action = "skip"

    return json.dumps(
        {
            "probability": probability,
            "action": action,
            "confidence": confidence,
            "amount_usd": amount_usd if action != "skip" else 0.0,
            "reasoning": f"Jev {llm_config.model}",
        }
    )


class LLMError(Exception):
    """Raised on LLM call or parsing failures."""


@dataclass(frozen=True)
class MarketDecision:
    """Parsed LLM trading decision."""

    probability: float
    action: str
    confidence: str
    amount_usd: float
    reasoning: str


def query_model(
    llm_config: LLMConfig,
    prompt: str,
    system_prompt: str,
    *,
    timeout: float | None = None,
    seed: int | None = None,
) -> str:
    """Call LLM via litellm and return raw text response.

    Args:
        llm_config: LLM configuration.
        prompt: User prompt.
        system_prompt: System prompt.

    Returns:
        Raw text response from the model.
    """
    if is_jev_model(llm_config.model):
        return _query_jev_decision(llm_config, prompt, system_prompt, timeout=timeout)

    # Set API key from env if configured
    if llm_config.api_key_env:
        api_key = os.environ.get(llm_config.api_key_env)
        if not api_key:
            raise LLMError(f"Environment variable {llm_config.api_key_env} not set")
    else:
        api_key = None

    try:
        completion_kwargs = {
            "model": llm_config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": llm_config.temperature,
            "max_tokens": llm_config.max_tokens,
            "api_key": api_key,
        }
        if timeout is not None:
            completion_kwargs["timeout"] = timeout
        if seed is not None:
            completion_kwargs["seed"] = seed

        response = litellm.completion(
            **completion_kwargs,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        raise LLMError(f"LLM call failed: {e}") from e


def parse_decision(raw_response: str) -> MarketDecision:
    """Parse a raw LLM response into a MarketDecision.

    Extracts JSON from the response, handling markdown code blocks.
    """
    text = raw_response.strip()

    # Strip markdown code fences if present
    if "```" in text:
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines).strip()

    # Try to find JSON object in text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"No JSON object found in response: {raw_response[:200]}")

    text = text[start:end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON in response: {e}") from e

    return _validate_decision(data)


def _validate_decision(data: dict) -> MarketDecision:
    """Validate and construct a MarketDecision from parsed JSON."""
    probability = data.get("probability")
    if probability is None:
        raise LLMError("Missing 'probability' in response")
    probability = float(probability)
    if not 0.0 <= probability <= 1.0:
        raise LLMError(f"probability must be 0.0-1.0, got {probability}")

    action = data.get("action", "skip")
    if action not in VALID_ACTIONS:
        raise LLMError(f"Invalid action '{action}', must be one of {VALID_ACTIONS}")

    confidence = data.get("confidence", "medium")
    if confidence not in VALID_CONFIDENCES:
        raise LLMError(
            f"Invalid confidence '{confidence}', must be one of {VALID_CONFIDENCES}"
        )

    amount_usd = float(data.get("amount_usd", 0))
    if action != "skip" and amount_usd <= 0:
        raise LLMError(f"amount_usd must be positive for action '{action}'")

    reasoning = data.get("reasoning", "")

    return MarketDecision(
        probability=probability,
        action=action,
        confidence=confidence,
        amount_usd=amount_usd,
        reasoning=reasoning,
    )
