"""SQLite storage for leads, qualifications and drafts. One row per company domain."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import EmailDraft, Lead, LeadStatus, Qualification, VietnamLaneCheck, is_company_website

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    domain        TEXT PRIMARY KEY,
    country       TEXT NOT NULL,
    campaign      TEXT,
    status        TEXT NOT NULL,
    score         INTEGER,
    tier          TEXT,
    lead_json     TEXT NOT NULL,
    qual_json     TEXT,
    draft_json    TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country);
"""

_LIST_FIELDS = ("services", "transport_modes", "trade_lanes", "networks", "general_emails", "phones", "source_urls")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LeadStore:
    def __init__(self, path: str | Path = "data/leads.db"):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(leads)")}
        if "lane_json" not in cols:  # upgrade databases created before the Vietnam-lane check existed
            self.db.execute("ALTER TABLE leads ADD COLUMN lane_json TEXT")
            self.db.commit()

    # ---------- write ----------
    def upsert_lead(self, lead: Lead) -> bool:
        """Insert a new lead, or merge new facts into an existing one. Returns True if new."""
        row = self.db.execute("SELECT lead_json, status FROM leads WHERE domain = ?", (lead.domain,)).fetchone()
        if row is None:
            self.db.execute(
                "INSERT INTO leads (domain, country, campaign, status, lead_json, updated_at) VALUES (?,?,?,?,?,?)",
                (lead.domain, lead.country, lead.campaign, lead.status.value, lead.model_dump_json(), _now()),
            )
            self.db.commit()
            return True

        existing = Lead.model_validate_json(row["lead_json"])
        merged = _merge(existing, lead)
        self.db.execute(
            "UPDATE leads SET lead_json = ?, updated_at = ? WHERE domain = ?",
            (merged.model_dump_json(), _now(), lead.domain),
        )
        self.db.commit()
        return False

    def save_qualification(self, domain: str, q: Qualification) -> None:
        status = LeadStatus.REJECTED if q.tier.value == "D" or not q.is_forwarder else LeadStatus.QUALIFIED
        self._update(domain, qual_json=q.model_dump_json(), score=q.score, tier=q.tier.value, status=status.value)

    def save_draft(self, domain: str, d: EmailDraft) -> None:
        self._update(domain, draft_json=d.model_dump_json(), status=LeadStatus.DRAFTED.value)

    def save_lane_check(self, domain: str, check: VietnamLaneCheck) -> None:
        self._update(domain, lane_json=check.model_dump_json())

    def lane_check(self, domain: str) -> VietnamLaneCheck | None:
        row = self.get(domain)
        return VietnamLaneCheck.model_validate_json(row["lane_json"]) if row and row["lane_json"] else None

    def for_lane_check(self, country: str | None = None, recheck: bool = False, limit: int | None = None):
        sql, args = "SELECT * FROM leads WHERE status NOT IN ('rejected', 'do_not_contact')", []
        if not recheck:
            sql += " AND lane_json IS NULL"
        if country:
            sql += " AND lower(country) = lower(?)"
            args.append(country)
        sql += " ORDER BY score DESC NULLS LAST, domain"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return list(self.db.execute(sql, args))

    def set_status(self, domain: str, status: LeadStatus) -> bool:
        cur = self.db.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE domain = ?", (status.value, _now(), domain)
        )
        self.db.commit()
        return cur.rowcount > 0

    def _update(self, domain: str, **cols) -> None:
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.db.execute(
            f"UPDATE leads SET {sets}, updated_at = ? WHERE domain = ?", (*cols.values(), _now(), domain)
        )
        self.db.commit()

    # ---------- read ----------
    def known_domains(self, country: str | None = None) -> list[str]:
        if country:
            rows = self.db.execute("SELECT domain FROM leads WHERE lower(country) = lower(?)", (country,))
        else:
            rows = self.db.execute("SELECT domain FROM leads")
        return [r["domain"] for r in rows]

    def by_status(self, status: LeadStatus, min_score: int | None = None, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM leads WHERE status = ?"
        args: list = [status.value]
        if min_score is not None:
            sql += " AND score >= ?"
            args.append(min_score)
        sql += " ORDER BY score DESC, updated_at"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return list(self.db.execute(sql, args))

    def get(self, domain: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM leads WHERE domain = ?", (domain,)).fetchone()

    def all(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM leads ORDER BY score DESC NULLS LAST, country, domain"))

    def stats(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, count(*) AS n FROM leads GROUP BY status")}

    # ---------- export ----------
    def export_csv(self, out: str | Path) -> int:
        """Excel-friendly CSV (UTF-8 BOM) with one row per lead."""
        rows = self.all()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "domain", "company_name", "country", "city", "status", "score", "tier", "campaign",
                "company_type", "transport_modes", "services", "trade_lanes", "networks", "vietnam_evidence",
                "general_emails", "phones", "contacts", "linkedin_url", "outreach_angle", "red_flags",
                "email_to", "email_subject", "email_body", "source_urls",
            ])
            for r in rows:
                lead = Lead.model_validate_json(r["lead_json"])
                q = Qualification.model_validate_json(r["qual_json"]) if r["qual_json"] else None
                d = EmailDraft.model_validate_json(r["draft_json"]) if r["draft_json"] else None
                w.writerow([
                    r["domain"], lead.company_name, lead.country, lead.city or "", r["status"],
                    r["score"] if r["score"] is not None else "", r["tier"] or "", lead.campaign or "",
                    lead.company_type, "; ".join(lead.transport_modes), "; ".join(lead.services),
                    "; ".join(lead.trade_lanes), "; ".join(lead.networks), lead.vietnam_evidence or "",
                    "; ".join(lead.general_emails), "; ".join(lead.phones),
                    "; ".join(f"{c.name} ({c.title or '-'}) {c.email or ''}".strip() for c in lead.contacts),
                    lead.linkedin_url or "", q.outreach_angle if q else "", "; ".join(q.red_flags) if q else "",
                    (d.recipient_email or "") if d else "", d.subject if d else "", d.body if d else "",
                    " ".join(lead.source_urls),
                ])
        return len(rows)

    def export_drafts_markdown(self, out: str | Path) -> int:
        """All drafts in one Markdown file for human review before sending."""
        rows = self.by_status(LeadStatus.DRAFTED)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        parts = ["# Email nháp chờ duyệt\n", "> Chưa có email nào được gửi. Sales kiểm tra, chỉnh sửa rồi gửi thủ công, "
                 "sau đó chạy `python -m forwarder_agent set-status <domain> contacted`.\n"]
        for r in rows:
            lead = Lead.model_validate_json(r["lead_json"])
            d = EmailDraft.model_validate_json(r["draft_json"])
            parts.append(
                f"\n---\n\n## {lead.company_name} — {lead.country} (score {r['score']}, tier {r['tier']})\n\n"
                f"- Website: {lead.website}\n- Gửi tới: {d.recipient_email or '(chưa có email công khai — tìm thêm)'}\n"
                f"- Ghi chú: {d.notes_vi}\n\n**Subject:** {d.subject}\n\n```text\n{d.body}\n```\n\n"
                f"**Follow-up 1:**\n\n```text\n{d.follow_up_1}\n```\n\n**Follow-up 2:**\n\n```text\n{d.follow_up_2}\n```\n"
            )
        out.write_text("".join(parts), encoding="utf-8")
        return len(rows)

    def import_csv(self, path: str | Path, campaign: str | None = None) -> tuple[int, int]:
        """Import leads you already have (trade fair lists, network directories you can legally export).

        Required columns: company_name, website, country. Optional: city, general_emails, phones, networks,
        services, transport_modes, trade_lanes, notes (lists separated by ';').
        """
        new = merged = 0
        with Path(path).open(encoding="utf-8-sig") as f:
            for rec in csv.DictReader(f):
                if not (rec.get("company_name") and rec.get("website") and rec.get("country")):
                    continue
                if not is_company_website(rec["website"]):  # must be the company's own site
                    continue
                data = {k: v for k, v in rec.items() if v}
                for k in _LIST_FIELDS:
                    if k in data:
                        data[k] = [x.strip() for x in data[k].split(";") if x.strip()]
                data["campaign"] = campaign or data.get("campaign")
                data.setdefault("source_urls", [f"import:{Path(path).name}"])
                lead = Lead.model_validate({k: v for k, v in data.items() if k in Lead.model_fields})
                if self.upsert_lead(lead):
                    new += 1
                else:
                    merged += 1
        return new, merged


def _merge(old: Lead, new: Lead) -> Lead:
    data = old.model_dump()
    for k in _LIST_FIELDS:
        data[k] = list(dict.fromkeys([*getattr(old, k), *getattr(new, k)]))
    known = {(c.name.lower(), (c.email or "").lower()) for c in old.contacts}
    data["contacts"] = [c.model_dump() for c in old.contacts] + [
        c.model_dump() for c in new.contacts if (c.name.lower(), (c.email or "").lower()) not in known
    ]
    for k in ("city", "vietnam_evidence", "linkedin_url", "notes"):
        if not data.get(k) and getattr(new, k):
            data[k] = getattr(new, k)
    return Lead.model_validate(data)


def lead_from_row(row: sqlite3.Row) -> Lead:
    return Lead.model_validate_json(row["lead_json"])


def row_summary(row: sqlite3.Row) -> dict:
    out = json.loads(row["lead_json"])
    out["score"], out["tier"], out["status"] = row["score"], row["tier"], row["status"]
    return out
