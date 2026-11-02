"""Verify whether an overseas agent really has import and/or export lanes with Vietnam.

Two independent researchers, one judge:
  1. Google Gemini + Google Search grounding  (google_ai.gemini_research)
  2. Claude + Anthropic web search / web fetch  (claude_research)
  3. Claude (structured output) compares both, keeps only claims backed by a URL, and returns VietnamLaneCheck.

Either researcher can be switched off (providers=("gemini",) or ("claude",)). When both run and agree,
confidence can be "high"; disagreement is reported in sources_agree=False.
"""

from __future__ import annotations

import logging
from typing import Iterable

from .google_ai import GroundedResearch, gemini_available, gemini_research
from .llm import WEB_FETCH_TOOL, WEB_SEARCH_TOOL, check_refusal, common_kwargs, make_client
from .models import Lead, VietnamLaneCheck

log = logging.getLogger(__name__)

LOCAL_TERMS = {
    "china": "越南, 越南专线, 中越物流, 越南进口, 越南出口, 胡志明, 海防, 河内, 凭祥, 东兴",
    "japan": "ベトナム, ハイフォン, ホーチミン",
    "south korea": "베트남, 하이퐁, 호치민",
    "taiwan": "越南, 胡志明, 海防",
    "germany": "Vietnam, Haiphong, Ho-Chi-Minh-Stadt",
    "thailand": "เวียดนาม",
}


def research_prompt(lead: Lead) -> str:
    terms = LOCAL_TERMS.get(lead.country.strip().lower(), "")
    return f"""Research the freight forwarder "{lead.company_name}" (official website {lead.website}, \
country {lead.country}{', city ' + lead.city if lead.city else ''}).

Question: does this company move cargo INTO Vietnam (import to Vietnam), OUT OF Vietnam (export from Vietnam), \
both, or neither? Find:
1. Direction(s) and modes (sea FCL/LCL, air, cross-border trucking, rail, express).
2. Vietnam gateways mentioned (Hai Phong, Cat Lai/Ho Chi Minh, Cai Mep, Da Nang, Noi Bai/HAN, Tan Son Nhat/SGN, \
Huu Nghi-Lang Son, Mong Cai, Lao Cai...) and the gateways on their side.
3. Frequency or volume hints, main commodities.
4. Whether they own an office/subsidiary in Vietnam, and any named Vietnam agents/partners.

Search the company's own website first (e.g. site:{lead.domain} Vietnam), then news, directories and LinkedIn.
{('Also search in the local language with terms like: ' + terms) if terms else ''}
For EVERY finding give the source URL. If you find no evidence for a direction, say so explicitly. Never guess."""


def claude_research(lead: Lead, client=None, max_turns: int = 8) -> GroundedResearch:
    """Claude researches the same question with Anthropic's server-side web search / fetch."""
    client = client or make_client()
    messages: list = [{"role": "user", "content": research_prompt(lead)}]
    response = None
    for _ in range(max_turns):
        response = client.beta.messages.create(
            max_tokens=16000, tools=[WEB_SEARCH_TOOL, WEB_FETCH_TOOL], messages=messages, **common_kwargs("medium")
        )
        check_refusal(response)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "pause_turn":
            break

    text_parts, sources = [], []
    for block in (response.content if response else []):
        if block.type == "text":
            text_parts.append(block.text)
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url:
                    sources.append({"title": getattr(c, "title", None), "uri": url})
    seen, unique = set(), []
    for s in sources:
        if s["uri"] not in seen:
            seen.add(s["uri"])
            unique.append(s)
    return GroundedResearch(provider="Claude + web search", text="\n".join(text_parts), sources=unique)


JUDGE_SYSTEM = """You verify trade lanes of freight forwarders for a Vietnam-based forwarder's sales team.
You receive the lead record and one or two independent research notes (Google Gemini with Google Search, \
Claude with web search). Decide whether the company moves cargo into Vietnam, out of Vietnam, both, none, \
or unknown.

Rules:
- Only count a claim if a research note ties it to a URL. Prefer the company's own website.
- import_to_vn = cargo from their side/world INTO Vietnam; export_from_vn = cargo FROM Vietnam out.
  A "China-Vietnam line" with delivery in Vietnam is import_to_vn; services like "Vietnam to China", \
"越南进口" (import from Vietnam into China) or Vietnam-origin consolidation are export_from_vn.
- confidence: high = company's own site states it clearly, or both researchers agree with URLs; medium = one \
reliable source; low = indirect or conflicting. Use direction=unknown when evidence is too thin.
- sources_agree: true/false when two research notes were given, null when only one.
- Keep the evidence list short (max 5), most direct first."""


def judge(lead: Lead, research: list[GroundedResearch], client=None) -> VietnamLaneCheck:
    client = client or make_client()
    blocks = "\n\n".join(r.as_prompt_block() for r in research) or "(no research available)"
    response = client.beta.messages.parse(
        max_tokens=8000,
        system=JUDGE_SYSTEM,
        messages=[{
            "role": "user",
            "content": "Lead record:\n" + lead.model_dump_json(indent=2, exclude={"status", "created_at"})
            + "\n\n" + blocks,
        }],
        output_format=VietnamLaneCheck,
        **common_kwargs("medium"),
    )
    check_refusal(response)
    return response.parsed_output


def check_lead(lead: Lead, providers: Iterable[str] = ("gemini", "claude"), claude_client=None,
               gemini_client=None) -> VietnamLaneCheck:
    providers = list(providers)
    research: list[GroundedResearch] = []
    if "gemini" in providers:
        if gemini_client is not None or gemini_available():
            try:
                research.append(gemini_research(research_prompt(lead), client=gemini_client))
            except Exception as e:  # one researcher failing must not block the other
                log.error("Gemini research failed for %s: %s", lead.domain, e)
        else:
            log.warning("GEMINI_API_KEY not set - skipping Google AI research")
    if "claude" in providers:
        try:
            research.append(claude_research(lead, claude_client))
        except Exception as e:
            log.error("Claude research failed for %s: %s", lead.domain, e)
    return judge(lead, research, claude_client)


def run_lane_checks(store, providers=("gemini", "claude"), country: str | None = None, limit: int | None = None,
                    recheck: bool = False, claude_client=None, gemini_client=None) -> int:
    claude_client = claude_client or make_client()
    done = 0
    for row in store.for_lane_check(country=country, recheck=recheck, limit=limit):
        lead = Lead.model_validate_json(row["lead_json"])
        try:
            result = check_lead(lead, providers, claude_client, gemini_client)
        except Exception as e:
            log.error("Lane check failed for %s: %s", row["domain"], e)
            continue
        store.save_lane_check(row["domain"], result)
        log.info("%s -> %s (%s)", row["domain"], result.direction.value, result.confidence.value)
        done += 1
    return done
