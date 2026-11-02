"""Qualification: score each new lead for fit as an overseas partner of a Vietnam forwarder."""

from __future__ import annotations

import logging

from .config import CompanyProfile
from .llm import check_refusal, common_kwargs, make_client
from .models import Lead, LeadStatus, Qualification
from .store import LeadStore, lead_from_row

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You qualify overseas freight forwarders as potential agency partners for this \
Vietnam-based forwarder:

{profile}

Score 0-100 using this rubric (sum, cap at 100):
- 30: evidence of Vietnam / Southeast Asia / Asia trade lanes or Asia-origin consolidation
- 20: forwarder/NVOCC with sea and/or air capability matching our services
- 15: member of a forwarder network or national association (WCA, JCtrans, GLA, PPL, X2, FIATA, ...)
- 15: serves industries that source from or ship to Vietnam (furniture, garments, footwear, electronics, \
seafood, coffee, agri, machinery)
- 10: has a reachable public business email or named sales/agency contact
- 10: size/profile suggests steady volume (multiple offices, own consolidations, years in business)

Tiers: A >= 75, B 55-74, C 35-54, D < 35. Set is_forwarder=false and tier D for shippers, carriers, \
software vendors, directories, or Vietnam-headquartered companies.
Be skeptical: only credit facts present in the lead data. Write all fields in English."""


def qualify_lead(lead: Lead, profile: CompanyProfile, client=None) -> Qualification:
    client = client or make_client()
    response = client.beta.messages.parse(
        max_tokens=8000,
        system=SYSTEM_PROMPT.format(profile=profile.as_prompt()),
        messages=[{
            "role": "user",
            "content": "Qualify this lead:\n\n" + lead.model_dump_json(indent=2, exclude={"status", "created_at"}),
        }],
        output_format=Qualification,
        **common_kwargs("medium"),
    )
    check_refusal(response)
    return response.parsed_output


def run_qualification(store: LeadStore, profile: CompanyProfile, client=None, limit: int | None = None) -> int:
    client = client or make_client()
    rows = store.by_status(LeadStatus.NEW, limit=limit)
    done = 0
    for row in rows:
        lead = lead_from_row(row)
        try:
            q = qualify_lead(lead, profile, client)
        except Exception as e:  # keep going; one bad lead must not stop the batch
            log.error("Qualification failed for %s: %s", row["domain"], e)
            continue
        store.save_qualification(row["domain"], q)
        log.info("%s -> score %d tier %s", row["domain"], q.score, q.tier.value)
        done += 1
    return done
