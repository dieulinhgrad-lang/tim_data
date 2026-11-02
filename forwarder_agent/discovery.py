"""Discovery agent: Claude searches the public web and records foreign forwarders via a `save_forwarder` tool.

Loop: Claude uses the server-side `web_search` / `web_fetch` tools (run by Anthropic) to research, and calls
our client-side `save_forwarder` tool once per company. We validate each call, dedupe by domain, and store it.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from .config import Campaign, CompanyProfile
from .llm import WEB_FETCH_TOOL, WEB_SEARCH_TOOL, check_refusal, common_kwargs, make_client
from .models import Lead, is_company_website
from .store import LeadStore

log = logging.getLogger(__name__)

_str_list = {"type": "array", "items": {"type": "string"}}
_nullable_str = {"type": ["string", "null"]}

SAVE_FORWARDER_TOOL = {
    "name": "save_forwarder",
    "description": (
        "Record ONE foreign freight-forwarding company you have verified from public sources. "
        "Call it once per company, as soon as you have verified it. Only include facts you saw on a page "
        "(never guess or construct email addresses). Use empty lists / null for unknown fields."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "company_name", "website", "country", "city", "company_type", "services", "transport_modes",
            "trade_lanes", "vietnam_evidence", "networks", "general_emails", "phones", "linkedin_url",
            "contacts", "source_urls", "notes",
        ],
        "properties": {
            "company_name": {"type": "string"},
            "website": {"type": "string", "description": "Official company website (homepage URL)"},
            "country": {"type": "string", "description": "Country of the office you found (English name)"},
            "city": _nullable_str,
            "company_type": {
                "type": "string",
                "enum": ["freight forwarder", "NVOCC", "customs broker", "3PL", "project cargo specialist", "other"],
            },
            "services": {**_str_list, "description": "e.g. sea FCL, LCL consolidation, air freight, customs, DDP"},
            "transport_modes": {**_str_list, "description": "sea, air, rail, road, multimodal"},
            "trade_lanes": {**_str_list, "description": "e.g. Asia-Europe, Transpacific, Intra-Asia, Vietnam-US"},
            "vietnam_evidence": {
                **_nullable_str,
                "description": "Quote/paraphrase showing they ship to/from Vietnam or SE Asia, else null",
            },
            "networks": {**_str_list, "description": "WCA, FIATA, JCtrans, GLA, PPL, X2, national association..."},
            "general_emails": {**_str_list, "description": "Publicly listed company emails, e.g. info@, sales@"},
            "phones": _str_list,
            "linkedin_url": _nullable_str,
            "contacts": {
                "type": "array",
                "description": "Named people publicly listed as sales/overseas/agency/procurement contacts",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "title", "email", "source_url"],
                    "properties": {
                        "name": {"type": "string"},
                        "title": _nullable_str,
                        "email": {**_nullable_str, "description": "Only if published on the page"},
                        "source_url": _nullable_str,
                    },
                },
            },
            "source_urls": {**_str_list, "description": "Pages where you verified these facts"},
            "notes": {**_nullable_str, "description": "Anything useful for a sales person (size, specialties)"},
        },
    },
}

SYSTEM_PROMPT = """You are a B2B lead researcher for a Vietnam-based freight forwarder looking for overseas \
agent / partner forwarders. Our company:

{profile}

Your job: find real freight-forwarding companies in the target country that are likely to route cargo \
to or from Vietnam (import or export) and could appoint us as their Vietnam agent.

Good signals (prioritise): they mention Vietnam, Southeast Asia, Asia or China+1 sourcing; they run \
Asia-origin consolidations or Asia trade lanes; they are members of forwarder networks (WCA, JCtrans, GLA, \
PPL, X2, FIATA or a national association); they publish an "agents / network / partners" page; they serve \
industries that source from Vietnam (furniture, garments, footwear, electronics, seafood, coffee, agriculture).

Where to look (public sources only): company websites, national forwarder associations' member lists, public \
network directories, trade-fair exhibitor lists, news articles, public business listings. Search in English and \
in the target country's language. Never log in, bypass paywalls, or scrape content behind member-only areas.

Rules:
- Record each verified company with `save_forwarder` immediately - don't batch them up until the end.
- Every company MUST have its own official website (its own domain). A listing on a marketplace, directory \
or social network (made-in-china, alibaba, JCtrans, WCA directory, LinkedIn, yellow pages...) can be used to \
discover a company, but then find and record its official website; skip the company if it has none.
- Association / network membership is NOT required. Record it when present (networks), leave it empty when not.
- If the company already has its own office or subsidiary in Vietnam, still record it but say so in notes.
- Only FOREIGN companies headquartered or with an office in the target country. Skip Vietnam-headquartered \
companies, shipping lines, airlines, shippers/BCOs, software vendors and pure directories.
- Every fact must come from a page you saw; include those pages in source_urls. Do not invent or pattern-guess \
email addresses or people. Prefer generic business emails (info@, sales@, agency@, overseas@).
- Skip companies whose domain is in the "already known" list.
- Stop when you have saved the target number of companies or sources are exhausted, then reply with a short \
summary of what you saved and which sources were most productive."""


def build_user_prompt(campaign: Campaign, known: list[str]) -> str:
    lines = [
        f"Target country: {campaign.country}",
        f"Focus: {campaign.focus}",
        f"Target number of new companies: {campaign.target_count}",
    ]
    if campaign.cities:
        lines.append(f"Priority cities / hubs: {', '.join(campaign.cities)}")
    if campaign.preferred_sources:
        lines.append("Suggested starting sources:\n" + "\n".join(f"- {s}" for s in campaign.preferred_sources))
    lines.append(
        "Already known domains (skip these): " + (", ".join(known[:300]) if known else "none")
    )
    return "\n".join(lines)


def handle_save_forwarder(raw: dict, campaign: Campaign, store: LeadStore, seen: set[str]) -> tuple[str, bool]:
    """Validate and store one save_forwarder call. Returns (tool_result_text, is_error)."""
    try:
        lead = Lead.model_validate({**raw, "campaign": campaign.key})
    except ValidationError as e:
        return f"Invalid input, fix and retry: {e.errors()[:3]}", True

    domain = lead.domain
    if not is_company_website(lead.website):
        return (f"Rejected {lead.website}: a lead needs the company's OWN website (not a marketplace, directory "
                "or social page). Find the official site and call save_forwarder again, or skip the company."), True
    if domain.endswith(".vn") or lead.country.strip().lower() in {"vietnam", "viet nam"}:
        return f"Skipped {domain}: we only want companies outside Vietnam.", False
    if domain in seen:
        return f"{domain} already saved in this run - find a different company.", False

    is_new = store.upsert_lead(lead)
    seen.add(domain)
    if is_new:
        return f"Saved {lead.company_name} ({domain}). Total new this run: {len(seen)}.", False
    return f"{domain} was already in the database; merged any new facts. Look for other companies.", False


def run_discovery(
    campaign: Campaign,
    profile: CompanyProfile,
    store: LeadStore,
    client=None,
    max_turns: int = 40,
    effort: str = "high",
) -> list[str]:
    """Run the discovery agent for one campaign. Returns the list of domains saved in this run."""
    client = client or make_client()
    known = store.known_domains(campaign.country)
    seen: set[str] = set()
    messages: list = [{"role": "user", "content": build_user_prompt(campaign, known)}]
    tools = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL, SAVE_FORWARDER_TOOL]
    system = SYSTEM_PROMPT.format(profile=profile.as_prompt())

    for turn in range(max_turns):
        response = client.beta.messages.create(
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            **common_kwargs(effort),
        )
        check_refusal(response)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":  # long server-tool turn; send it back to continue
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            if response.stop_reason == "max_tokens":
                log.warning("Discovery hit max_tokens on turn %d; stopping.", turn)
            break

        results = []
        for block in tool_uses:
            if block.name == "save_forwarder":
                raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
                text, is_error = handle_save_forwarder(raw, campaign, store, seen)
                log.info("save_forwarder -> %s", text)
            else:
                text, is_error = f"Unknown tool {block.name}", True
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
        reached = len(seen) >= campaign.target_count
        if reached:
            results.append({"type": "text", "text": "Target reached. Reply with your short summary only."})
        messages.append({"role": "user", "content": results})

        if reached:
            # Let the model wrap up with its summary, then stop.
            final = client.beta.messages.create(
                max_tokens=4000, system=system, tools=tools, messages=messages, **common_kwargs("low")
            )
            messages.append({"role": "assistant", "content": final.content})
            break
    else:
        log.warning("Discovery stopped after max_turns=%d", max_turns)

    return sorted(seen)


# ---------------- Google AI (Gemini + Google Search) discovery ----------------
GEMINI_DISCOVERY_PROMPT = """Using Google Search, list up to {n} real freight-forwarding companies (forwarders, \
NVOCCs, customs brokers) based in {country} that ship cargo to or from Vietnam, or are likely to (Asia / \
Southeast Asia lanes). Focus: {focus}
{cities}
Requirements for each company:
- It MUST have its own official website (its own domain). Do not give marketplace, directory or social links \
as the website; use them only to find the company.
- Association / network membership (WCA, JCtrans, FIATA, national association) is optional - mention it if found.
- Do not include Vietnam-headquartered companies, shipping lines, airlines or software vendors.
- Skip these already-known domains: {known}

For each company give: name, official website, city, networks (if any), the evidence that they handle \
Vietnam cargo (with direction: into Vietnam / out of Vietnam), public business emails and phones, and the \
source URLs you used. Never invent emails or websites."""

EXTRACT_SYSTEM = """Convert the research notes into a list of companies. Copy only facts present in the notes. \
website must be the company's own homepage if the notes give one, else null. Drop entries that are clearly \
directories, marketplaces, carriers or Vietnam-headquartered companies."""


def run_gemini_discovery(campaign: Campaign, profile: CompanyProfile, store: LeadStore, claude_client=None,
                         gemini_client=None) -> list[str]:
    """Google Gemini finds candidates with Google Search; Claude structures them; same filters as save_forwarder."""
    from .google_ai import gemini_research
    from .models import DiscoveredList

    claude_client = claude_client or make_client()
    known = store.known_domains(campaign.country)
    research = gemini_research(GEMINI_DISCOVERY_PROMPT.format(
        n=campaign.target_count, country=campaign.country, focus=campaign.focus,
        cities=f"Priority cities: {', '.join(campaign.cities)}" if campaign.cities else "",
        known=", ".join(known[:300]) or "none",
    ), client=gemini_client)

    response = claude_client.beta.messages.parse(
        max_tokens=16000, system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": research.as_prompt_block()}],
        output_format=DiscoveredList, **common_kwargs("low"),
    )
    check_refusal(response)

    seen: set[str] = set()
    for c in response.parsed_output.companies:
        if not c.website:
            log.info("Gemini candidate %s skipped: no official website", c.company_name)
            continue
        raw = {
            "company_name": c.company_name, "website": c.website, "country": campaign.country, "city": c.city,
            "networks": c.networks, "vietnam_evidence": c.vietnam_evidence, "general_emails": c.general_emails,
            "phones": c.phones, "source_urls": c.source_urls or [s["uri"] for s in research.sources[:3]],
            "notes": "found via Google Gemini + Google Search",
        }
        text, _ = handle_save_forwarder(raw, campaign, store, seen)
        log.info("gemini candidate -> %s", text)
    return sorted(seen)
