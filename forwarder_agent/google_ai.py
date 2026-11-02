"""Google AI (Gemini) with Google Search grounding, used as a second research source next to Claude.

Gemini searches Google and returns grounded notes + source URLs. Claude then turns those notes into
structured, validated data (Gemini's search grounding is not combined with a response schema here, so the
structuring step stays with Claude).

Setup: pip install google-genai ; export GEMINI_API_KEY=...  (from Google AI Studio)
Model: GEMINI_MODEL env var (default "gemini-flash-latest").
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")


@dataclass
class GroundedResearch:
    provider: str
    text: str
    sources: list[dict] = field(default_factory=list)  # [{"title":..., "uri":...}]
    queries: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        src = "\n".join(f"- {s.get('title') or ''} {s.get('uri') or ''}".strip() for s in self.sources) or "- (none)"
        return f"### Research from {self.provider}\n{self.text.strip()}\n\nSources:\n{src}"


def gemini_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def make_gemini_client():
    from google import genai  # optional dependency

    return genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY


def gemini_research(prompt: str, client=None, model: str = GEMINI_MODEL) -> GroundedResearch:
    """Ask Gemini a research question answered with live Google Search results."""
    from google.genai import types

    client = client or make_gemini_client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    sources, queries = [], []
    for cand in response.candidates or []:
        meta = getattr(cand, "grounding_metadata", None)
        if not meta:
            continue
        queries.extend(meta.web_search_queries or [])
        for chunk in meta.grounding_chunks or []:
            web = getattr(chunk, "web", None)
            if web and web.uri:
                sources.append({"title": web.title, "uri": web.uri, "domain": getattr(web, "domain", None)})
    return GroundedResearch(provider="Google Gemini + Google Search", text=response.text or "",
                            sources=sources, queries=queries)
