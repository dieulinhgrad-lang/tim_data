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
    assert len(china.rows) == 2 and china.rows[1][14] == 70 and china.rows[1][-1] == "Đã gọi điện"


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
