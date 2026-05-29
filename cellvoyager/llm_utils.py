from __future__ import annotations

import json
import os
import re

import openai

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_model_provider(model: str | None) -> str:
    if not model:
        return "unknown"
    if model.startswith(("claude-", "anthropic/")):
        return "anthropic"
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith(
        (
            "openai/",
            "google/",
            "qwen/",
            "meta-llama/",
            "deepseek/",
            "mistralai/",
            "gpt-",
            "o1",
            "o3",
            "o4",
        )
    ):
        return "openai"
    return "unknown"


def get_provider_label(provider: str) -> str:
    if provider == "anthropic":
        return "Anthropic"
    if provider == "openai":
        return "OpenAI-compatible"
    if provider == "gemini":
        return "Google Gemini"
    return "Unknown"


def get_openai_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or None


def has_openai_compatible_config(api_key: str | None = None, base_url: str | None = None) -> bool:
    return bool(api_key or os.getenv("OPENAI_API_KEY") or base_url or get_openai_base_url())


def has_provider_config(provider: str) -> bool:
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return has_openai_compatible_config()
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY"))
    return False


def has_any_provider_config() -> bool:
    return any(
        (
            has_openai_compatible_config(),
            bool(os.getenv("ANTHROPIC_API_KEY")),
            bool(os.getenv("GEMINI_API_KEY")),
        )
    )


def get_openai_api_key(api_key: str | None = None, base_url: str | None = None) -> str | None:
    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_base_url = base_url or get_openai_base_url()
    if resolved_key:
        return resolved_key
    if resolved_base_url:
        return "local"
    return None


def get_gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or None


def create_gemini_client() -> openai.OpenAI | None:
    api_key = get_gemini_api_key()
    if not api_key:
        return None
    return openai.OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)


def create_openai_client(api_key: str | None = None, base_url: str | None = None) -> openai.OpenAI | None:
    resolved_base_url = base_url or get_openai_base_url()
    resolved_api_key = get_openai_api_key(api_key=api_key, base_url=resolved_base_url)
    if not resolved_api_key:
        return None

    kwargs = {"api_key": resolved_api_key}
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    return openai.OpenAI(**kwargs)


def extract_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    raise ValueError("No JSON object found in model response")


def create_json_chat_completion(client, model: str, messages: list[dict]):
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if "'response_format.type' must be 'json_schema' or 'text'" not in str(exc):
            raise
        return client.chat.completions.create(
            model=model,
            messages=messages,
        )


def parse_json_response_text(text: str):
    return json.loads(extract_json_text(text))
