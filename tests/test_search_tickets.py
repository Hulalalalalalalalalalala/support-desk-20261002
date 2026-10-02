import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support_desk import SupportDesk


class SearchTicketsSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = SupportDesk(self.root)
        # T-1 closed, uncategorized, rich text fields
        self.app.open_ticket("T-1", "Alice Zhang", "Reset PASSWORD ticket issue", opened_at=5)
        self.app.assign("T-1", "Bob")
        self.app.note("T-1", "Checked the LOGS")
        self.app.respond("T-1", "On it: pwreset", 6)
        self.app.reply("T-1", "follow UP body", 7)
        self.app.close("T-1", "Sent reset LINK")
        # T-2 open, category network, knowledge-based first response (snapshot has unique words)
        self.app.open_ticket("T-src", "Eve", "Original source heading", opened_at=1)
        self.app.assign("T-src", "Fay")
        self.app.close("T-src", "sourcebody content")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-2", "Carol", "Billing ticket question", opened_at=5)
        self.app.set_category("T-2", "network")
        self.app.respond_with_knowledge("T-2", "KB-1", 6)
        self.app.reply("T-2", "manual later reply", 7)
        # after the saved response, the article gets title/body words that exist nowhere
        # in any ticket except the snapshot (whose title and new body are not indexed;
        # the saved message keeps the original body)
        self.app.update_knowledge("KB-1", "Snapshottitle magic", "snapshotbody magic")
        # a later knowledge reply to T-2 saves the revised body, so its message is indexed
        self.app.reply_with_knowledge("T-2", "KB-1", 8)
        # T-3 open, untimed, no responses; has internal-whitespace category
        self.app.open_ticket("T-3", "Dan", "No clock ticket")
        self.app.set_category("T-3", "  a  b ")  # stored as "a  b"
        # T-10 checks ticket_id case-sensitive ordering against T-2
        self.app.open_ticket("T-10", "Gus", "Billing ticket thing", opened_at=5)
        self.app.set_category("T-10", "Network")  # different case
        # legacy ticket without category/notes/first_response/replies fields
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({"tickets": {"T-9": {
            "ticket_id": "T-9", "customer": "Legacy Customer", "subject": "OLD style",
            "status": "open", "assignee": None, "notes": [], "resolution": None,
        }}}), encoding="utf-8")
        self.legacy = SupportDesk(legacy)

    def ids(self, result):
        return [t["ticket_id"] for t in result["items"]]

    def test_basic_term_matching_across_fields(self):
        self.assertEqual(self.ids(self.app.search_tickets("alice")), ["T-1"])
        self.assertEqual(self.ids(self.app.search_tickets("PASSWORD")), ["T-1"])  # casefold
        self.assertEqual(self.ids(self.app.search_tickets("logs")), ["T-1"])      # note
        self.assertEqual(self.ids(self.app.search_tickets("pwreset")), ["T-1"])   # first response
        self.assertEqual(self.ids(self.app.search_tickets("follow")), ["T-1"])    # reply
        self.assertEqual(self.ids(self.app.search_tickets("link")), ["T-1"])      # resolution
        # knowledge first response body is searchable via the saved message (original content);
        # that same text also lives in the source ticket's resolution, so both match
        self.assertEqual(self.ids(self.app.search_tickets("sourcebody")), ["T-2", "T-src"])
        # a knowledge reply saved after revision is searchable through its own saved message
        self.assertEqual(self.ids(self.app.search_tickets("snapshotbody")), ["T-2"])
        # follow-up manual reply body is searchable
        self.assertEqual(self.ids(self.app.search_tickets("manual")), ["T-2"])

    def test_each_term_must_hit_a_single_continuous_field_but_terms_split(self):
        # two terms, each found somewhere (different fields allowed)
        result = self.app.search_tickets("alice logs")
        self.assertEqual(self.ids(result), ["T-1"])
        # a term cannot be assembled across fields: "alicebilling" appears nowhere
        self.assertEqual(self.app.search_tickets("alicebilling")["items"], [])
        # a term cannot be assembled across fields: note ends LOGS, subject starts Reset
        self.assertEqual(self.app.search_tickets("logsreset")["items"], [])
        # terms are AND-ed: a term present in no field rejects the ticket
        self.assertEqual(self.app.search_tickets("reset xyzzy")["items"], [])

    def test_punctuation_matches_literally_and_whitespace_split(self):
        self.assertEqual(self.ids(self.app.search_tickets("it:")), ["T-1"])       # "it:" in response
        self.assertEqual(self.app.search_tickets("pwreset,")["items"], [])

    def test_ticket_id_assignee_category_and_kb_snapshot_other_fields_excluded(self):
        # ticket id not searchable
        self.assertEqual(self.app.search_tickets("T-1")["items"], [])
        # assignee not searchable
        self.assertEqual(self.app.search_tickets("Bob")["items"], [])
        # snapshot-only fields (article id / revised title) are not indexed
        self.assertEqual(self.app.search_tickets("KB-1")["items"], [])
        self.assertEqual(self.app.search_tickets("Snapshottitle")["items"], [])
        # the revised title word also never leaked into any ticket field
        self.assertEqual(self.app.search_tickets("snapshottitle")["items"], [])
        # category value itself is not searchable text
        self.assertEqual(self.app.search_tickets("network")["items"], [])

    def test_status_filter(self):
        self.assertEqual(self.ids(self.app.search_tickets("billing", status="open")),
                         ["T-10", "T-2"])
        self.assertEqual(self.ids(self.app.search_tickets("billing", status="closed")), [])
        result = self.app.search_tickets("reset", status=None)
        self.assertEqual(self.ids(result), ["T-1"])
        for bad in ("OPEN", "closed ", 1, True, []):
            with self.assertRaises(ValueError, msg=bad):
                self.app.search_tickets("reset", status=bad)

    def test_category_filter_explicit_null_vs_omitted_and_case(self):
        # omitted -> no category restriction
        self.assertEqual(
            self.ids(self.app.search_tickets("ticket")),
            ["T-1", "T-10", "T-2", "T-3"],
        )
        # explicit null -> only uncategorized
        self.assertEqual(self.ids(self.app.search_tickets("ticket", category=None)), ["T-1"])
        # exact case-sensitive category
        self.assertEqual(self.ids(self.app.search_tickets("billing", category="network")), ["T-2"])
        self.assertEqual(self.ids(self.app.search_tickets("billing", category="Network")), ["T-10"])
        # internal whitespace preserved; end whitespace trimmed
        self.assertEqual(self.ids(self.app.search_tickets("clock", category="a  b")), ["T-3"])
        self.assertEqual(self.ids(self.app.search_tickets("clock", category="  a  b  ")), ["T-3"])
        self.assertEqual(self.app.search_tickets("clock", category="a b")["items"], [])
        for bad in (1, True, [], {}):
            with self.assertRaises(ValueError, msg=bad):
                self.app.search_tickets("clock", category=bad)
        for blank in ("", " ", "  \t "):
            with self.assertRaises(ValueError, msg=repr(blank)):
                self.app.search_tickets("clock", category=blank)

    def test_status_and_category_intersect(self):
        self.assertEqual(
            self.ids(self.app.search_tickets("billing", status="open", category="network")),
            ["T-2"],
        )
        self.assertEqual(
            self.app.search_tickets("billing", status="closed", category="network")["items"],
            [],
        )
        self.assertEqual(
            self.app.search_tickets("reset", status="closed", category=None)["items"],
            self.app.search_tickets("reset", status="closed")["items"],
        )

    def test_sort_then_paginate_and_total(self):
        result = self.app.search_tickets("ticket")
        self.assertEqual(result["total"], 4)
        self.assertEqual(self.ids(result), ["T-1", "T-10", "T-2", "T-3"])
        page = self.app.search_tickets("ticket", offset=1, limit=2)
        self.assertEqual(page["total"], 4)
        self.assertEqual(self.ids(page), ["T-10", "T-2"])
        page = self.app.search_tickets("ticket", offset=4, limit=2)
        self.assertEqual((page["total"], page["items"]), (4, []))
        page = self.app.search_tickets("ticket", offset=10)
        self.assertEqual((page["total"], page["items"]), (4, []))
        self.assertEqual(self.app.search_tickets("ticket", limit=100)["total"], 4)
        self.assertEqual(len(self.app.search_tickets("ticket", limit=1)["items"]), 1)
        self.assertEqual(len(self.app.search_tickets("ticket", offset=3)["items"]), 1)

    def test_default_pagination(self):
        for i in range(25):
            self.app.open_ticket(f"X-{i:02d}", "Paged Customer", "paged subject")
        result = self.app.search_tickets("paged")
        self.assertEqual(result["total"], 25)
        self.assertEqual(len(result["items"]), 20)
        self.assertEqual(self.ids(result)[0], "X-00")

    def test_pagination_validation(self):
        for name, values in {
            "offset": [True, False, 1.0, -0.0, -1, "0", None, 1.5],
            "limit": [True, False, 1.0, 0, -1, "20", None, 101, 1.5, 20.0],
        }.items():
            for bad in values:
                kwargs = {"offset": bad} if name == "offset" else {"limit": bad}
                with self.assertRaises(ValueError, msg=(name, bad)):
                    self.app.search_tickets("ticket", **kwargs)
        # boundaries accepted
        self.app.search_tickets("ticket", offset=0, limit=1)
        self.app.search_tickets("ticket", offset=0, limit=100)

    def test_query_validation_and_signature(self):
        for bad in (None, 1, 1.5, True, [], {}, "", " ", "  \t "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.app.search_tickets(bad)
        with self.assertRaises(TypeError):
            self.app.search_tickets()
        with self.assertRaises(TypeError):
            self.app.search_tickets("x", unknown=1)

    def test_no_matches_no_tickets_and_missing_directory(self):
        self.assertEqual(self.app.search_tickets("zzzz-not-present"),
                         {"total": 0, "items": []})
        empty = SupportDesk(self.root / "empty-dir")
        self.assertEqual(empty.search_tickets("anything"), {"total": 0, "items": []})
        self.assertFalse((self.root / "empty-dir").exists())

    def test_read_only_never_writes_or_backfills(self):
        before = self.app.path.read_bytes()
        self.app.search_tickets("ticket", status="closed", category=None, offset=1, limit=1)
        self.app.search_tickets("zzz")
        with self.assertRaises(ValueError):
            self.app.search_tickets(" ")
        self.assertEqual(before, self.app.path.read_bytes())
        # legacy ticket: missing category treated as uncategorized, no backfill
        legacy_path = self.legacy.path
        before_legacy = legacy_path.read_bytes()
        self.assertEqual(self.ids(self.legacy.search_tickets("legacy")), ["T-9"])
        self.assertEqual(self.ids(self.legacy.search_tickets("legacy", category=None)), ["T-9"])
        self.assertEqual(self.legacy.search_tickets("legacy", category="x")["items"], [])
        self.assertEqual(before_legacy, legacy_path.read_bytes())
        stored = json.loads(legacy_path.read_text(encoding="utf-8"))
        self.assertNotIn("category", stored["tickets"]["T-9"])
        # missing first_response/replies simply contribute no bodies
        self.assertEqual(self.ids(self.legacy.search_tickets("customer")), ["T-9"])

    def test_items_are_full_tickets(self):
        ticket = self.app.search_tickets("pwreset")["items"][0]
        self.assertEqual(ticket, self.app.get("T-1"))

    def test_cli_success_and_failure_contract(self):
        payload = self.root / "q.json"
        payload.write_text(json.dumps({"query": "billing", "status": "open",
                                       "category": "network", "offset": 0, "limit": 5}),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "ticket-search", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(self.ids(value), ["T-2"])
        self.assertEqual(value["total"], 1)

        # missing query -> TypeError -> exit 2, empty stdout, error JSON stderr
        payload.write_text(json.dumps({"status": "open"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "ticket-search", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

        # unknown param, blank query, bad status, bad pagination all exit 2
        for body in ({"query": "x", "extra": 1}, {"query": " "}, {"query": "x", "status": "nope"},
                     {"query": "x", "offset": -1}, {"query": "x", "limit": 0},
                     {"query": "x", "offset": True}, {"query": 1},
                     {"query": "x", "category": 9}):
            payload.write_text(json.dumps(body), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                "ticket-search", str(payload)], text=True, capture_output=True)
            self.assertEqual(r.returncode, 2, body)
            self.assertEqual(r.stdout, "", body)
            self.assertIn("error", json.loads(r.stderr), body)

        # array input works like other commands
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"query": "billing"}, {"query": "nonezzz"}]), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                            "ticket-search", str(batch)], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual([self.ids(row) for row in out], [["T-10", "T-2"], []])
        self.assertEqual([row["total"] for row in out], [2, 0])

        # missing directory: success, empty result, nothing created
        missing = self.root / "missing"
        payload.write_text(json.dumps({"query": "x"}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(missing),
                            "ticket-search", str(payload)], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"total": 0, "items": []})
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
