"""Model layer (C2). Gemini via google-genai; model ID comes from .env. Optional: with no key or
when call limits are hit, the app runs in calculator-only mode (templates, no LLM)."""
from __future__ import annotations
import json, re, time
from pathlib import Path
import yaml
from . import config
from .memory import Memory

PROMPTS = yaml.safe_load((config.ROOT / "prompts" / "solarwise_assistant.yaml").read_text(encoding="utf-8"))


class GeminiClient:
    def __init__(self, memory: Memory):
        self.mem, self._client = memory, None

    @property
    def configured(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    @property
    def available(self) -> bool:
        return self.configured and self.mem.can_call_llm()

    def _c(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        return self._client

    def _timed(self, label, fn):
        t0 = time.perf_counter()
        try:
            out = fn()
            self.mem.count_llm_call()
            self.mem.trace(f"llm:{label}", (time.perf_counter() - t0) * 1000, f"model={config.GEMINI_MODEL}")
            return out
        except Exception as e:  # quota, network, auth
            self.mem.trace(f"llm:{label}:error", (time.perf_counter() - t0) * 1000, type(e).__name__)
            return None

    def classify(self, user_text: str) -> dict | None:
        if not self.available:
            return None
        from google.genai import types
        def call():
            r = self._c().models.generate_content(
                model=config.GEMINI_MODEL, contents=user_text,
                config=types.GenerateContentConfig(system_instruction=PROMPTS["classify"], response_mime_type="application/json", temperature=0))
            return json.loads(re.sub(r"^```json|```$", "", r.text.strip()))
        return self._timed("classify", call)

    def _compose_prompt(self, user_text, tool_text, evidence) -> str:
        return PROMPTS["compose"].format(user_text=user_text, tool_results=tool_text, evidence=evidence or "(none)")

    def compose(self, user_text: str, tool_text: str, evidence: str) -> str | None:
        if not self.available:
            return None
        from google.genai import types
        def call():
            r = self._c().models.generate_content(
                model=config.GEMINI_MODEL, contents=self._compose_prompt(user_text, tool_text, evidence),
                config=types.GenerateContentConfig(system_instruction=PROMPTS["system"], temperature=0.1))
            return r.text
        return self._timed("compose", call)

    def stream_compose(self, user_text, tool_text, evidence):
        """Yield text chunks from Gemini (E1 streaming); yields nothing if unavailable."""
        if not self.available:
            return
        from google.genai import types
        try:
            self.mem.count_llm_call()
            for ch in self._c().models.generate_content_stream(
                    model=config.GEMINI_MODEL, contents=self._compose_prompt(user_text, tool_text, evidence),
                    config=types.GenerateContentConfig(system_instruction=PROMPTS["system"], temperature=0.1)):
                if ch.text:
                    yield ch.text
        except Exception as e:
            self.mem.trace("llm:stream:error", 0, type(e).__name__)


_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?")


def numbers_grounded(answer: str, allowed_text: str) -> tuple[bool, list[str]]:
    """Every multi-digit number in an LLM answer must appear in tool results / evidence / user text."""
    allowed = {n.replace(",", "") for n in _NUM.findall(allowed_text)}
    bad = []
    for n in _NUM.findall(answer):
        n2 = n.replace(",", "").rstrip(".")
        if len(n2.replace(".", "")) <= 1:
            continue
        if n2 not in allowed and n2.rstrip("0").rstrip(".") not in {a.rstrip("0").rstrip(".") for a in allowed}:
            bad.append(n2)
    return (not bad), bad
