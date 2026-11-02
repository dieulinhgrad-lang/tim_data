"""Push leads to Google Sheets: one tab per country market, upsert by domain.

Setup (one time):
  1. Google Cloud console -> create a service account, enable "Google Sheets API", download the JSON key.
  2. Share the target spreadsheet with the service account's email (Editor).
  3. export GOOGLE_APPLICATION_CREDENTIALS=/path/key.json  and  GOOGLE_SHEETS_ID=<spreadsheet id>

Only the columns in HEADER are written. Columns added to the right of them (e.g. sales notes) are never
touched, so the sales team can annotate freely.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Protocol

from .models import EmailDraft, Lead, Qualification, VietnamLaneCheck

HEADER = [
    "Domain", "Công ty", "Website", "Thành phố", "Loại hình", "Hiệp hội / Network", "Thành viên hiệp hội?",
    "Dịch vụ", "Phương thức", "Tuyến", "Bằng chứng tuyến Việt Nam",
    "Tuyến VN: chiều", "Tuyến VN: phương thức", "Tuyến VN: cảng / cửa khẩu", "Có VP tại VN?",
    "Tuyến VN: độ tin cậy", "Tuyến VN: tóm tắt", "Tuyến VN: nguồn", "Email công khai", "Điện thoại",
    "Người liên hệ", "Điểm", "Hạng", "Trạng thái", "Góc tiếp cận", "Red flags", "Email gửi tới",
    "Tiêu đề email nháp", "Nguồn", "Cập nhật",
]


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


LAST_COL = _col_letter(len(HEADER))


DIRECTION_VI = {
    "import_to_vn": "Nhập vào VN", "export_from_vn": "Xuất từ VN", "both": "Cả nhập & xuất",
    "none": "Không thấy tuyến VN", "unknown": "Chưa rõ",
}
CONFIDENCE_VI = {"high": "Cao", "medium": "Trung bình", "low": "Thấp"}


def lane_cells(lane: VietnamLaneCheck | None) -> list:
    if lane is None:
        return ["Chưa kiểm tra", "", "", "", "", "", ""]
    return [
        DIRECTION_VI.get(lane.direction.value, lane.direction.value), ", ".join(lane.modes),
        ", ".join(lane.vn_gateways), "Có" if lane.has_vietnam_office else "Không",
        CONFIDENCE_VI.get(lane.confidence.value, lane.confidence.value), lane.summary_vi,
        " ".join(e.url for e in lane.evidence),
    ]


def lead_to_row(lead: Lead, q: Qualification | None, d: EmailDraft | None, status: str,
                score: int | None, tier: str | None, lane: VietnamLaneCheck | None = None) -> list:
    contacts = "; ".join(
        " ".join(x for x in (c.name, f"({c.title})" if c.title else "", c.email or "") if x) for c in lead.contacts
    )
    return [
        lead.domain, lead.company_name, lead.website, lead.city or "", lead.company_type,
        ", ".join(lead.networks), "Có" if lead.networks else "Không",
        "; ".join(lead.services), ", ".join(lead.transport_modes), "; ".join(lead.trade_lanes),
        lead.vietnam_evidence or "", *lane_cells(lane), "; ".join(lead.general_emails), "; ".join(lead.phones), contacts,
        score if score is not None else "", tier or "", status,
        q.outreach_angle if q else "", "; ".join(q.red_flags) if q else "",
        (d.recipient_email or "") if d else "", d.subject if d else "",
        " ".join(lead.source_urls), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    ]


def tab_name(country: str) -> str:
    # Sheet titles cannot contain []:*?/\ and are limited to 100 chars
    clean = "".join(ch for ch in country.strip() if ch not in "[]:*?/\\")
    return clean[:100] or "Unknown"


class Worksheet(Protocol):
    title: str

    def col_values(self, col: int) -> list[str]: ...
    def row_values(self, row: int) -> list[str]: ...
    def update(self, values: list[list], range_name: str) -> object: ...
    def append_rows(self, values: list[list], value_input_option: str = ...) -> object: ...


class Spreadsheet(Protocol):
    def worksheets(self) -> list[Worksheet]: ...
    def add_worksheet(self, title: str, rows: int, cols: int) -> Worksheet: ...


def upsert_tab(ws: Worksheet, rows: list[list]) -> tuple[int, int]:
    """Write rows into one country tab. Existing domains are updated in place, new ones appended."""
    existing = ws.col_values(1)
    if existing and existing[0] == HEADER[0]:
        current = ws.row_values(1)[: len(HEADER)]
        if current != HEADER:
            raise ValueError(
                f"Tab '{ws.title}' has an older column layout. Insert the missing columns so row 1 matches "
                f"sheets.HEADER (sales-note columns then shift right safely), or push to a new tab."
            )
    if not existing or existing[0] != HEADER[0]:
        ws.update(range_name=f"A1:{LAST_COL}1", values=[HEADER])
        existing = [HEADER[0]] + existing[1:] if existing else [HEADER[0]]
    index = {dom: i + 1 for i, dom in enumerate(existing) if i > 0 and dom}  # domain -> 1-based row

    updated, new_rows = 0, []
    for row in rows:
        r = index.get(row[0])
        if r:
            ws.update(range_name=f"A{r}:{LAST_COL}{r}", values=[row])
            updated += 1
        else:
            new_rows.append(row)
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
    return len(new_rows), updated


def push_to_sheets(spreadsheet: Spreadsheet, records: Iterable[tuple[str, list]],
                   only_country: str | None = None) -> dict[str, tuple[int, int]]:
    """records: (country, row). Returns {tab: (added, updated)}."""
    by_tab: dict[str, list[list]] = defaultdict(list)
    for country, row in records:
        if only_country and country.strip().lower() != only_country.strip().lower():
            continue
        by_tab[tab_name(country)].append(row)

    tabs = {ws.title: ws for ws in spreadsheet.worksheets()}
    result = {}
    for title, rows in sorted(by_tab.items()):
        ws = tabs.get(title) or spreadsheet.add_worksheet(title=title, rows=max(200, len(rows) + 50), cols=len(HEADER) + 5)
        result[title] = upsert_tab(ws, rows)
    return result


def store_records(store) -> list[tuple[str, list]]:
    out = []
    for r in store.all():
        lead = Lead.model_validate_json(r["lead_json"])
        q = Qualification.model_validate_json(r["qual_json"]) if r["qual_json"] else None
        d = EmailDraft.model_validate_json(r["draft_json"]) if r["draft_json"] else None
        lane = VietnamLaneCheck.model_validate_json(r["lane_json"]) if r["lane_json"] else None
        out.append((lead.country, lead_to_row(lead, q, d, r["status"], r["score"], r["tier"], lane)))
    return out


def open_spreadsheet(spreadsheet_id: str | None = None, credentials_path: str | None = None):
    """Open a spreadsheet with a service-account key (requires `pip install gspread`)."""
    import gspread  # optional dependency

    spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEETS_ID")
    credentials_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not spreadsheet_id:
        raise SystemExit("Missing spreadsheet id: pass --sheet-id or set GOOGLE_SHEETS_ID")
    if not credentials_path:
        raise SystemExit("Missing credentials: pass --credentials or set GOOGLE_APPLICATION_CREDENTIALS")
    return gspread.service_account(filename=credentials_path).open_by_key(spreadsheet_id)


def known_domains_from_sheet(spreadsheet: Spreadsheet) -> set[str]:
    """All domains already present in any tab (column A). The Sheet is the long-term memory across daily runs,
    because the local SQLite file lives in an ephemeral container."""
    domains: set[str] = set()
    for ws in spreadsheet.worksheets():
        values = ws.col_values(1)
        if values and values[0] == HEADER[0]:
            domains.update(v.strip().lower() for v in values[1:] if v and v.strip())
    return domains


def rows_for_connector(records: list[tuple[str, list]]) -> dict[str, list[list]]:
    """Rows grouped by tab, ready for a Google Sheets connector that writes USER_ENTERED values:
    text that Sheets would parse as a formula or number (phones like +86-..., =, -, @) is prefixed with '."""
    out: dict[str, list[list]] = defaultdict(list)
    for country, row in records:
        safe = [("'" + c) if isinstance(c, str) and c != "" and c[:1] in "+=-@(0123456789" and HEADER[i] in TEXT_COLUMNS else c
                for i, c in enumerate(row)]
        out[tab_name(country)].append(safe)
    return dict(out)


TEXT_COLUMNS = {"Điện thoại", "Email công khai", "Người liên hệ", "Tuyến VN: tóm tắt", "Bằng chứng tuyến Việt Nam"}
