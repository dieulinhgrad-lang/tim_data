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


# Marketplaces, directories and social sites: a lead must have its OWN company website, not a listing page.
NON_COMPANY_DOMAINS = (
    "made-in-china.com", "alibaba.com", "1688.com", "globalsources.com", "jctrans.com", "wcaworld.com",
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "wechat.com",
    "yellowpages", "qy6.com.cn", "007swz.com", "dy1905.net", "freightnet.com", "azfreight.com",
    "cofastnetwork.com", "kompass.com", "dnb.com", "zoominfo.com", "baidu.com", "zhihu.com", "sohu.com",
    "exa.ai", "google.com", "wikipedia.org",
)


def is_company_website(url_or_domain: str) -> bool:
    d = normalize_domain(url_or_domain)
    if not d or "." not in d:
        return False
    return not any(d == bad or d.endswith("." + bad) or d.startswith(bad) for bad in NON_COMPANY_DOMAINS)


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


# ---------------- Vietnam trade-lane verification ----------------
class LaneDirection(str, Enum):
    IMPORT_TO_VN = "import_to_vn"      # hàng từ nước của agent / thế giới VÀO Việt Nam
    EXPORT_FROM_VN = "export_from_vn"  # hàng từ Việt Nam RA nước của agent / thế giới
    BOTH = "both"
    NONE = "none"                      # không thấy tuyến Việt Nam
    UNKNOWN = "unknown"                # không đủ dữ liệu


class Confidence(str, Enum):
    HIGH = "high"      # trang của chính công ty nói rõ, hoặc 2 nguồn độc lập trùng khớp
    MEDIUM = "medium"  # 1 nguồn đáng tin, hoặc suy ra rõ ràng
    LOW = "low"        # gián tiếp / mâu thuẫn


class LaneEvidence(BaseModel):
    claim: str = Field(description="What the source says, short quote or paraphrase")
    url: str
    source: str = Field(description="'company_site', 'directory', 'news', 'linkedin', 'other'")


class VietnamLaneCheck(BaseModel):
    """Does this agent move cargo into and/or out of Vietnam? Structured verdict built from web evidence."""

    direction: LaneDirection
    modes: list[str] = Field(description="sea FCL, sea LCL, air, cross-border road, rail, express")
    vn_gateways: list[str] = Field(description="Vietnam ports / airports / border gates mentioned, e.g. Hai Phong, "
                                               "Cat Lai, Da Nang, SGN, HAN, Huu Nghi/Lang Son, Mong Cai")
    counterpart_gateways: list[str] = Field(description="Origin/destination gateways on the agent's side")
    commodities: list[str]
    frequency_hint: str | None = Field(description="e.g. 'daily trucks', 'weekly LCL', '1-3 containers/day'")
    has_vietnam_office: bool
    vietnam_partners: list[str] = Field(description="Named Vietnam agents/partners if published")
    evidence: list[LaneEvidence]
    confidence: Confidence
    sources_agree: bool | None = Field(description="True/False if Google and Claude research were compared, else null")
    summary_vi: str = Field(description="1-2 câu tiếng Việt: agent có tuyến nhập/xuất VN không, chiều nào, bằng chứng gì")


class DiscoveredCompany(BaseModel):
    company_name: str
    website: str | None
    city: str | None
    networks: list[str]
    vietnam_evidence: str | None
    general_emails: list[str]
    phones: list[str]
    source_urls: list[str]


class DiscoveredList(BaseModel):
    companies: list[DiscoveredCompany]
