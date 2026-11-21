"""Nicrron wiring, tested offline with a fake `openai` client (no key or network needed)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ["DATA_DIR"] = str(ROOT / "data")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))

from drafter import DEFAULT_MODEL, NicrronDrafter, TemplateDrafter  # noqa: E402
from env import load_env  # noqa: E402
from run_day import make_drafter  # noqa: E402

try:
    from fastapi.testclient import TestClient
    import api
except ImportError:  # pragma: no cover
    TestClient = None


class FakeOpenAI:
    """Stands in for openai.OpenAI; returns whatever `reply` is set to and records the call."""
    reply = "{}"
    calls = []

    def __init__(self, base_url=None, api_key=None, **kw):
        self.base_url, self.api_key = base_url, api_key
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature):
        FakeOpenAI.calls.append({"base_url": self.base_url, "model": model, "temperature": temperature})
        usage = types.SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 50})
        msg = types.SimpleNamespace(content=FakeOpenAI.reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=usage)


def fake_openai_module():
    return types.SimpleNamespace(OpenAI=FakeOpenAI)


class Wiring(unittest.TestCase):
    def test_default_model_is_sonnet_5_5(self):
        self.assertEqual(DEFAULT_MODEL, "anthropic/claude-sonnet-5-5")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NICRRON_MODEL", None)
            self.assertEqual(make_drafter("nicrron").model, "anthropic/claude-sonnet-5-5")
        self.assertEqual(make_drafter("nicrron:openai/gpt-6-sol").model, "openai/gpt-6-sol")

    def test_env_file_loads_but_real_environment_wins(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# comment\nexport ZZ_A="one"\nZZ_B=two\nZZ_C=from-file\n')
            with mock.patch.dict(os.environ, {"ZZ_C": "from-env"}):
                loaded = load_env(p)
                self.assertEqual((os.environ["ZZ_A"], os.environ["ZZ_B"], os.environ["ZZ_C"]), ("one", "two", "from-env"))
                self.assertEqual(sorted(loaded), ["ZZ_A", "ZZ_B"])
            for k in ("ZZ_A", "ZZ_B"):
                os.environ.pop(k, None)

    def test_calls_nicrron_like_the_quickstart(self):
        FakeOpenAI.calls, FakeOpenAI.reply = [], '```json\n{"filing_type": "x"}\n```'
        with mock.patch.dict(sys.modules, {"openai": fake_openai_module()}), \
             mock.patch.dict(os.environ, {"NICRRON_API_KEY": "test-key"}):
            d = NicrronDrafter()
            out = d.draft({"type": "blocked_property", "due": "2027-02-16", "facts": {}}, {})
        self.assertEqual(out, {"filing_type": "x"})  # code fence stripped
        call = FakeOpenAI.calls[0]
        self.assertEqual((call["base_url"], call["model"], call["temperature"]),
                         ("https://api.nicrron.ai/v1", "anthropic/claude-sonnet-5-5", 0))
        self.assertEqual((d.stats["model_calls"], d.stats["prompt_tokens"]), (1, 100))

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NICRRON_API_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "NICRRON_API_KEY"):
                NicrronDrafter().call_nicrron([])


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class RedraftEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(api.app)

    def setUp(self):
        self.c.post("/api/simulate/reset")
        self.item = self.c.get("/api/queue").json()[0]          # C01 blocked property report
        r = api.run("2027-02-01")
        filing = r["filings"][0]
        self.good = json.dumps(TemplateDrafter().draft(filing, r["issuer"]))
        self.env = {"NICRRON_API_KEY": "test-key", "DEMO_ACCESS_CODE": "letmein"}

    def post(self, headers=None, **body):
        return self.c.post(f"/api/queue/{self.item['item_id']}/redraft", json={"day": "2027-02-01", **body},
                           headers=headers or {})

    def test_models_endpoint_lists_sonnet_and_hides_key(self):
        with mock.patch.dict(os.environ, self.env):
            m = self.c.get("/api/models").json()
        self.assertEqual(m["default"], "anthropic/claude-sonnet-5-5")
        self.assertEqual(m["models"][0]["label"], "Claude Sonnet 5.5")
        self.assertTrue(m["live_drafts_enabled"])
        self.assertNotIn("test-key", json.dumps(m))

    def test_locked_without_server_settings_or_code(self):
        with mock.patch.dict(os.environ, {"NICRRON_API_KEY": "test-key"}):
            os.environ.pop("DEMO_ACCESS_CODE", None)
            self.assertEqual(self.post({"X-Access-Code": "x"}).status_code, 503)
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(self.post().status_code, 401)
            self.assertEqual(self.post({"X-Access-Code": "wrong"}).status_code, 401)
            self.assertEqual(self.post({"X-Access-Code": "letmein"}, model="someone/expensive").status_code, 400)

    def test_sonnet_redraft_is_validated(self):
        FakeOpenAI.calls, FakeOpenAI.reply = [], self.good
        with mock.patch.dict(sys.modules, {"openai": fake_openai_module()}), mock.patch.dict(os.environ, self.env):
            api._recent_model_calls.clear()
            r = self.post({"X-Access-Code": "letmein"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["model_label"], body["validator"], body["attempts"]), ("Claude Sonnet 5.5", "passed", 1))
        self.assertEqual(FakeOpenAI.calls[0]["model"], "anthropic/claude-sonnet-5-5")
        self.assertEqual(self.c.get(f"/api/queue/{self.item['item_id']}").json()["status"], "awaiting_review")

    def test_bad_model_draft_is_caught_then_sent_to_a_human(self):
        bad = json.loads(self.good)
        bad["fields"]["legal_authority"] = "SDN"
        FakeOpenAI.calls, FakeOpenAI.reply = [], json.dumps(bad)
        with mock.patch.dict(sys.modules, {"openai": fake_openai_module()}), mock.patch.dict(os.environ, self.env):
            api._recent_model_calls.clear()
            body = self.post({"X-Access-Code": "letmein"}).json()
        self.assertEqual((body["validator"], body["attempts"], len(FakeOpenAI.calls)), ("needs_human_fix", 2, 2))
        self.assertTrue(any("SDN" in p for p in body["problems"]))

    def test_rate_limit(self):
        FakeOpenAI.reply = self.good
        with mock.patch.dict(sys.modules, {"openai": fake_openai_module()}), mock.patch.dict(os.environ, self.env), \
             mock.patch.object(api, "MODEL_CALLS_PER_MINUTE", 2):
            api._recent_model_calls.clear()
            codes = [self.post({"X-Access-Code": "letmein"}).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


if __name__ == "__main__":
    unittest.main()
