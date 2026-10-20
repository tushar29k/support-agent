"""Free-tier LLM client. stdlib only — no new deps, keeps Render light.

Env:
  LLM_PROVIDER  gemini (default) | openrouter
  LLM_MODEL     override the default model for the provider
  LLM_API_KEY   the key. FreeLLMClient.from_env() returns None without it,
                so callers keep their mock path with zero behavior change.

Resilience: 20s timeout, one retry with backoff on 429/5xx, then
FreeLLMError whose message never contains the key.
"""
import json
import os
import time
import urllib.request
import urllib.error


class FreeLLMError(RuntimeError):
    pass


_TIMEOUT = 20
_BACKOFF = 1.5

_DEFAULTS = {
    "gemini": "gemini-3.8-flash",
    "openrouter": "openai/gpt-oss-20b:free",
}


class FreeLLMClient:
    def __init__(self, provider=None, model=None, api_key=None):
        self.provider = (provider or os.environ.get("LLM_PROVIDER",
                                                     "gemini")).lower()
        if self.provider not in _DEFAULTS:
            raise FreeLLMError(f"unknown LLM_PROVIDER '{self.provider}'")
        self.model = model or os.environ.get("LLM_MODEL") or \
            _DEFAULTS[self.provider]
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        if not self.api_key:
            raise FreeLLMError("no LLM_API_KEY in the environment")

    @classmethod
    def from_env(cls):
        # None = no key configured → caller stays on its mock path
        if not os.environ.get("LLM_API_KEY"):
            return None
        return cls()

    @property
    def label(self):
        return f"{self.provider}:{self.model}"

    def generate(self, prompt, max_tokens=256, temperature=0.7):
        url = self._url()
        body = self._body(prompt, max_tokens, temperature)
        last = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, method="POST",
                                             data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                    return self._parse(json.loads(r.read().decode()))
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in (429, 500, 502, 503, 504) or attempt == 1:
                    raise FreeLLMError(f"provider http {e.code}") from e
                time.sleep(_BACKOFF * (attempt + 1))
            except Exception as e:  # noqa: BLE001 - timeouts etc
                raise FreeLLMError("provider request failed") from e
        raise FreeLLMError("provider request failed") from last

    def _url(self):
        if self.provider == "gemini":
            return ("https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self.model}:generateContent?key={self.api_key}")
        return "https://openrouter.ai/api/v1/chat/completions"

    def _body(self, prompt, max_tokens, temperature):
        if self.provider == "gemini":
            return {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens,
                                        "temperature": temperature}}
        return {"model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": temperature}

    def _parse(self, data):
        try:
            if self.provider == "gemini":
                return data["candidates"][0]["content"]["parts"][0]["text"]
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise FreeLLMError("unparseable provider response") from e
