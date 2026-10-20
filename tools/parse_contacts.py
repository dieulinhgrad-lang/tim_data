"""Parse text copied from a JCtrans agent's "Contact Us" tab into contact rows.

Usage: python3 tools/parse_contacts.py <member_id> <company_name> <pasted.txt> [out.csv]
Appends to data/jctrans_contacts.csv by default, skipping emails already present.
"""
import csv
import os
import re
import sys

FIELDS = ["member_id", "company_name", "contact_name", "title", "email", "phone", "im_id"]
STATUS = re.compile(r"^(Online|Offline|\(Active .*\))$", re.I)
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
PHONE = re.compile(r"^\+?[\d\s()./-]{7,}$")


def parse(text):
    lines = [l.strip() for l in text.splitlines() if l.strip() and l.strip() != "Contact Us"]
    # A new contact starts at a line immediately followed by a status line.
    starts = [i for i in range(len(lines) - 1) if STATUS.match(lines[i + 1]) and not STATUS.match(lines[i])]
    contacts = []
    for n, s in enumerate(starts):
        block = lines[s:starts[n + 1] if n + 1 < len(starts) else len(lines)]
        c = {"contact_name": block[0], "title": "", "email": "", "phone": [], "im_id": []}
        for l in block[1:]:
            if STATUS.match(l):
                continue
            if EMAIL.match(l):
                c["email"] = l
            elif PHONE.match(l):
                c["phone"].append(l)
            elif not c["email"] and not c["title"]:
                c["title"] = l
            else:
                c["im_id"].append(l)
        c["phone"] = "; ".join(c["phone"])
        c["im_id"] = "; ".join(c["im_id"])
        contacts.append(c)
    return contacts


def main():
    member_id, company, src = sys.argv[1:4]
    out = sys.argv[4] if len(sys.argv) > 4 else "data/jctrans_contacts.csv"
    seen = set()
    if os.path.exists(out):
        with open(out, newline="", encoding="utf-8") as f:
            seen = {r["email"].lower() for r in csv.DictReader(f) if r["email"]}
    with open(src, encoding="utf-8") as f:
        rows = parse(f.read())
    new = [r for r in rows if not r["email"] or r["email"].lower() not in seen]
    write_header = not os.path.exists(out)
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for r in new:
            w.writerow({"member_id": member_id, "company_name": company, **r})
    print(f"{len(rows)} parsed, {len(new)} added -> {out}")


if __name__ == "__main__":
    main()
