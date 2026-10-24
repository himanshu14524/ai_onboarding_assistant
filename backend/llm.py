"""LLM access. Works with Anthropic or OpenAI keys; returns None if neither is set
(the app then falls back to rule-based extraction / extractive answers)."""
import json
import os
import re
from functools import lru_cache

from .config import ANTHROPIC_MODEL, LLM_BASE_URL, LLM_MODEL, LLM_VERIFY_SSL, OPENAI_MODEL


@lru_cache(maxsize=1)
def get_llm():
    try:
        if os.getenv("LLM_API_KEY"):  # OpenAI-compatible LiteLLM proxy (e.g. DeepSeek-V3)
            import httpx
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                base_url=LLM_BASE_URL,
                model=LLM_MODEL,
                api_key=os.environ["LLM_API_KEY"],
                http_client=httpx.Client(verify=LLM_VERIFY_SSL),
                temperature=0.1,
                max_tokens=1500,
            )
        if os.getenv("ANTHROPIC_API_KEY"):
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=ANTHROPIC_MODEL, temperature=0.1, max_tokens=1500)
        if os.getenv("OPENAI_API_KEY"):
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=OPENAI_MODEL, temperature=0.1)
    except Exception as exc:  # missing package etc.
        print(f"[llm] disabled: {exc}")
    return None


def llm_name() -> str:
    if os.getenv("LLM_API_KEY"):
        return f"proxy:{LLM_MODEL}"
    if os.getenv("ANTHROPIC_API_KEY"):
        return f"anthropic:{ANTHROPIC_MODEL}"
    if os.getenv("OPENAI_API_KEY"):
        return f"openai:{OPENAI_MODEL}"
    return "none (rule-based fallback)"


def text_of(msg) -> str:
    c = getattr(msg, "content", msg)
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return str(c)


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None
