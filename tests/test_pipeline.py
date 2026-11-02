"""Offline tests: a fake Anthropic client drives the full pipeline without network calls."""

from types import SimpleNamespace as NS

import pytest

from forwarder_agent.config import Campaign, load_campaigns, load_profile
from forwarder_agent.discovery import SAVE_FORWARDER_TOOL, run_discovery
from forwarder_agent.models import EmailDraft, Lead, LeadStatus, Qualification, Tier, normalize_domain
from forwarder_agent.outreach import run_outreach
from forwarder_agent.qualify import run_qualification
from forwarder_agent.store import LeadStore


def lead_input(name, website, country="Germany", **kw):
    base = {
        "company_name": name, "website": website, "country": country, "city": "Hamburg",
        "company_type": "freight forwarder", "services": ["sea FCL"], "transport_modes": ["sea"],
        "trade_lanes": ["Asia-Europe"], "vietnam_evidence": "Weekly LCL from Ho Chi Minh City",
        "networks": ["WCA"], "general_emails": ["info@" + normalize_domain(website)], "phones": [],
        "linkedin_url": None, "contacts": [], "source_urls": [website], "notes": None,
    }
    base.update(kw)
    return base


def tool_use(i, inp):
    return NS(type="tool_use", id=f"tu_{i}", name="save_forwarder", input=inp)


class FakeMessages:
    def __init__(self, turns, qual=None, draft=None):
        self.turns = list(turns)
        self.calls = []
        self.qual, self.draft = qual, draft

    def create(self, **kw):
        self.calls.append(kw)
        content, stop = self.turns.pop(0) if self.turns else ([NS(type="text", text="done")], "end_turn")
        return NS(content=content, stop_reason=stop)

    def parse(self, **kw):
        self.calls.append(kw)
        out = self.qual if kw["output_format"] is Qualification else self.draft
        return NS(parsed_output=out, stop_reason="end_turn")


def fake_client(*a, **kw):
    return NS(beta=NS(messages=FakeMessages(*a, **kw)))


@pytest.fixture
def store():
    return LeadStore(":memory:")


@pytest.fixture
def profile():
    return load_profile()


def test_configs_load():
    campaigns = load_campaigns()
    assert "germany_sea_air" in campaigns
    assert load_profile().services


def test_normalize_domain():
    assert normalize_domain("https://www.ABC-Logistics.de/en/") == "abc-logistics.de"
    assert normalize_domain("abc.com") == "abc.com"


def test_strict_tool_schema_requires_every_property():
    schema = SAVE_FORWARDER_TOOL["input_schema"]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_discovery_saves_dedupes_and_filters(store, profile):
    turns = [
        ([NS(type="server_tool_use", id="s1", name="web_search", input={}),
          tool_use(1, lead_input("Alpha Spedition", "https://www.alpha.de")),
          tool_use(2, lead_input("Alpha again", "alpha.de/contact")),          # duplicate domain
          tool_use(3, lead_input("VN Co", "https://vnco.com.vn", "Vietnam"))],  # Vietnamese -> skip
         "tool_use"),
        ([], "pause_turn"),
        ([tool_use(4, lead_input("Beta Freight", "https://beta-freight.de"))], "tool_use"),
        ([NS(type="text", text="Saved 2 companies.")], "end_turn"),
    ]
    client = fake_client(turns)
    c = Campaign(key="de", country="Germany", focus="sea", target_count=10)
    saved = run_discovery(c, profile, store, client=client)

    assert saved == ["alpha.de", "beta-freight.de"]
    assert store.known_domains("germany") == ["alpha.de", "beta-freight.de"]
    calls = client.beta.messages.calls
    assert calls[0]["fallbacks"] == "default"
    assert calls[0]["model"] == "claude-opus-5-5"
    # tool results for one turn go back in a single user message
    first_results = calls[1]["messages"][2]
    assert first_results["role"] == "user" and len(first_results["content"]) == 3


def test_discovery_stops_at_target(store, profile):
    turns = [([tool_use(1, lead_input("A", "a.de")), tool_use(2, lead_input("B", "b.de"))], "tool_use")]
    client = fake_client(turns)
    saved = run_discovery(Campaign(key="de", country="Germany", focus="x", target_count=2), profile, store, client)
    assert len(saved) == 2
    # messages list is shared and the final assistant summary is appended after the call
    last = client.beta.messages.calls[-1]["messages"][-2]["content"]
    assert last[-1]["type"] == "text" and "Target reached" in last[-1]["text"]


def test_qualify_and_draft_flow(store, profile, tmp_path):
    store.upsert_lead(Lead.model_validate(lead_input("Alpha", "alpha.de")))
    store.upsert_lead(Lead.model_validate(lead_input("Weak", "weak.de")))
    good = Qualification(score=80, tier=Tier.A, is_forwarder=True, vietnam_fit="LCL from HCMC",
                         recommended_services=["LCL"], outreach_angle="Your HCMC LCL", red_flags=[], reasons=["x"])
    draft = EmailDraft(subject="Vietnam agent", body="Dear team ...", follow_up_1="f1", follow_up_2="f2",
                       recipient_email="info@alpha.de", notes_vi="ghi chú")
    client = fake_client([], qual=good, draft=draft)

    assert run_qualification(store, profile, client) == 2
    store.save_qualification("weak.de", good.model_copy(update={"score": 20, "tier": Tier.D}))
    assert store.get("weak.de")["status"] == LeadStatus.REJECTED.value

    assert run_outreach(store, profile, client, min_score=55) == 1
    assert store.get("alpha.de")["status"] == LeadStatus.DRAFTED.value

    assert store.export_csv(tmp_path / "leads.csv") == 2
    assert store.export_drafts_markdown(tmp_path / "drafts.md") == 1
    assert "info@alpha.de" in (tmp_path / "drafts.md").read_text(encoding="utf-8")


def test_merge_on_rediscovery(store):
    store.upsert_lead(Lead.model_validate(lead_input("Alpha", "alpha.de", networks=["WCA"])))
    is_new = store.upsert_lead(Lead.model_validate(lead_input("Alpha", "www.alpha.de", networks=["JCtrans"])))
    assert not is_new
    lead = Lead.model_validate_json(store.get("alpha.de")["lead_json"])
    assert lead.networks == ["WCA", "JCtrans"]


def test_import_csv(store, tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("company_name,website,country,networks\nGamma,gamma.co.uk,United Kingdom,WCA;FIATA\n,,,\n",
                 encoding="utf-8")
    assert store.import_csv(p, "fair") == (1, 0)
    lead = Lead.model_validate_json(store.get("gamma.co.uk")["lead_json"])
    assert lead.networks == ["WCA", "FIATA"] and lead.campaign == "fair"


# ---------------- Google Sheets ----------------
from forwarder_agent.models import is_company_website
from forwarder_agent.sheets import HEADER, push_to_sheets, store_records


class FakeWS:
    def __init__(self, title, rows=None):
        self.title, self.rows = title, rows or []

    def col_values(self, col):
        return [r[col - 1] for r in self.rows]

    def row_values(self, row):
        return self.rows[row - 1]

    def update(self, values, range_name):
        row = int("".join(ch for ch in range_name.split(":")[0] if ch.isdigit()))
        while len(self.rows) < row:
            self.rows.append([])
        self.rows[row - 1] = list(values[0]) + self.rows[row - 1][len(values[0]):]

    def append_rows(self, values, value_input_option="RAW"):
        self.rows.extend(list(v) for v in values)


class FakeSpreadsheet:
    def __init__(self):
        self.tabs = []

    def worksheets(self):
        return self.tabs

    def add_worksheet(self, title, rows, cols):
        ws = FakeWS(title)
        self.tabs.append(ws)
        return ws


def test_push_to_sheets_one_tab_per_country_and_upsert(store):
    store.upsert_lead(Lead.model_validate(lead_input("Alpha", "alpha.de")))
    store.upsert_lead(Lead.model_validate(lead_input("Sino", "sino56.cn", "China", networks=[])))
    ss = FakeSpreadsheet()
    assert push_to_sheets(ss, store_records(store)) == {"China": (1, 0), "Germany": (1, 0)}
    china = next(t for t in ss.tabs if t.title == "China")
    assert china.rows[0] == HEADER
    assert china.rows[1][0] == "sino56.cn" and china.rows[1][6] == "Không"  # no association is fine

    # sales adds a note in an extra column; a re-push updates in place and keeps the note
    china.rows[1].append("Đã gọi điện")
    store.save_qualification("sino56.cn", Qualification(
        score=70, tier=Tier.B, is_forwarder=True, vietnam_fit="x", recommended_services=[], outreach_angle="y",
        red_flags=[], reasons=[]))
    assert push_to_sheets(ss, store_records(store), only_country="china") == {"China": (0, 1)}
    assert len(china.rows) == 2 and china.rows[1][HEADER.index("Điểm")] == 70 and china.rows[1][-1] == "Đã gọi điện"


def test_company_website_required():
    assert is_company_website("https://www.gowin88.cn/en/")
    assert not is_company_website("https://shenzhencompass.en.made-in-china.com/product/x")
    assert not is_company_website("https://linkedin.com/company/abc")
    assert not is_company_website("gxfangyun.qy6.com.cn")


def test_discovery_rejects_directory_listing(store, profile):
    turns = [([tool_use(1, lead_input("Compass", "https://compass.en.made-in-china.com", "China"))], "tool_use")]
    client = fake_client(turns)
    saved = run_discovery(Campaign(key="cn", country="China", focus="x", target_count=5), profile, store, client)
    assert saved == []
    result = client.beta.messages.calls[1]["messages"][2]["content"][0]
    assert result["is_error"] and "OWN website" in result["content"]


# ---------------- Vietnam lane check (Google AI + Claude) ----------------
from forwarder_agent.google_ai import GroundedResearch
from forwarder_agent.models import Confidence, DiscoveredCompany, DiscoveredList, LaneDirection, LaneEvidence, VietnamLaneCheck
from forwarder_agent.vietnam_lanes import check_lead, research_prompt, run_lane_checks


LANE = VietnamLaneCheck(
    direction=LaneDirection.BOTH, modes=["cross-border road", "sea LCL"], vn_gateways=["Huu Nghi", "Cat Lai"],
    counterpart_gateways=["Pingxiang"], commodities=["electronics"], frequency_hint="daily trucks",
    has_vietnam_office=False, vietnam_partners=[],
    evidence=[LaneEvidence(claim="China-Vietnam line", url="https://sino56.cn/vn", source="company_site")],
    confidence=Confidence.HIGH, sources_agree=True, summary_vi="Có tuyến cả hai chiều.",
)


class FakeGeminiModels:
    def __init__(self, text="Gemini notes", uris=("https://sino56.cn/vn",)):
        self.text, self.uris, self.calls = text, uris, []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        chunks = [NS(web=NS(uri=u, title="t", domain=None)) for u in self.uris]
        meta = NS(web_search_queries=["q"], grounding_chunks=chunks)
        return NS(text=self.text, candidates=[NS(grounding_metadata=meta)])


class LaneClaude(FakeMessages):
    def parse(self, **kw):
        self.calls.append(kw)
        if kw["output_format"] is VietnamLaneCheck:
            return NS(parsed_output=LANE, stop_reason="end_turn")
        if kw["output_format"] is DiscoveredList:
            return NS(parsed_output=self.discovered, stop_reason="end_turn")
        return super().parse(**kw)


def test_research_prompt_uses_local_language_terms():
    lead = Lead.model_validate(lead_input("Sino", "sino56.cn", "China"))
    p = research_prompt(lead)
    assert "越南专线" in p and "site:sino56.cn" in p


def test_check_lead_combines_gemini_and_claude(store):
    lead = Lead.model_validate(lead_input("Sino", "sino56.cn", "China"))
    claude = NS(beta=NS(messages=LaneClaude([([NS(type="text", text="Claude notes", citations=None)], "end_turn")])))
    gemini = NS(models=FakeGeminiModels())
    result = check_lead(lead, ("gemini", "claude"), claude_client=claude, gemini_client=gemini)
    assert result.direction == LaneDirection.BOTH
    judge_input = claude.beta.messages.calls[-1]["messages"][0]["content"]
    assert "Google Gemini" in judge_input and "Claude + web search" in judge_input and "https://sino56.cn/vn" in judge_input


def test_run_lane_checks_stores_and_exports(store):
    store.upsert_lead(Lead.model_validate(lead_input("Sino", "sino56.cn", "China")))
    claude = NS(beta=NS(messages=LaneClaude([])))
    assert run_lane_checks(store, providers=("claude",), claude_client=claude) == 1
    assert store.lane_check("sino56.cn").direction == LaneDirection.BOTH
    assert run_lane_checks(store, providers=("claude",), claude_client=claude) == 0  # already checked
    row = dict(store_records(store))["China"]
    assert row[HEADER.index("Tuyến VN: chiều")] == "Cả nhập & xuất"
    assert row[HEADER.index("Tuyến VN: độ tin cậy")] == "Cao"


def test_gemini_discovery_requires_own_website(store, profile):
    from forwarder_agent.discovery import run_gemini_discovery
    found = DiscoveredList(companies=[
        DiscoveredCompany(company_name="Good", website="https://good56.cn", city="Shenzhen", networks=[],
                          vietnam_evidence="Vietnam line", general_emails=[], phones=[], source_urls=[]),
        DiscoveredCompany(company_name="NoSite", website=None, city=None, networks=["WCA"], vietnam_evidence=None,
                          general_emails=[], phones=[], source_urls=[]),
        DiscoveredCompany(company_name="Listing", website="https://x.en.made-in-china.com", city=None, networks=[],
                          vietnam_evidence=None, general_emails=[], phones=[], source_urls=[]),
    ])
    msgs = LaneClaude([])
    msgs.discovered = found
    saved = run_gemini_discovery(Campaign(key="cn", country="China", focus="x", target_count=5), profile, store,
                                 claude_client=NS(beta=NS(messages=msgs)), gemini_client=NS(models=FakeGeminiModels()))
    assert saved == ["good56.cn"]


def test_sheet_with_old_layout_is_not_overwritten(store):
    store.upsert_lead(Lead.model_validate(lead_input("Sino", "sino56.cn", "China")))
    ss = FakeSpreadsheet()
    ss.tabs.append(FakeWS("China", [["Domain", "Công ty", "old layout"]]))
    with pytest.raises(ValueError, match="older column layout"):
        push_to_sheets(ss, store_records(store))


# ---------------- Daily run ----------------
import json as _json
from datetime import date, datetime, timezone

from forwarder_agent import daily as daily_mod
from forwarder_agent.cli import main as cli_main
from forwarder_agent.daily import DailyConfig, local_day, plan_quotas, redistribute, run_daily
from forwarder_agent.sheets import known_domains_from_sheet, rows_for_connector


def test_daily_config_quota_sums_to_target():
    cfg = daily_mod.load_daily()
    assert cfg.total_per_day == 50 and sum(cfg.quota.values()) == cfg.total_per_day
    assert set(cfg.quota) <= set(load_campaigns())


def test_plan_quotas_scales_and_redistributes():
    cfg = DailyConfig(spreadsheet_id=None, total_per_day=100, quota={"a": 3, "b": 1, "zzz": 5})
    plan = plan_quotas(cfg, ["a", "b"], total=10)
    assert plan == {"a": 8, "b": 2} or sum(plan.values()) == 10
    assert sum(plan_quotas(cfg, ["a", "b"], total=7).values()) == 7
    # 'a' filled its quota, 'b' did not -> the shortfall goes to 'a'
    assert redistribute(3, {"a": 8, "b": 0}, {"a": 8, "b": 2}) == {"a": 3}
    assert redistribute(3, {"a": 1}, {"a": 8}) == {}


def test_local_day_uses_vietnam_time():
    # 2026-11-02 20:30 UTC is already 2026-11-03 in Vietnam
    assert local_day(datetime(2026, 11, 2, 20, 30, tzinfo=timezone.utc).isoformat()) == date(2026, 11, 3)


def test_known_domains_from_sheet_and_connector_rows(store):
    ss = FakeSpreadsheet()
    ss.tabs.append(FakeWS("China", [HEADER, ["a56.cn"], ["B56.cn "]]))
    ss.tabs.append(FakeWS("Notes", [["something else"], ["x.com"]]))
    assert known_domains_from_sheet(ss) == {"a56.cn", "b56.cn"}
    store.upsert_lead(Lead.model_validate(lead_input("Sino", "sino56.cn", "China", phones=["+86-123"])))
    tabs = rows_for_connector(store_records(store))
    assert tabs["China"][0][HEADER.index("Điện thoại")] == "'+86-123"
    assert tabs["China"][0][0] == "sino56.cn"


def test_run_daily_end_to_end(store, profile, tmp_path, monkeypatch):
    import forwarder_agent.discovery as disc
    import forwarder_agent.outreach as outr
    import forwarder_agent.qualify as qual
    import forwarder_agent.vietnam_lanes as vl

    calls = []

    def fake_discovery(c, profile, store, client, extra_known=frozenset()):
        calls.append((c.key, c.target_count, set(extra_known)))
        n = c.target_count if c.key == "china" else 0  # only China has supply
        out = []
        for i in range(n):
            d = f"{c.key}{len(calls)}-{i}.cn"
            if d not in extra_known:
                store.upsert_lead(Lead.model_validate(lead_input(f"Co {d}", d, c.country)))
                out.append(d)
        return out

    monkeypatch.setattr(disc, "run_discovery", fake_discovery)
    monkeypatch.setattr(vl, "run_lane_checks", lambda *a, **k: 0)
    monkeypatch.setattr(qual, "run_qualification", lambda *a, **k: 0)
    monkeypatch.setattr(outr, "run_outreach", lambda *a, **k: 0)

    ss = FakeSpreadsheet()
    ss.tabs.append(FakeWS("China", [HEADER, ["old56.cn"]]))
    cfg = DailyConfig(spreadsheet_id="x", total_per_day=10, quota={"china": 6, "japan": 4})
    today = daily_mod.local_today()
    result = run_daily(store, profile, cfg, client=None, provider="claude", spreadsheet=ss, day=today,
                       out_dir=tmp_path)
    assert result["produced"] == {"china": 10, "japan": 0}  # Japan's 4 moved to China in round 2
    assert "old56.cn" in calls[0][2]  # Sheet domains are passed as already-known
    assert (tmp_path / f"{today.isoformat()}.csv").exists()
    assert "đạt: **10**" in result["report"]
    assert result["pushed"]["China"][0] == 10


def test_cli_import_lanes_and_sheet_rows(tmp_path):
    db = tmp_path / "t.db"
    csvp = tmp_path / "in.csv"
    csvp.write_text("company_name,website,country\nSino,sino56.cn,China\n", encoding="utf-8")
    cli_main(["--db", str(db), "import-csv", str(csvp)])
    lanes = tmp_path / "lanes.json"
    lanes.write_text(_json.dumps({"https://www.sino56.cn": LANE.model_dump(mode="json")}), encoding="utf-8")
    cli_main(["--db", str(db), "import-lanes", str(lanes)])
    out = tmp_path / "rows.json"
    cli_main(["--db", str(db), "sheet-rows", "--out", str(out)])
    payload = _json.loads(out.read_text(encoding="utf-8"))
    row = payload["tabs"]["China"][0]
    assert row[payload["header"].index("Tuyến VN: chiều")] == "Cả nhập & xuất"


def test_cli_import_quals_and_daily_report(tmp_path):
    db = tmp_path / "t.db"
    csvp = tmp_path / "in.csv"
    csvp.write_text("company_name,website,country\nSino,sino56.cn,China\n", encoding="utf-8")
    cli_main(["--db", str(db), "import-csv", str(csvp)])
    q = Qualification(score=80, tier=Tier.A, is_forwarder=True, vietnam_fit="x", recommended_services=[],
                      outreach_angle="y", red_flags=[], reasons=[])
    qp = tmp_path / "q.json"
    qp.write_text(_json.dumps({"sino56.cn": q.model_dump(mode="json")}), encoding="utf-8")
    cli_main(["--db", str(db), "import-quals", str(qp)])
    cli_main(["--db", str(db), "daily-report", "--out-leads", str(tmp_path / "leads"), "--target", "1"])
    day = daily_mod.local_today().isoformat()
    assert "Sino" in (tmp_path / "leads" / f"{day}.csv").read_text(encoding="utf-8-sig")
    assert "đạt: **1**" in (tmp_path / "leads" / f"{day}-report.md").read_text(encoding="utf-8")
