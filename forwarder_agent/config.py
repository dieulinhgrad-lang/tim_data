"""Load company profile and campaign definitions from TOML files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / "config" / "company_profile.toml"
DEFAULT_CAMPAIGNS = ROOT / "config" / "campaigns.toml"


@dataclass
class CompanyProfile:
    name: str
    website: str
    sender_name: str
    sender_title: str
    sender_email: str
    offices: list[str]
    services: list[str]
    strengths: list[str]
    networks: list[str]
    extra_context: str = ""

    def as_prompt(self) -> str:
        bullet = lambda items: "\n".join(f"- {i}" for i in items) or "- (none)"
        return (
            f"Company: {self.name} ({self.website})\n"
            f"Offices in Vietnam: {', '.join(self.offices)}\n"
            f"Services:\n{bullet(self.services)}\n"
            f"Strengths / USPs:\n{bullet(self.strengths)}\n"
            f"Network memberships: {', '.join(self.networks) or 'none'}\n"
            f"Sender: {self.sender_name}, {self.sender_title} <{self.sender_email}>\n"
            f"{self.extra_context}".strip()
        )


@dataclass
class Campaign:
    key: str
    country: str
    focus: str
    target_count: int = 15
    cities: list[str] = field(default_factory=list)
    preferred_sources: list[str] = field(default_factory=list)


def load_profile(path: Path = DEFAULT_PROFILE) -> CompanyProfile:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return CompanyProfile(**data["company"])


def load_campaigns(path: Path = DEFAULT_CAMPAIGNS) -> dict[str, Campaign]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {key: Campaign(key=key, **value) for key, value in data.get("campaigns", {}).items()}
