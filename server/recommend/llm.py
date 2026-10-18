"""A language model, for the language parts: naming taste contexts and turning requests into filters.

Works with OpenAI or Claude, whichever has credentials: OPENAI_API_KEY, or ANTHROPIC_API_KEY (or an `ant auth login`
profile). With both, OpenAI is used unless LLM_PROVIDER=anthropic. OPENAI_MODEL picks the OpenAI model.

Everything here is optional. Without credentials callers get None and fall back to rule-based behaviour.
"""

import logging
import os
from pathlib import Path

MODEL = "claude-opus-5-5"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")  # structured output, cheap and quick for parsing

logger = logging.getLogger(__name__)


def provider():
    """"openai", "anthropic", or None when neither is configured."""
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_anthropic = any(os.environ.get(n) for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) or (Path.home() / ".config" / "anthropic").exists()
    wanted = os.environ.get("LLM_PROVIDER", "").lower()
    if wanted == "anthropic" and has_anthropic:
        return "anthropic"
    if has_openai:
        return "openai"
    return "anthropic" if has_anthropic else None


def available():
    return provider() is not None


def parse(system, prompt, output_format, effort="low"):
    """A validated `output_format` instance, or None when no model is configured, it declines, or it errors."""
    which = provider()
    if which == "openai":
        return _openai(system, prompt, output_format)
    if which == "anthropic":
        return _anthropic(system, prompt, output_format, effort)
    return None


def _openai(system, prompt, output_format):
    import openai

    try:
        completion = openai.OpenAI().chat.completions.parse(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_format=output_format,
            temperature=0,  # the same request should parse the same way every time
        )
    except openai.APIConnectionError:
        logger.warning("Couldn't reach the OpenAI API; using the fallback")
        return None
    except openai.APIStatusError as error:
        logger.warning("OpenAI API error %s; using the fallback", error.status_code)
        return None
    except openai.LengthFinishReasonError:
        logger.warning("OpenAI ran out of room for the answer; using the fallback")
        return None
    message = completion.choices[0].message
    if message.refusal:
        logger.warning("OpenAI declined the request; using the fallback")
        return None
    return message.parsed


def _anthropic(system, prompt, output_format, effort):
    """Short structured tasks, so effort is low. The system prompt is cached: it's the stable part across requests.
    Refusal fallbacks are on, so a declined request is retried on another model in the same call."""
    import anthropic

    try:
        response = anthropic.Anthropic().beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError:
        logger.warning("Couldn't reach the Claude API; using the fallback")
        return None
    except anthropic.APIStatusError as error:
        logger.warning("Claude API error %s; using the fallback", error.status_code)
        return None
    if response.stop_reason == "refusal":
        logger.warning("Claude declined the request; using the fallback")
        return None
    return response.parsed_output
