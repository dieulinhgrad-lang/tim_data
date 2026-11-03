"""Bulk email drafts (mail merge) built from the analysed lead files - no LLM, no sending.

Each email is tailored to what the analysis says the agent needs on the Vietnam side:
  - lane direction (export from VN -> origin services; import into VN -> destination services; both; unknown)
  - Vietnam ports / border gates and modes (sea, air, China road)
  - whether they already have their own Vietnam office (-> offer the regions they don't cover)
  - their market (-> the right FTA certificate of origin)
Only services listed in config/company_profile.toml-style wording are offered. Drafts are for human review.
"""

from __future__ import annotations

import csv
import glob
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import CompanyProfile

NORTH = {"hải phòng": "Hai Phong", "hà nội": "Hanoi", "han": "Hanoi", "lạng sơn": "Lang Son", "hữu nghị": "Huu Nghi",
         "móng cái": "Mong Cai", "bắc ninh": "Bac Ninh", "quảng ninh": "Quang Ninh", "đồng đăng": "Dong Dang",
         "tân thanh": "Tan Thanh", "lào cai": "Lao Cai"}
CENTRAL = {"đà nẵng": "Da Nang", "quy nhơn": "Quy Nhon"}
SOUTH = {"hcm": "Ho Chi Minh City", "cát lái": "Cat Lai", "cái mép": "Cai Mep", "sgn": "Ho Chi Minh City",
         "vũng tàu": "Vung Tau", "đồng nai": "Dong Nai", "bình dương": "Binh Duong"}
REGION_NAME = {"north": "North Vietnam (Hanoi, Hai Phong)", "central": "Central Vietnam (Da Nang)",
               "south": "South Vietnam (Ho Chi Minh City, Cat Lai, Cai Mep)"}

FTA = {
    "China": "C/O Form E (ACFTA)",
    "South Korea": "C/O Form VK / AK (VKFTA, AKFTA)",
    "Japan": "C/O Form VJ / AJ (VJEPA, AJCEP)",
    "India": "C/O Form AI (AIFTA)",
    "Australia": "C/O Form AANZ (AANZFTA)",
    "United Kingdom": "UKVFTA origin documents (EUR.1)",
    "Germany": "EVFTA origin documents (EUR.1 / REX statement)",
    "Netherlands": "EVFTA origin documents (EUR.1 / REX statement)",
}
LANE_ALIASES = {"US": "the US", "USA": "the US", "UK": "the UK", "Europe": "Europe", "EU": "Europe"}

OPT_OUT = "If this isn't relevant, just reply \"no thanks\" and we won't contact you again."


@dataclass
class Draft:
    domain: str
    company: str
    country: str
    tier: str
    score: int
    direction: str
    to: str
    cc: str
    contact: str
    subject: str
    body: str
    followup: str
    notes_vi: str
    extra: dict = field(default_factory=dict)


def _regions(gateways: str) -> tuple[set[str], list[str]]:
    regions, places = set(), []
    for part in re.split(r"[,/;()]+", gateways.lower()):
        part = part.strip()
        for table, reg in ((NORTH, "north"), (CENTRAL, "central"), (SOUTH, "south")):
            for key, name in table.items():
                if key in part:
                    regions.add(reg)
                    if name not in places:
                        places.append(name)
    return regions, places


def _counterpart(lanes: str, country: str) -> str:
    """Other end of their Vietnam lane, e.g. 'Vietnam-UK' -> 'the UK'; fallback: their market."""
    for lane in lanes.split(";"):
        parts = [p.strip() for p in lane.split("-")]
        if "Vietnam" in parts and len(parts) >= 2:
            others = [p for p in parts if p != "Vietnam"]
            if others:
                return LANE_ALIASES.get(others[0], others[0])
    return LANE_ALIASES.get(country, country)


def _pick_recipient(emails: str, direction: str, override: str = "") -> tuple[str, str]:
    if override:
        return override, ""
    addrs = [e.strip().lstrip("'") for e in re.split(r"[;,\s]+", emails) if "@" in e]
    if not addrs:
        return "", ""
    pref = ["agency", "overseas", "partner"]
    pref += {"Xuất từ VN": ["export", "import"], "Nhập vào VN": ["import", "export"]}.get(direction, [])
    pref += ["sales", "business", "bd", "market", "info", "cs"]

    def rank(a: str) -> int:
        local = a.split("@")[0].lower()
        return next((i for i, p in enumerate(pref) if local.startswith(p)), len(pref))

    addrs.sort(key=rank)
    return addrs[0], "; ".join(addrs[1:])


def _join(items: list[str]) -> str:
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def compose(r: dict, country: str, profile: CompanyProfile, contact: dict | None = None) -> Draft:
    contact = contact or {}
    company = r["Công ty"].strip()
    direction = r["Tuyến VN: chiều"].strip() or "Chưa rõ"
    modes = (r["Tuyến VN: phương thức"] or r["Phương thức"]).lower()
    gateways = r["Tuyến VN: cảng / cửa khẩu"]
    has_office = r["Có VP tại VN?"].strip() == "Có"
    regions, places = _regions(gateways)
    other = _counterpart(r["Tuyến"], country)
    city = r["Thành phố"].split(",")[0].strip()
    dest = other if other not in ("Asia", "World", "Southeast Asia") else "your gateways"
    border = _has(gateways, "hữu nghị", "móng cái", "lạng sơn", "tân thanh", "đồng đăng", "lào cai", "đông hưng")
    road_china = (country == "China" or other == "China") and "road" in modes and (border or not places)
    air = "air" in modes
    co = FTA.get(country) if other not in ("the US",) else None
    if country == "China" and other != "China":  # cross-trade from a Chinese forwarder
        co = None
    via = f" via {', '.join(places[:3])}" if places else ""

    # --- what they need on the Vietnam side -------------------------------------------------------
    export, imp = [], []
    export.append("FOB/EXW pick-ups at factories across North and South Vietnam, export customs clearance"
                  + (f" and {co}" if co else " and C/O / phytosanitary documents"))
    if road_china:
        export.append("Cross-border trucking Vietnam to China (Lang Son / Huu Nghi, Mong Cai) with clearance on the Vietnam side")
    export.append(f"Weekly LCL consolidation and FCL bookings from Cat Lai, Cai Mep, Hai Phong and Da Nang to {dest}"
                  + (", plus air freight via SGN and HAN" if air else ""))
    if road_china:
        imp.append("Border clearance at Huu Nghi (Lang Son) / Mong Cai and trucking straight to the consignee's door")
    imp.append("Deconsolidation at our CFS, import customs clearance and duty/tax handling")
    imp.append("Door delivery anywhere in Vietnam, with warehousing if the consignee needs it")

    if has_office:
        missing = [REGION_NAME[x] for x in ("north", "south", "central") if x not in regions] if regions else []
        cover = " and ".join(missing[:2])
        if direction == "Nhập vào VN":
            pts = imp[:2]
        elif direction == "Xuất từ VN":
            pts = export[:2]
        else:
            pts = [export[0], imp[0]]
        pts.append(f"Coverage in {cover}, as a complement to your own Vietnam team" if cover
                   else "Back-up capacity and a second quote when your Vietnam team is busy")
        opening = (f"We understand {company} already has its own presence in Vietnam. "
                   "Many forwarders with a Vietnam office still use a local partner for regions or peaks "
                   "their team doesn't cover, and that is where we would like to help.")
        subject = (f"Vietnam coverage in {missing[0].split(' (')[0]} for {company}" if missing
                   else f"Back-up Vietnam agent for {company}")
        ask = "Would you be open to keeping us as your Vietnam back-up agent and comparing rates on one shipment?"
    elif direction == "Cả nhập & xuất":
        pts = [export[1], imp[0], imp[-1]] if road_china else [export[0], export[-1], imp[0]]
        opening = f"We understand {company} moves cargo between {other} and Vietnam in both directions{via}."
        subject = f"Vietnam agent for your {other.replace('the ', '')} ⇄ Vietnam shipments (import & export)"
        ask = "Could we exchange agency profiles and quote on your next Vietnam shipment, either direction?"
    elif direction == "Xuất từ VN":
        pts = export[:3] if len(export) >= 3 else export
        opening = f"We understand {company} handles cargo shipped from Vietnam{via} to {other}."
        subject = f"Vietnam origin agent for your Vietnam → {other.replace('the ', '')} cargo"
        ask = "Do you have an upcoming Vietnam pick-up we could quote on?"
    elif direction == "Nhập vào VN":
        pts = imp[:3]
        opening = f"We understand {company} ships cargo from {other} into Vietnam{via}."
        subject = f"Vietnam destination agent for your {other.replace('the ', '')} → Vietnam cargo"
        ask = "Do you have a shipment heading to Vietnam we could quote destination charges for?"
    else:
        pts = [export[0], imp[-2], imp[-1]]
        opening = f"We noticed Vietnam is part of {company}'s network."
        subject = f"Vietnam agent partnership – {company}"
        ask = ("Do your Vietnam shipments run mainly into or out of Vietnam? "
               "Happy to quote on the next one either way.")

    greet_name = contact.get("name", "").split()[0] if contact.get("name") else ""
    greeting = f"Dear {greet_name}," if greet_name else f"Dear {company} team,"
    strengths = "Same-business-day quotations and an English-speaking operations team."
    body = "\n".join([
        greeting, "",
        f"{opening}", "",
        f"I'm {profile.sender_name}, {profile.sender_title} at {profile.name}, a Vietnam-based forwarder with "
        f"offices in {_join(profile.offices)}. We would like to support you as your agent in Vietnam:",
        *[f"- {p}" for p in pts],
        "", strengths, "", ask, "",
        "Best regards,",
        profile.sender_name, f"{profile.sender_title} | {profile.name}",
        f"{profile.sender_email} | {profile.website}", "",
        OPT_OUT,
    ])
    followup = "\n".join([
        greeting, "",
        f"Just following up on my note about Vietnam agency support for {company}. "
        + ("If you have any cargo moving to or from Vietnam in the next few weeks, send us the details "
           "and we'll come back with a quote the same business day."),
        "", "Best regards,", profile.sender_name, "", OPT_OUT,
    ])

    to, cc = _pick_recipient(r["Email công khai"], direction, contact.get("email", ""))
    conf = r["Tuyến VN: độ tin cậy"]
    notes = [f"Hạng {r['Hạng']} ({r['Điểm']}đ), chiều tuyến: {direction}, độ tin cậy: {conf or 'n/a'}."]
    notes.append(f"Bằng chứng: {r['Bằng chứng tuyến Việt Nam'][:220]} — nguồn: {r['Tuyến VN: nguồn'] or r['Nguồn']}")
    if conf == "Thấp" or direction == "Chưa rõ":
        notes.append("Cần mở website kiểm tra lại chiều tuyến trước khi gửi.")
    if has_office:
        notes.append("Agent đã có VP/công ty riêng tại VN → chào vùng họ chưa phủ hoặc làm back-up.")
    if r["Red flags"]:
        notes.append(f"Red flags: {r['Red flags']}")
    if contact.get("linkedin"):
        notes.append(f"LinkedIn: {contact['linkedin']}")

    return Draft(r["Domain"].lstrip("﻿"), company, country, r["Hạng"], int(float(r["Điểm"] or 0)), direction,
                 to, cc, contact.get("name", ""), subject, body, followup, " ".join(notes),
                 extra={"website": r["Website"], "phone": r["Điện thoại"].lstrip("'")})


def load_leads(leads_dir: str | Path = "leads") -> tuple[list[dict], dict[str, str]]:
    leads_dir = Path(leads_dir)
    country: dict[str, str] = {}
    for f in glob.glob(str(leads_dir / "*sheet-rows.json")):
        for tab, rows in json.loads(Path(f).read_text(encoding="utf-8"))["tabs"].items():
            for row in rows:
                country[row[0]] = tab
    seen, rows = set(), []
    for f in sorted(glob.glob(str(leads_dir / "20*.csv"))):
        if "candidates" in f:
            continue
        default = "China" if "china" in Path(f).name else ""
        for r in csv.DictReader(open(f, encoding="utf-8-sig")):
            d = r["Domain"]
            if d in seen:
                continue
            seen.add(d)
            country.setdefault(d, default)
            rows.append(r)
    return rows, country


def load_contacts(path: str | Path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    return {r["domain"].strip(): r for r in csv.DictReader(open(p, encoding="utf-8-sig"))}


def build_drafts(profile: CompanyProfile, leads_dir="leads", contacts_path="config/contacts.csv",
                 min_score: int = 35, tiers: str = "ABC") -> tuple[list[Draft], list[Draft]]:
    rows, country = load_leads(leads_dir)
    contacts = load_contacts(contacts_path)
    ok, no_email = [], []
    for r in rows:
        if r["Hạng"] not in tiers or r["Trạng thái"] == "rejected" or float(r["Điểm"] or 0) < min_score:
            continue
        d = compose(r, country.get(r["Domain"], ""), profile, contacts.get(r["Domain"]))
        (ok if d.to else no_email).append(d)
    key = lambda d: (-d.score, d.country, d.company)
    return sorted(ok, key=key), sorted(no_email, key=key)


HEADER = ["Email Address", "CC", "Contact", "Company", "Country", "Tier", "Score", "Tuyến VN: chiều",
          "Subject", "Body", "Follow-up (sau 5 ngày)", "Ghi chú cho sales", "Domain", "Trạng thái gửi"]


def as_row(d: Draft) -> list:
    return [d.to, d.cc, d.contact, d.company, d.country, d.tier, d.score, d.direction, d.subject, d.body,
            d.followup, d.notes_vi, d.domain, "Chưa gửi - chờ duyệt"]


def write_outputs(drafts: list[Draft], no_email: list[Draft], out_dir: str | Path, day: str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"csv": out / f"{day}-email-drafts.csv", "md": out / f"{day}-email-drafts.md",
             "no_email": out / f"{day}-no-email.csv"}
    with open(paths["csv"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(as_row(d) for d in drafts)
    with open(paths["no_email"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Country", "Tier", "Score", "Tuyến VN: chiều", "Website", "Phone", "Subject",
                    "Body (gửi qua form website / LinkedIn)", "Ghi chú cho sales", "Domain"])
        w.writerows([d.company, d.country, d.tier, d.score, d.direction, d.extra["website"], d.extra["phone"],
                     d.subject, d.body, d.notes_vi, d.domain] for d in no_email)
    lines = [f"# Email nháp hàng loạt — {day}", "",
             f"{len(drafts)} email có địa chỉ, {len(no_email)} agent không có email công khai "
             f"(xem `{paths['no_email'].name}`). **Chưa gửi email nào** — duyệt trước khi gửi.", ""]
    for i, d in enumerate(drafts, 1):
        lines += [f"## {i}. {d.company} ({d.country}) — hạng {d.tier}, {d.score}đ", "",
                  f"- **To:** {d.to}" + (f"  \n- **CC (tuỳ chọn):** {d.cc}" if d.cc else ""),
                  f"- **Subject:** {d.subject}", f"- **Ghi chú:** {d.notes_vi}", "", "```text", d.body, "```", ""]
    paths["md"].write_text("\n".join(lines), encoding="utf-8")
    return paths
