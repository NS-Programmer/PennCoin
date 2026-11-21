"""Unit tests for the plain-code core (standard library unittest).

Run: python -I -m unittest discover -s tests -v   (from the project folder)
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from rules import SanctionsList, State, due_date, match_day, validate  # noqa: E402
from drafter import TemplateDrafter  # noqa: E402
from ninoo import record_ors_id  # noqa: E402
from run_day import ISSUER  # noqa: E402

DATA = ROOT / "data"
LAZARUS = "0x098B716B8Aaf21512996dC57EB0615e2383E2f96"


class Deadlines(unittest.TestCase):
    def test_presidents_day_skipped(self):
        self.assertEqual(due_date("2027-02-01"), "2027-02-16")

    def test_thanksgiving_skipped(self):
        self.assertEqual(due_date("2026-11-23"), "2026-12-08")

    def test_mlk_day_2027(self):
        # Jan 18, 2027 (GENIUS effective date) is MLK Day
        self.assertEqual(due_date("2027-01-15"), "2027-02-01")


class Matching(unittest.TestCase):
    def setUp(self):
        self.sdn = SanctionsList(DATA)

    def test_exact_match_only(self):
        self.assertIsNotNone(self.sdn.entry(LAZARUS, "2027-02-01"))
        self.assertIsNotNone(self.sdn.entry(LAZARUS.lower(), "2027-02-01"))
        lookalike = LAZARUS[:-1] + "7"
        self.assertIsNone(self.sdn.entry(lookalike, "2027-02-01"))

    def test_overlay_timing(self):
        self.assertIsNone(self.sdn.entry(LAZARUS, "2027-01-31"))  # simulated: not yet listed


class ValidatorAndDrafts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events = json.loads((DATA / "events.json").read_text())
        initial = json.loads((DATA / "initial_state.json").read_text())
        cls.issuer = dict(ISSUER, token_ethereum=initial["tokens"]["ethereum"], token_polygon=initial["tokens"]["polygon"])
        cls.state = State(initial)
        cls.filing = match_day("2027-02-01", events, SanctionsList(DATA), cls.state)["C01"]["filings"][0]
        cls.good = TemplateDrafter().draft(cls.filing, cls.issuer)

    def check(self, draft):
        return validate(self.filing, draft, self.issuer, self.state.reported)

    def test_good_draft_passes(self):
        self.assertEqual(self.check(self.good), [])

    def test_sdn_as_authority_rejected(self):
        d = copy.deepcopy(self.good)
        d["fields"]["legal_authority"] = "SDN"
        self.assertTrue(any("SDN" in p for p in self.check(d)))

    def test_missing_field_rejected(self):
        d = copy.deepcopy(self.good)
        d["fields"]["usd_value"] = ""
        self.assertTrue(any("usd_value" in p for p in self.check(d)))

    def test_backdated_or_moved_deadline_rejected(self):
        d = copy.deepcopy(self.good)
        d["due"] = "2027-03-01"
        self.assertTrue(any("due date" in p for p in self.check(d)))

    def test_wrong_filing_type_rejected(self):
        d = copy.deepcopy(self.good)
        d["filing_type"] = "rejected_transaction"
        self.assertTrue(any("disagrees" in p for p in self.check(d)))

    def test_other_holders_address_rejected(self):
        d = copy.deepcopy(self.good)
        d["explanation"] += " Also see 0x67d40EE1A85bf4a4Bb7Ffae16De985e8427B6b45."
        self.assertTrue(any("not in this case" in p for p in self.check(d)))

    def test_second_blocked_report_rejected(self):
        reported = dict(self.state.reported)
        reported[LAZARUS.lower()] = "SIM-ORS-9999"
        problems = validate(self.filing, self.good, self.issuer, reported)
        self.assertTrue(any("D1" in p for p in problems))


class Filing(unittest.TestCase):
    def test_no_filed_status_without_ors_id(self):
        item = {"status": "approved"}
        with self.assertRaises(ValueError):
            record_ors_id(item, "  ", "maya")
        self.assertEqual(record_ors_id(item, "ORS-123", "maya")["status"], "filed")

    def test_must_approve_first(self):
        with self.assertRaises(ValueError):
            record_ors_id({"status": "awaiting_review"}, "ORS-123", "maya")


if __name__ == "__main__":
    unittest.main()
