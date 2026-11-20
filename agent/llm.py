"""
Thin client for Sarvam's API: /v2/chat/completions hosts both Deepseek (generic
reasoning) and Sarvam's own conversational model; /v2/systemone is a separate,
differently-shaped structured-decision endpoint (Prism).

Plain `requests` calls, not the OpenAI/Anthropic SDKs -- Sarvam authenticates with
a non-standard header (api-subscription-key, not Authorization: Bearer), and these
are two simple POST endpoints, not worth forcing through an SDK built around a
different API shape.
"""
import json
import re

import requests

from .config import (
    SARVAM_API_KEY,
    SARVAM_BASE_URL,
    SARVAM_MODEL_CONVERSATIONAL,
    SARVAM_MODEL_DECISION,
    SARVAM_MODEL_REASONING,
)


def _headers():
    return {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}


def _require_key():
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not set -- put the real key in .env")


def _chat(model, messages, temperature=1, top_p=0.95, max_tokens=4096, reasoning_effort=None):
    _require_key()
    body = {"model": model, "messages": messages, "temperature": temperature,
            "top_p": top_p, "max_tokens": max_tokens}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    r = requests.post(f"{SARVAM_BASE_URL}/chat/completions", headers=_headers(), json=body, timeout=120)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content")
    if content is None:
        # Reasoning models spend max_tokens on "reasoning_content" (internal chain
        # of thought) before writing the final answer -- if the budget runs out
        # mid-thought, content comes back null with finish_reason "length". Fail
        # loudly rather than crash obscurely on .strip() of None.
        raise RuntimeError(
            "Model returned no content (finish_reason="
            f"{r.json()['choices'][0].get('finish_reason')!r}) -- it likely ran out of "
            "max_tokens while reasoning. Increase max_tokens and retry."
        )
    return content


def call_with_tools(messages, tools, reasoning_effort="low", max_tokens=16000, **kw):
    """Like call_reasoning(), but returns the raw assistant message (not just
    .content) so the caller can see tool_calls / finish_reason. Used only by
    agent/decompose_agent.py's tool-use loop -- the two existing call sites
    (decompose.py, letter.py) stay on call_reasoning(), untouched, since they
    never need a tool_calls turn and raising loudly on null content there is
    exactly the right behavior for them."""
    _require_key()
    body = {"model": SARVAM_MODEL_REASONING, "messages": messages, "tools": tools,
            "reasoning_effort": reasoning_effort, "max_tokens": max_tokens, **kw}
    r = requests.post(f"{SARVAM_BASE_URL}/chat/completions", headers=_headers(), json=body, timeout=120)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    return choice["message"], choice.get("finish_reason")


def call_reasoning(messages, reasoning_effort="low", max_tokens=16000, **kw):
    """Deepseek -- generic reasoning. Used by decompose.py (request -> checks.json).
    Higher max_tokens default than the base _chat(): this is a reasoning model whose
    internal "thinking" tokens count against the same budget as the final answer, and
    4096 was observed to be entirely consumed by reasoning before any output."""
    return _chat(SARVAM_MODEL_REASONING, messages, reasoning_effort=reasoning_effort, max_tokens=max_tokens, **kw)


def call_conversational(messages, **kw):
    """Sarvam's own conversational model -- used by transcript_review.py.
    Our transcripts are Hindi/English/regional code-mixed call conversations,
    which is this model's specialty, not Deepseek's."""
    return _chat(SARVAM_MODEL_CONVERSATIONAL, messages, **kw)


def call_decision(state: str, questions: dict) -> dict:
    """Sarvam Prism -- structured decision making (/v2/systemone). Different wire
    shape from chat completions: you describe a situation (`state`) and ask typed
    questions (bool/choice/score) about it. Not wired into anything yet -- scaffolded
    for severity grading / routing if we decide to use it there. Must NOT be used
    for the actual per-account GAP/EVIDENCED verdict -- that stays deterministic
    code, same reason as always: reproducibility, auditability, and the brief's own
    penalty for wrapping a deterministic comparison in a model."""
    _require_key()
    body = {"model": SARVAM_MODEL_DECISION, "state": state, "questions": questions}
    r = requests.post(f"{SARVAM_BASE_URL}/systemone", headers=_headers(), json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```json fences or add a sentence before/after
    it. Strip that and parse the first {...} block. Raises if nothing parses --
    callers should treat that as a hard failure, not guess at partial output."""
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in model output: {text[:200]!r}")
    return json.loads(m.group(0))
