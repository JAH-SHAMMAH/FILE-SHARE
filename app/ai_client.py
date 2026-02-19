import os
import httpx


def get_ai_provider() -> str:
    provider = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if provider:
        return provider
    # default: use OpenAI if key is present, otherwise local
    return "openai" if os.getenv("OPENAI_API_KEY") else "ollama"


def chat_completion(messages, model: str | None = None, max_tokens: int = 800, temperature: float = 0.7) -> str:
    provider = get_ai_provider()

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OpenAI key is not configured")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        data = {
            "model": model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
            r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI error: {r.status_code} - {r.text}")
            j = r.json()
            return j.get("choices", [{}])[0].get("message", {}).get("content", "")

    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        data = {
            "model": model or os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
            r = client.post(f"{base_url}/api/chat", json=data)
            if r.status_code != 200:
                raise RuntimeError(f"Ollama error: {r.status_code} - {r.text}")
            j = r.json()
            return (j.get("message") or {}).get("content", "")

    raise RuntimeError(f"Unknown AI provider: {provider}")
