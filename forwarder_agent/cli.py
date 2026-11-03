"""Command line interface.

    python -m forwarder_agent discover --campaign us_transpacific
    python -m forwarder_agent discover --country "Netherlands" --focus "sea LCL importers from Asia" --max 10
    python -m forwarder_agent qualify
    python -m forwarder_agent draft --min-score 60
    python -m forwarder_agent run --campaign germany_sea_air     # discover -> qualify -> draft -> export
    python -m forwarder_agent export
    python -m forwarder_agent import-csv my_list.csv --campaign fair_2026
    python -m forwarder_agent set-status abc-logistics.de contacted
    python -m forwarder_agent stats
    python -m forwarder_agent discover --campaign china --provider both      # Claude + Google Gemini
    python -m forwarder_agent check-lanes --country China                     # có tuyến nhập/xuất VN không?
    python -m forwarder_agent qualify --requalify                             # chấm lại sau khi kiểm tra tuyến
    python -m forwarder_agent daily --provider both                          # 50 agent mới mỗi ngày (config/daily.toml)
    python -m forwarder_agent import-lanes lanes.json                         # nạp kết quả kiểm tra tuyến VN
    python -m forwarder_agent sheet-rows --since 2026-11-03 --out rows.json   # dòng để ghi qua connector
    python -m forwarder_agent push-sheets --sheet-id <ID> [--country China]   # mỗi quốc gia 1 tab
    python -m forwarder_agent bulk-drafts                                     # email nháp hàng loạt từ leads/*.csv (không gửi)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import DEFAULT_CAMPAIGNS, DEFAULT_PROFILE, Campaign, load_campaigns, load_profile
from .models import LeadStatus, normalize_domain
from .store import LeadStore


def _campaign_from_args(args) -> Campaign:
    if args.campaign:
        campaigns = load_campaigns(Path(args.campaigns))
        if args.campaign not in campaigns:
            sys.exit(f"Unknown campaign '{args.campaign}'. Available: {', '.join(campaigns)}")
        c = campaigns[args.campaign]
        if args.max:
            c.target_count = args.max
        return c
    if not args.country:
        sys.exit("Provide --campaign or --country")
    key = f"adhoc_{args.country.lower().replace(' ', '_')}"
    return Campaign(
        key=key,
        country=args.country,
        focus=args.focus or "freight forwarders handling sea/air cargo with Asia, ideally Vietnam",
        target_count=args.max or 15,
    )


def _export(store: LeadStore, out_dir: Path) -> None:
    n = store.export_csv(out_dir / "leads.csv")
    m = store.export_drafts_markdown(out_dir / "drafts.md")
    print(f"Exported {n} leads -> {out_dir / 'leads.csv'}; {m} drafts -> {out_dir / 'drafts.md'}")


def _push_sheets(store: LeadStore, sheet_id: str | None, credentials: str | None, country: str | None) -> None:
    from .sheets import open_spreadsheet, push_to_sheets, store_records

    result = push_to_sheets(open_spreadsheet(sheet_id, credentials), store_records(store), only_country=country)
    for tab, (added, updated) in result.items():
        print(f"Google Sheets tab '{tab}': +{added} new, {updated} updated")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="forwarder_agent", description="AI agent tìm forwarder nước ngoài")
    p.add_argument("--db", default="data/leads.db")
    p.add_argument("--profile", default=str(DEFAULT_PROFILE))
    p.add_argument("--campaigns", default=str(DEFAULT_CAMPAIGNS))
    p.add_argument("--out-dir", default="output")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_campaign_args(sp):
        sp.add_argument("--campaign", help="key in config/campaigns.toml")
        sp.add_argument("--country")
        sp.add_argument("--focus")
        sp.add_argument("--max", type=int, help="target number of new companies")
        sp.add_argument("--provider", choices=["claude", "gemini", "both"], default="claude",
                        help="who searches: Claude web search, Google Gemini + Google Search, or both")

    add_campaign_args(sub.add_parser("discover", help="tìm forwarder mới bằng web search"))
    sp = sub.add_parser("qualify", help="chấm điểm lead mới")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--requalify", action="store_true", help="chấm lại cả lead đã chấm")
    sp = sub.add_parser("check-lanes", help="kiểm tra agent có tuyến nhập/xuất Việt Nam (Google AI + Claude)")
    sp.add_argument("--country")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--recheck", action="store_true", help="kiểm tra lại cả lead đã kiểm tra")
    sp.add_argument("--providers", default="gemini,claude", help="gemini,claude | gemini | claude")
    sp = sub.add_parser("draft", help="soạn email nháp cho lead đạt điểm")
    sp.add_argument("--min-score", type=int, default=55)
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("run", help="discover -> qualify -> draft -> export")
    add_campaign_args(sp)
    sp.add_argument("--all-campaigns", action="store_true")
    sp.add_argument("--min-score", type=int, default=55)
    sp.add_argument("--providers", default="gemini,claude", help="lane-check researchers")
    sp.add_argument("--sheet-id", help="also push results to this Google Sheet (or set GOOGLE_SHEETS_ID)")
    sp.add_argument("--credentials", help="service-account JSON (or GOOGLE_APPLICATION_CREDENTIALS)")
    sp = sub.add_parser("push-sheets", help="đẩy dữ liệu lên Google Sheets, mỗi quốc gia một tab")
    sp.add_argument("--sheet-id")
    sp.add_argument("--credentials")
    sp.add_argument("--country", help="chỉ đẩy một quốc gia, ví dụ China")
    sub.add_parser("export", help="xuất CSV + Markdown email nháp")
    sp = sub.add_parser("import-csv", help="nhập danh sách có sẵn")
    sp.add_argument("path")
    sp.add_argument("--campaign")
    sp = sub.add_parser("set-status", help="cập nhật trạng thái sau khi gửi/phản hồi")
    sp.add_argument("domain")
    sp.add_argument("status", choices=[s.value for s in LeadStatus])
    sub.add_parser("stats")
    sp = sub.add_parser("daily", help="mỗi ngày tìm N agent mới (mặc định theo config/daily.toml: 50) và đẩy lên Google Sheets")
    sp.add_argument("--total", type=int, help="ghi đè total_per_day trong config/daily.toml")
    sp.add_argument("--provider", choices=["claude", "gemini", "both"], default="both")
    sp.add_argument("--daily-config", default=None)
    sp.add_argument("--credentials")
    sp.add_argument("--no-sheets", action="store_true")
    sp = sub.add_parser("import-lanes", help="nạp kết quả kiểm tra tuyến VN từ JSON {domain: VietnamLaneCheck}")
    sp.add_argument("path")
    sp = sub.add_parser("import-quals", help="nạp điểm chấm từ JSON {domain: Qualification}")
    sp.add_argument("path")
    sp = sub.add_parser("daily-report", help="ghi leads/<ngày>.csv và báo cáo cho các lead tạo hôm nay")
    sp.add_argument("--day", help="YYYY-MM-DD (mặc định: hôm nay theo giờ Việt Nam)")
    sp.add_argument("--target", type=int, help="mặc định: total_per_day trong config/daily.toml")
    sp.add_argument("--out-leads", default="leads")
    sp = sub.add_parser("bulk-drafts", help="soạn email nháp hàng loạt từ leads/*.csv theo nhu cầu từng agent (không gửi)")
    sp.add_argument("--leads-dir", default="leads")
    sp.add_argument("--contacts", default="config/contacts.csv", help="CSV domain,name,title,email,linkedin")
    sp.add_argument("--min-score", type=int, default=35)
    sp.add_argument("--tiers", default="ABC")
    sp.add_argument("--out", default="leads/outreach")
    sp.add_argument("--day", help="tên file, mặc định hôm nay")
    sp = sub.add_parser("sheet-rows", help="xuất JSON {tab: rows} để ghi lên Sheets qua connector")
    sp.add_argument("--since", help="chỉ lead tạo từ ngày này (YYYY-MM-DD)")
    sp.add_argument("--out", default="-")

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    store = LeadStore(args.db)
    out_dir = Path(args.out_dir)

    def discover(c):
        found = []
        if args.provider in ("claude", "both"):
            found += run_discovery(c, profile, store, client)
        if args.provider in ("gemini", "both"):
            found += run_gemini_discovery(c, profile, store, client)
        return sorted(set(found))

    if args.cmd in {"discover", "qualify", "draft", "run", "check-lanes", "daily"}:
        # import lazily so export/stats work without the anthropic package configured
        from .discovery import run_discovery, run_gemini_discovery
        from .vietnam_lanes import run_lane_checks
        from .llm import make_client
        from .outreach import run_outreach
        from .qualify import run_qualification

        profile = load_profile(Path(args.profile))
        client = make_client()

    if args.cmd == "discover":
        c = _campaign_from_args(args)
        saved = discover(c)
        print(f"[{c.key}] saved {len(saved)} companies: {', '.join(saved)}")
    elif args.cmd == "check-lanes":
        n = run_lane_checks(store, providers=args.providers.split(","), country=args.country, limit=args.limit,
                            recheck=args.recheck, claude_client=client)
        print(f"Checked Vietnam lanes for {n} leads")
    elif args.cmd == "qualify":
        print(f"Qualified {run_qualification(store, profile, client, limit=args.limit, requalify=args.requalify)} leads")
    elif args.cmd == "draft":
        print(f"Drafted {run_outreach(store, profile, client, min_score=args.min_score, limit=args.limit)} emails")
    elif args.cmd == "run":
        campaigns = list(load_campaigns(Path(args.campaigns)).values()) if args.all_campaigns else [
            _campaign_from_args(args)
        ]
        for c in campaigns:
            saved = discover(c)
            print(f"[{c.key}] saved {len(saved)} companies")
            n = run_lane_checks(store, providers=args.providers.split(","), country=c.country, claude_client=client)
            print(f"[{c.key}] checked Vietnam lanes for {n} leads")
        print(f"Qualified {run_qualification(store, profile, client)} leads")
        print(f"Drafted {run_outreach(store, profile, client, min_score=args.min_score)} emails")
        _export(store, out_dir)
        if args.sheet_id or os.environ.get("GOOGLE_SHEETS_ID"):
            _push_sheets(store, args.sheet_id, args.credentials, None)
    elif args.cmd == "push-sheets":
        _push_sheets(store, args.sheet_id, args.credentials, args.country)
    elif args.cmd == "export":
        _export(store, out_dir)
    elif args.cmd == "import-csv":
        new, merged = store.import_csv(args.path, args.campaign)
        print(f"Imported {new} new leads, merged {merged} existing")
    elif args.cmd == "set-status":
        ok = store.set_status(normalize_domain(args.domain), LeadStatus(args.status))
        print("Updated" if ok else f"Domain not found: {args.domain}")
    elif args.cmd == "daily":
        from .daily import DEFAULT_DAILY, load_daily, run_daily

        cfg = load_daily(Path(args.daily_config) if args.daily_config else DEFAULT_DAILY)
        spreadsheet = None
        if not args.no_sheets:
            from .sheets import open_spreadsheet

            spreadsheet = open_spreadsheet(os.environ.get("GOOGLE_SHEETS_ID") or cfg.spreadsheet_id, args.credentials)
        result = run_daily(store, profile, cfg, client, provider=args.provider, total=args.total,
                           spreadsheet=spreadsheet)
        print(result["report"])
        print(f"CSV: {result['csv']}")
        for tab, (added, updated) in result["pushed"].items():
            print(f"Google Sheets tab '{tab}': +{added} new, {updated} updated")
    elif args.cmd == "import-lanes":
        import json

        from .models import VietnamLaneCheck

        data = json.loads(Path(args.path).read_text(encoding="utf-8"))
        ok = 0
        for domain, lane in data.items():
            d = normalize_domain(domain)
            if store.get(d):
                store.save_lane_check(d, VietnamLaneCheck.model_validate(lane))
                ok += 1
            else:
                print(f"skip {domain}: not in database (import-csv first)")
        print(f"Imported {ok} lane checks")
    elif args.cmd == "import-quals":
        import json

        from .models import Qualification

        data = json.loads(Path(args.path).read_text(encoding="utf-8"))
        ok = 0
        for domain, q in data.items():
            d = normalize_domain(domain)
            if store.get(d):
                store.save_qualification(d, Qualification.model_validate(q))
                ok += 1
            else:
                print(f"skip {domain}: not in database (import-csv first)")
        print(f"Imported {ok} qualifications")
    elif args.cmd == "daily-report":
        from collections import Counter
        from datetime import date as _date

        from .daily import load_daily, local_today, report, todays_leads, write_daily_csv

        day = _date.fromisoformat(args.day) if args.day else local_today()
        out = Path(args.out_leads)
        csv_path = write_daily_csv(store, day, out)
        per_market = Counter(r["country"] for r in todays_leads(store, day))
        text = report(store, day, args.target or load_daily().total_per_day, dict(per_market))
        (out / f"{day.isoformat()}-report.md").write_text(text, encoding="utf-8")
        print(text)
        print(f"CSV: {csv_path}")
    elif args.cmd == "bulk-drafts":
        from collections import Counter
        from datetime import date as _date

        from .mailmerge import build_drafts, write_outputs

        drafts, no_email = build_drafts(load_profile(Path(args.profile)), args.leads_dir, args.contacts,
                                        args.min_score, args.tiers)
        paths = write_outputs(drafts, no_email, args.out, args.day or _date.today().isoformat())
        print(f"{len(drafts)} email drafts, {len(no_email)} without public email (nothing sent)")
        print("Theo chiều tuyến:", dict(Counter(d.direction for d in drafts)))
        for k, v in paths.items():
            print(f"{k}: {v}")
    elif args.cmd == "sheet-rows":
        import json

        from datetime import date as _date

        from .daily import local_day
        from .models import Lead as _Lead
        from .sheets import HEADER, rows_for_connector, store_records

        keep = None
        if args.since:
            since = _date.fromisoformat(args.since)
            keep = {r["domain"] for r in store.all()
                    if local_day(_Lead.model_validate_json(r["lead_json"]).created_at) >= since}
        recs = [(c, row) for c, row in store_records(store) if keep is None or row[0] in keep]
        payload = json.dumps({"header": HEADER, "tabs": rows_for_connector(recs)}, ensure_ascii=False)
        if args.out == "-":
            print(payload)
        else:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"Wrote {len(recs)} rows -> {args.out}")
    elif args.cmd == "stats":
        for status, n in sorted(store.stats().items()):
            print(f"{status:15} {n}")


if __name__ == "__main__":
    main()
