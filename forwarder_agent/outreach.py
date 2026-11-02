"""Outreach: write a personalised first email + 2 follow-ups for each qualified lead.

Drafts are stored for human review. Nothing is sent automatically.
"""

from __future__ import annotations

import logging

from .config import CompanyProfile
from .llm import check_refusal, common_kwargs, make_client
from .models import EmailDraft, Lead, LeadStatus, Qualification
from .store import LeadStore, lead_from_row

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write first-touch B2B partnership emails from a Vietnam-based freight forwarder to \
overseas forwarders, proposing to act as their Vietnam agent for import and export shipments.

Sender company:
{profile}

Style:
- Plain text, English, peer-to-peer tone between forwarders. No hype, no emojis, no attachments mentioned.
- First email <= 160 words. Open with one specific, true observation about THEIR business (from the lead \
data), then 2-3 concrete things we can do for their Vietnam shipments (e.g. FOB/EXW pick-ups at HCMC/Hai \
Phong/Da Nang, LCL consolidation, customs clearance, door delivery for imports, competitive rates to their \
gateway), then one low-friction ask (e.g. "Do you have any Vietnam shipments we could quote on?" or \
"Could we exchange agency profiles / rate requests?").
- Never invent facts, volumes, awards, rates or mutual contacts. Only use services/strengths listed for the sender.
- Address a named contact if one is given, otherwise "Dear {{Company}} team".
- End with the sender signature and a one-line opt-out: "If this isn't relevant, just reply 'no thanks' and \
we won't contact you again."
- Follow-ups: 2-4 sentences each, add one new piece of value (e.g. current market note or a specific lane), \
no guilt-tripping.
- recipient_email: choose the best publicly listed address from the lead data (named sales/agency contact > \
agency@/overseas@/sales@ > info@). Null if none.
- notes_vi: 2-3 câu tiếng Việt cho nhân viên sales: lý do chọn góc tiếp cận, điều cần kiểm tra trước khi gửi."""


def draft_email(lead: Lead, q: Qualification, profile: CompanyProfile, client=None) -> EmailDraft:
    client = client or make_client()
    payload = (
        "Lead:\n" + lead.model_dump_json(indent=2, exclude={"status", "created_at", "campaign"})
        + "\n\nQualification:\n" + q.model_dump_json(indent=2)
    )
    response = client.beta.messages.parse(
        max_tokens=8000,
        system=SYSTEM_PROMPT.format(profile=profile.as_prompt()),
        messages=[{"role": "user", "content": payload}],
        output_format=EmailDraft,
        **common_kwargs("medium"),
    )
    check_refusal(response)
    return response.parsed_output


def run_outreach(
    store: LeadStore, profile: CompanyProfile, client=None, min_score: int = 55, limit: int | None = None
) -> int:
    client = client or make_client()
    rows = store.by_status(LeadStatus.QUALIFIED, min_score=min_score, limit=limit)
    done = 0
    for row in rows:
        lead = lead_from_row(row)
        q = Qualification.model_validate_json(row["qual_json"])
        try:
            d = draft_email(lead, q, profile, client)
        except Exception as e:
            log.error("Drafting failed for %s: %s", row["domain"], e)
            continue
        store.save_draft(row["domain"], d)
        log.info("Drafted email for %s -> %s", row["domain"], d.recipient_email)
        done += 1
    return done
