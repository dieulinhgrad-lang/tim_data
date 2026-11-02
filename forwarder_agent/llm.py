"""Thin wrapper around the Anthropic client with the settings every step shares."""

from __future__ import annotations

import os

import anthropic

MODEL = os.environ.get("FORWARDER_AGENT_MODEL", "claude-opus-5-5")

# Server-side refusal fallback: if a request is declined by a safety classifier,
# the API re-runs it on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 25}
WEB_FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 20}


class RefusalError(RuntimeError):
    pass


def make_client() -> anthropic.Anthropic:
    # Credentials: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
    return anthropic.Anthropic(max_retries=4)


def common_kwargs(effort: str = "medium") -> dict:
    return {
        "model": MODEL,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }


def check_refusal(response) -> None:
    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        raise RefusalError(f"Model declined the request: {details}")
