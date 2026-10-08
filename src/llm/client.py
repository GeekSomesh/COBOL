"""Minimal client for a local Ollama server (no data leaves the machine)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


class LLMError(RuntimeError):
    pass


@dataclass
class OllamaClient:
    model: str
    url: str = DEFAULT_URL
    temperature: float = 0.0
    seed: int = 7
    num_ctx: int = 4096
    num_predict: int = 400
    timeout: float = 180
    calls: int = field(default=0, init=False)
    seconds: float = field(default=0.0, init=False)

    def chat(self, messages: list[dict[str, str]], schema: Optional[dict[str, Any]] = None,
             temperature: Optional[float] = None, seed: Optional[int] = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "seed": self.seed if seed is None else seed,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }
        if schema is not None:
            body["format"] = schema
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"ollama chat failed: {exc}") from exc
        self.calls += 1
        self.seconds += time.perf_counter() - t0
        return resp.json()["message"]["content"]

    def available(self) -> bool:
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=5).json()
        except requests.RequestException:
            return False
        names = {m["name"] for m in tags.get("models", [])}
        return self.model in names or f"{self.model}:latest" in names


def embed(texts: list[str], model: str = "nomic-embed-text", url: str = DEFAULT_URL) -> list[list[float]]:
    """Sentence embeddings from the local embedding model (intent similarity, search)."""
    out: list[list[float]] = []
    for i in range(0, len(texts), 32):
        chunk = [t if t.strip() else "(empty)" for t in texts[i:i + 32]]
        for attempt in range(3):
            resp = requests.post(f"{url}/api/embed", json={"model": model, "input": chunk}, timeout=120)
            if resp.ok:
                break
            time.sleep(2 * (attempt + 1))
        resp.raise_for_status()
        out.extend(resp.json()["embeddings"])
    return out


def unload(model: str, url: str = DEFAULT_URL) -> None:
    """Free GPU memory held by a loaded model (before fine-tuning)."""
    try:
        requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass
