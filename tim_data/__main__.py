"""CLI: python -m tim_data <template> [options] [--out file.csv|file.json]

Examples:
  python -m tim_data maps --keywords "nha khoa quận 1" --language vi --country vn --out data/nha_khoa.csv
  python -m tim_data amazon-search --keywords "wireless earbuds" --limit 30 --out data/earbuds.json
  python -m tim_data amazon-reviews --asin B0XXXXXXX
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .browseract import AuthError, BrowserActClient, BrowserActError, to_records
from .templates import TEMPLATES


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tim_data", description="Collect data via BrowserAct templates")
    sub = p.add_subparsers(dest="template", required=True)
    for name, tpl in TEMPLATES.items():
        sp = sub.add_parser(name, help=tpl.description)
        for opt, (_, default) in tpl.params.items():
            sp.add_argument(f"--{opt}", required=default is None, default=default)
        sp.add_argument("--out", help="Write results to .csv or .json (default: print JSON)")
        sp.add_argument("--timeout", type=float, default=600, help="Max seconds to wait")
    return p


def save(records: list[dict], out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fields: list[str] = []
        for r in records:
            fields += [k for k in r if k not in fields]
        with path.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: opens cleanly in Excel
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(records)
    else:
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tpl = TEMPLATES[args.template]
    params = {api_name: getattr(args, opt) for opt, (api_name, _) in tpl.params.items()}
    log = lambda msg: print(msg, file=sys.stderr, flush=True)
    try:
        client = BrowserActClient(max_poll_time=args.timeout, log=log)
        try:
            result = client.run(tpl.template_id, params)
        except AuthError:
            raise
        except BrowserActError as e:  # upstream skills recommend exactly one retry
            log(f"First attempt failed ({e}); retrying once")
            result = client.run(tpl.template_id, params)
    except BrowserActError as e:
        log(f"Error: {e}")
        return 2 if isinstance(e, AuthError) else 1

    records = to_records(result)
    if args.out:
        save(records, args.out)
        log(f"Saved {len(records)} rows -> {args.out}")
    else:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
