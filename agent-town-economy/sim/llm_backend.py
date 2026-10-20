"""OpenAI-compatible LLM backend (stdlib only, no third-party deps).

Works against the OpenAI API or any OpenAI-compatible server -- including a
self-hosted vLLM endpoint, which is what the paper used (Qwen3.8-27B / gpt-oss).
The backend is a ``callable(prompt) -> str`` returning the model's raw text;
``sim.policy.LLMPolicy`` parses and validates it into a tool name, falling back
to the heuristic on any error so a run never crashes on a bad generation
(mirroring the paper's "malformed generations are forfeited, not retried").

Note on reasoning models: models that "think" before answering (Gemini 3.x /
gpt-oss families) spend most of the token budget on internal reasoning, so
``max_tokens`` must be generous or the reply comes back empty
(finish_reason=length, completion_tokens=0) -- exactly the paper's gpt-oss
observation that raising the output budget cut malformed generations sharply.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

SYSTEM_PROMPT = (
    "You are one resident agent in a small tourist-town economy. "
    "On each turn choose exactly ONE action to take. "
    "Reply with ONLY a compact JSON object: {\"tool\": \"<tool_name>\"}. "
    "Do not add prose."
)


class OpenAICompatibleBackend:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    def __call__(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"]
