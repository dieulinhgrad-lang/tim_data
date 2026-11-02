"""Daily run: find N NEW overseas agents per day (total_per_day, default 50) across markets, verify Vietnam lanes, score, draft, publish.

Steps
  1. Load quotas from config/daily.toml; read every domain already in the Google Sheet (long-term memory).
  2. Round 1: discover each market up to its quota. Round 2: re-distribute the shortfall to markets that still
     return results. Never pad: if fewer verified agents exist, the report says so.
  3. Vietnam lane check (Google Gemini + Claude), qualification, email drafts for leads >= min score.
  4. Write leads/YYYY-MM-DD.csv (today's new agents only) + a Markdown report; push all tabs to Google Sheets.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from .config import ROOT, Campaign, CompanyProfile, load_campaigns
from .models import Lead

log = logging.getLogger(__name__)
DEFAULT_DAILY = ROOT / "config" / "daily.toml"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")  # "today" means today in Vietnam, whatever the server clock says


def local_today() -> date:
    return datetime.now(TZ).date()


def local_day(iso_timestamp: str) -> date:
    dt = datetime.fromisoformat(iso_timestamp)
    return (dt.astimezone(TZ) if dt.tzinfo else dt).date()


@dataclass
class DailyConfig:
    spreadsheet_id: str | None
    total_per_day: int
    quota: dict[str, int]
    lane_providers: list[str] = field(default_factory=lambda: ["gemini", "claude"])
    min_score_for_draft: int = 55


def load_daily(path: Path = DEFAULT_DAILY) -> DailyConfig:
    d = tomllib.loads(path.read_text(encoding="utf-8"))
    return DailyConfig(
        spreadsheet_id=d.get("spreadsheet_id"), total_per_day=int(d.get("total_per_day", 50)),
        quota={k: int(v) for k, v in d.get("quota", {}).items()},
        lane_providers=list(d.get("lane_providers", ["gemini", "claude"])),
        min_score_for_draft=int(d.get("min_score_for_draft", 55)),
    )


def plan_quotas(cfg: DailyConfig, available: list[str], total: int | None = None) -> dict[str, int]:
    """Scale configured quotas so they sum to `total`; ignore markets missing from campaigns.toml."""
    total = total or cfg.total_per_day
    base = {k: v for k, v in cfg.quota.items() if k in available and v > 0}
    if not base:
        raise ValueError("No valid markets in config/daily.toml [quota]")
    s = sum(base.values())
    plan = {k: (v * total) // s for k, v in base.items()}
    for k in sorted(base, key=lambda k: -base[k])[: total - sum(plan.values())]:  # hand out the remainder
        plan[k] += 1
    return plan


def redistribute(shortfall: int, produced: dict[str, int], asked: dict[str, int]) -> dict[str, int]:
    """Give the shortfall to markets that filled their quota (they still have supply), round-robin."""
    productive = [k for k in asked if produced.get(k, 0) >= asked[k] > 0]
    extra = {k: 0 for k in productive}
    for i in range(shortfall if productive else 0):
        extra[productive[i % len(productive)]] += 1
    return {k: v for k, v in extra.items() if v}


def todays_leads(store, day: date) -> list:
    out = []
    for r in store.all():
        if local_day(Lead.model_validate_json(r["lead_json"]).created_at) == day:
            out.append(r)
    return out


def write_daily_csv(store, day: date, out_dir: Path) -> Path:
    """leads/YYYY-MM-DD.csv with today's new agents (same columns as the Sheet)."""
    import csv

    from .sheets import HEADER, store_records

    today = {r["domain"] for r in todays_leads(store, day)}
    rows = [row for _, row in store_records(store) if row[0] in today]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return path


def report(store, day: date, target: int, per_market: dict[str, int]) -> str:
    from collections import Counter

    rows = todays_leads(store, day)
    tiers = Counter(r["tier"] or "-" for r in rows)
    lanes = Counter()
    for r in rows:
        if r["lane_json"]:
            import json
            lanes[json.loads(r["lane_json"])["direction"]] += 1
        else:
            lanes["not_checked"] += 1
    lines = [
        f"# Báo cáo tìm agent ngày {day.isoformat()}",
        f"- Mục tiêu: {target} agent mới — đạt: **{len(rows)}**" + ("" if len(rows) >= target else
                                                                  " (thiếu, không bù bằng dữ liệu chưa xác minh)"),
        "- Theo thị trường: " + ", ".join(f"{k}: {v}" for k, v in per_market.items()),
        "- Hạng: " + ", ".join(f"{k}: {v}" for k, v in sorted(tiers.items())),
        "- Tuyến VN: " + ", ".join(f"{k}: {v}" for k, v in sorted(lanes.items())),
    ]
    top = sorted((r for r in rows if r["score"] is not None), key=lambda r: -r["score"])[:10]
    if top:
        lines.append("\n## Top 10 hôm nay")
        for r in top:
            lead = Lead.model_validate_json(r["lead_json"])
            lines.append(f"- {lead.company_name} ({lead.country}) — {r['score']} điểm, hạng {r['tier']} — {lead.website}")
    return "\n".join(lines) + "\n"


def run_daily(store, profile: CompanyProfile, cfg: DailyConfig, client=None, provider: str = "claude",
              total: int | None = None, spreadsheet=None, day: date | None = None,
              out_dir: Path = Path("leads"), campaigns: dict[str, Campaign] | None = None) -> dict:
    from .discovery import run_discovery, run_gemini_discovery
    from .outreach import run_outreach
    from .qualify import run_qualification
    from .sheets import known_domains_from_sheet, push_to_sheets, store_records
    from .vietnam_lanes import run_lane_checks

    day = day or local_today()
    total = total or cfg.total_per_day
    campaigns = campaigns or load_campaigns()
    sheet_known = known_domains_from_sheet(spreadsheet) if spreadsheet is not None else set()
    log.info("Sheet already holds %d domains", len(sheet_known))

    def discover(key: str, n: int) -> int:
        c = campaigns[key]
        c = Campaign(key=c.key, country=c.country, focus=c.focus, target_count=n, cities=c.cities,
                     preferred_sources=c.preferred_sources)
        found: list[str] = []
        if provider in ("claude", "both"):
            found += run_discovery(c, profile, store, client, extra_known=sheet_known)
        if provider in ("gemini", "both") and len(set(found)) < n:
            c.target_count = n - len(set(found))
            found += run_gemini_discovery(c, profile, store, client, extra_known=sheet_known | set(found))
        sheet_known.update(found)
        return len(set(found))

    asked = plan_quotas(cfg, list(campaigns), total)
    produced = {k: discover(k, n) for k, n in asked.items()}
    shortfall = total - sum(produced.values())
    if shortfall > 0:  # round 2
        for k, n in redistribute(shortfall, produced, asked).items():
            produced[k] += discover(k, n)

    run_lane_checks(store, providers=cfg.lane_providers, claude_client=client)
    run_qualification(store, profile, client)
    run_outreach(store, profile, client, min_score=cfg.min_score_for_draft)

    csv_path = write_daily_csv(store, day, out_dir)
    text = report(store, day, total, produced)
    (out_dir / f"{day.isoformat()}-report.md").write_text(text, encoding="utf-8")
    pushed = push_to_sheets(spreadsheet, store_records(store)) if spreadsheet is not None else {}
    return {"produced": produced, "csv": str(csv_path), "report": text, "pushed": pushed}
