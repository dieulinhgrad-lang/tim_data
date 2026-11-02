import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tim_data import AuthError, BrowserActClient, BrowserActError, to_records
from tim_data.__main__ import main, save


class FakeResp:
    def __init__(self, data):
        self.data, self.status_code, self.text = data, 200, json.dumps(data)

    def json(self):
        return self.data


class FakeSession:
    """Replays a scripted sequence of API responses and records calls."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append((method, url.rsplit("/", 1)[-1], kw))
        return FakeResp(self.responses.pop(0))


def client(responses, **kw):
    return BrowserActClient(
        api_key="k", session=FakeSession(responses), log=lambda m: None, sleep=lambda s: None, **kw
    )


ROWS = [{"Title Name": "Nha khoa A", "Rating": "4.8"}, {"Title Name": "Nha khoa B", "Phone number": "028"}]


class ClientTests(unittest.TestCase):
    def test_finished_task_returns_result(self):
        # Upstream bug: this path printed "timed out" and returned None.
        c = client([
            {"id": "t1"},
            {"status": "running"},
            {"status": "finished"},
            {"output": {"string": json.dumps(ROWS)}},
        ])
        self.assertEqual(c.run("tpl", {"keywords": "nha khoa", "country": "vn"}), ROWS)
        method, path, kw = c.session.calls[0]
        self.assertEqual((method, path), ("POST", "run-task-by-template"))
        self.assertEqual(kw["json"]["input_parameters"][1], {"name": "country", "value": "vn"})
        self.assertEqual(c.session.calls[-1][1], "get-task")

    def test_failed_task_raises(self):
        c = client([{"id": "t1"}, {"status": "failed"}])
        with self.assertRaises(BrowserActError):
            c.run("tpl", {})

    def test_invalid_key_raises_auth_error(self):
        c = client([{"msg": "Invalid authorization"}])
        with self.assertRaises(AuthError):
            c.run("tpl", {})

    def test_missing_key(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(AuthError):
            BrowserActClient()

    def test_timeout(self):
        c = client([{"id": "t1"}] + [{"status": "running"}] * 50, max_poll_time=0)
        with self.assertRaises(BrowserActError):
            c.run("tpl", {})

    def test_non_json_output_kept_raw(self):
        c = client([{"id": "t1"}, {"status": "finished"}, {"output": {"string": "plain text"}}])
        self.assertEqual(c.run("tpl", {}), "plain text")


class RecordsTests(unittest.TestCase):
    def test_to_records(self):
        self.assertEqual(to_records(ROWS), ROWS)
        self.assertEqual(to_records({"data": ROWS}), ROWS)
        self.assertEqual(to_records("x"), [{"value": "x"}])

    def test_save_csv_merges_columns(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "sub" / "out.csv"
            save(ROWS, str(out))
            with out.open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0]), ["Title Name", "Rating", "Phone number"])
            self.assertEqual(rows[1]["Phone number"], "028")


class CliTests(unittest.TestCase):
    def test_cli_maps_to_json(self):
        responses = [{"id": "t1"}, {"status": "finished"}, {"output": {"string": json.dumps(ROWS)}}]
        fake = FakeSession(responses)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict("os.environ", {"BROWSERACT_API_KEY": "k"}), \
                mock.patch("tim_data.browseract.requests.Session", return_value=fake):
            out = Path(d) / "maps.json"
            code = main(["maps", "--keywords", "nha khoa", "--country", "vn", "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), ROWS)
        params = fake.calls[0][2]["json"]["input_parameters"]
        self.assertEqual([p["name"] for p in params], ["keywords", "language", "country"])

    def test_cli_requires_keywords(self):
        with self.assertRaises(SystemExit):
            main(["amazon-search"])


if __name__ == "__main__":
    unittest.main()
