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
"""

from __future__ import annotations

import argparse
import logging
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

    add_campaign_args(sub.add_parser("discover", help="tìm forwarder mới bằng web search"))
    sp = sub.add_parser("qualify", help="chấm điểm lead mới")
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("draft", help="soạn email nháp cho lead đạt điểm")
    sp.add_argument("--min-score", type=int, default=55)
    sp.add_argument("--limit", type=int)
    sp = sub.add_parser("run", help="discover -> qualify -> draft -> export")
    add_campaign_args(sp)
    sp.add_argument("--all-campaigns", action="store_true")
    sp.add_argument("--min-score", type=int, default=55)
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

    if args.cmd in {"discover", "qualify", "draft", "run"}:
        # import lazily so export/stats work without the anthropic package configured
        from .discovery import run_discovery
        from .llm import make_client
        from .outreach import run_outreach
        from .qualify import run_qualification

        profile = load_profile(Path(args.profile))
        client = make_client()

    if args.cmd == "discover":
        c = _campaign_from_args(args)
        saved = run_discovery(c, profile, store, client)
        print(f"[{c.key}] saved {len(saved)} companies: {', '.join(saved)}")
    elif args.cmd == "qualify":
        print(f"Qualified {run_qualification(store, profile, client, limit=args.limit)} leads")
    elif args.cmd == "draft":
        print(f"Drafted {run_outreach(store, profile, client, min_score=args.min_score, limit=args.limit)} emails")
    elif args.cmd == "run":
        campaigns = list(load_campaigns(Path(args.campaigns)).values()) if args.all_campaigns else [
            _campaign_from_args(args)
        ]
        for c in campaigns:
            saved = run_discovery(c, profile, store, client)
            print(f"[{c.key}] saved {len(saved)} companies")
        print(f"Qualified {run_qualification(store, profile, client)} leads")
        print(f"Drafted {run_outreach(store, profile, client, min_score=args.min_score)} emails")
        _export(store, out_dir)
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
