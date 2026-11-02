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
    python -m forwarder_agent push-sheets --sheet-id <ID> [--country China]   # mỗi quốc gia 1 tab
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

    if args.cmd in {"discover", "qualify", "draft", "run", "check-lanes"}:
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
    elif args.cmd == "stats":
        for status, n in sorted(store.stats().items()):
            print(f"{status:15} {n}")


if __name__ == "__main__":
    main()
