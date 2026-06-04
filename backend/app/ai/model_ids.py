from __future__ import annotations


GROQ_MODEL_ALIASES = {
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
}


def normalize_groq_model(model: str | None, default: str = "llama-3.3-70b-versatile") -> str:
    value = (model or "").strip()
    if not value:
        return default
    return GROQ_MODEL_ALIASES.get(value, value)
