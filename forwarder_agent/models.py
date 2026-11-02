"""Data models for leads (foreign freight forwarders), qualification and outreach drafts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class LeadStatus(str, Enum):
    NEW = "new"                      # vừa thu thập, chưa chấm điểm
    QUALIFIED = "qualified"          # đã chấm điểm
    DRAFTED = "drafted"              # đã có email nháp chờ duyệt
    CONTACTED = "contacted"          # sales đã gửi email (đánh dấu thủ công)
    REPLIED = "replied"              # agent nước ngoài đã phản hồi
    REJECTED = "rejected"            # không phù hợp
    DO_NOT_CONTACT = "do_not_contact"  # yêu cầu không liên hệ / opt-out


class ContactPerson(BaseModel):
    name: str
    title: str | None = None
    email: str | None = None
    source_url: str | None = None


class Lead(BaseModel):
    """One foreign forwarder company, as captured by the discovery agent."""

    company_name: str
    website: str
    country: str
    city: str | None = None
    company_type: str = "freight forwarder"
    services: list[str] = Field(default_factory=list)
    transport_modes: list[str] = Field(default_factory=list)
    trade_lanes: list[str] = Field(default_factory=list)
    vietnam_evidence: str | None = None
    networks: list[str] = Field(default_factory=list)
    general_emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None
    contacts: list[ContactPerson] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None

    # pipeline bookkeeping
    campaign: str | None = None
    status: LeadStatus = LeadStatus.NEW
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("website")
    @classmethod
    def _normalize_website(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v

    @property
    def domain(self) -> str:
        return normalize_domain(self.website)


def normalize_domain(url_or_domain: str) -> str:
    """'https://www.ABC-Logistics.de/en/' -> 'abc-logistics.de' (dedup key)."""
    s = url_or_domain.strip().lower()
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).hostname or ""
    return host.removeprefix("www.")


class Tier(str, Enum):
    A = "A"  # rất phù hợp - liên hệ ngay
    B = "B"  # phù hợp - liên hệ trong đợt này
    C = "C"  # có tiềm năng nhưng thiếu dữ liệu
    D = "D"  # không phù hợp


class Qualification(BaseModel):
    """Structured output of the qualification step."""

    score: int = Field(description="0-100 fit score for partnering with a Vietnam-based forwarder")
    tier: Tier
    is_forwarder: bool = Field(description="False if it is a shipper, carrier, software vendor, directory, etc.")
    vietnam_fit: str = Field(description="Why this company may move cargo to/from Vietnam (or why not)")
    recommended_services: list[str] = Field(description="Our services most relevant to this lead")
    outreach_angle: str = Field(description="One-sentence hook for the first email")
    red_flags: list[str]
    reasons: list[str]


class EmailDraft(BaseModel):
    """Structured output of the outreach step. Never sent automatically."""

    subject: str
    body: str = Field(description="Plain-text first email in English, <= 160 words, with opt-out line")
    follow_up_1: str = Field(description="Short follow-up for ~5 business days later")
    follow_up_2: str = Field(description="Final short follow-up for ~12 business days later")
    recipient_email: str | None = Field(description="Best public email to send to, or null if none found")
    notes_vi: str = Field(description="Ghi chú tiếng Việt cho sales: vì sao chọn góc tiếp cận này, cần kiểm tra gì")
