import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from support_desk import SupportDesk

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = SupportDesk(self.root)

    def test_assignment_note_closure_and_reopen(self):
        self.app.open_ticket("T", "Alice", "Download")
        self.app.assign("T", "Bob")
        self.app.note("T", "Checked")
        self.app.close("T", "Sent link")
        value = SupportDesk(self.root).get("T")
        self.assertEqual((value["status"], value["notes"]), ("closed", ["Checked"]))

    def test_unassigned_and_empty_resolution_cannot_close(self):
        self.app.open_ticket("T", "Alice", "Download")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.close("T", "Sent link")
        with self.assertRaises(ValueError):
            self.app.close("T", " ")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_closed_ticket_is_not_mutated_and_filter_works(self):
        self.app.open_ticket("T", "Alice", "Download")
        self.app.assign("T", "Bob")
        self.app.close("T", "Done")
        with self.assertRaises(ValueError):
            self.app.note("T", "Late")
        self.assertEqual(self.app.list_tickets("open"), [])

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual((value["status"], value["assignee"]), ("closed", "小林"))
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

    def test_response_queue_waits_and_overdue_boundary(self):
        self.app.open_ticket("T-9", "Alice", "A", opened_at=9)
        self.app.open_ticket("T-10", "Bob", "B", opened_at=10)
        self.app.open_ticket("T-40", "Cara", "C", opened_at=40)
        queue = self.app.response_queue(40)
        self.assertEqual(queue["untimed"], 0)
        self.assertEqual([(i["ticket"]["ticket_id"], i["waiting_minutes"], i["overdue"]) for i in queue["items"]],
                         [("T-9", 31, True), ("T-10", 30, False), ("T-40", 0, False)])
        self.assertEqual(queue["items"][0]["ticket"], self.app.get("T-9"))

    def test_response_queue_skips_closed_responded_and_counts_untimed(self):
        self.app.open_ticket("T-open", "Alice", "A", opened_at=5)
        self.app.open_ticket("T-untimed", "Bob", "B")
        self.app.open_ticket("T-responded", "Cara", "C", opened_at=5)
        self.app.respond("T-responded", "On it", 6)
        self.app.open_ticket("T-closed", "Dan", "D", opened_at=5)
        self.app.assign("T-closed", "Eve")
        self.app.close("T-closed", "Done")
        queue = self.app.response_queue(10)
        self.assertEqual(queue["untimed"], 1)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-open"])

    def test_response_queue_sorts_same_minute_by_ticket_id(self):
        self.app.open_ticket("T-b", "Alice", "A", opened_at=3)
        self.app.open_ticket("T-a", "Bob", "B", opened_at=3)
        queue = self.app.response_queue(5)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-a", "T-b"])

    def test_response_queue_rejects_future_opened_at_and_bad_arguments(self):
        self.app.open_ticket("T", "Alice", "A", opened_at=50)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_queue(40)
        for as_of, target in [(True, 30), (1.5, 30), ("40", 30), (None, 30), (-1, 30), (40, 0), (40, -5), (40, True), (40, 30.0), (40, "30"), (40, None)]:
            with self.assertRaises(ValueError, msg=(as_of, target)):
                self.app.response_queue(as_of, target)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_response_queue_empty_directory_creates_no_file(self):
        queue = self.app.response_queue(0)
        self.assertEqual(queue, {"untimed": 0, "items": []})
        self.assertFalse(self.app.path.exists())

    def test_cli_response_queue(self):
        self.app.open_ticket("T-9", "Alice", "A", opened_at=9)
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"as_of": 40}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-queue", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([(i["ticket"]["ticket_id"], i["waiting_minutes"], i["overdue"]) for i in value["items"]], [("T-9", 31, True)])
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"as_of": -1}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-queue", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))

    def _closed_ticket(self, ticket_id="T", subject="Download", resolution="Sent link"):
        self.app.open_ticket(ticket_id, "Alice", subject)
        self.app.assign(ticket_id, "Bob")
        self.app.close(ticket_id, resolution)

    def test_set_priority_persists_and_repeat_is_quiet(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=5)
        self.app.assign("T", "Bob")
        self.app.note("T", "Checked")
        self.app.respond("T", "On it", 6)
        ticket = self.app.set_priority(" T ", " high ")
        self.assertEqual(ticket["priority"], "high")
        self.assertEqual((ticket["status"], ticket["assignee"], ticket["notes"], ticket["resolution"]),
                         ("open", "Bob", ["Checked"], None))
        self.assertEqual(ticket["first_response"], {"message": "On it", "responded_at": 6})
        again = self.app.set_priority("T", "high")
        self.assertEqual(again["notes"], ["Checked"])
        reloaded = SupportDesk(self.root).get("T")
        self.assertEqual(reloaded["priority"], "high")

    def test_priority_queue_orders_and_keeps_ticket_raw(self):
        self.app.open_ticket("T-normal-2", "Alice", "A")
        self.app.open_ticket("T-urgent", "Bob", "B", opened_at=5)
        self.app.open_ticket("T-low", "Cara", "C")
        self.app.open_ticket("T-high", "Dan", "D")
        self.app.open_ticket("T-normal-1", "Eve", "E")
        self.app.set_priority("T-urgent", "urgent")
        self.app.set_priority("T-high", "high")
        self.app.set_priority("T-low", "low")
        # responded, unassigned and untimed tickets all stay in the queue
        self.app.respond("T-urgent", "Seen", 6)
        closed = "T-closed"
        self.app.open_ticket(closed, "Fay", "F")
        self.app.assign(closed, "Gus")
        self.app.close(closed, "Done")
        queue = self.app.priority_queue()
        self.assertEqual([(item["ticket"]["ticket_id"], item["priority"]) for item in queue],
                         [("T-urgent", "urgent"), ("T-high", "high"),
                          ("T-normal-1", "normal"), ("T-normal-2", "normal"), ("T-low", "low")])
        self.assertNotIn("priority", queue[2]["ticket"])
        self.assertEqual(queue[0]["ticket"], self.app.get("T-urgent"))

    def test_set_priority_rejects_bad_input_without_writing(self):
        self.app.open_ticket("T-open", "Alice", "Open")
        self._closed_ticket("T-closed")
        before = self.app.path.read_bytes()
        for ticket_id, priority in [(None, "high"), (1, "high"), (" ", "high"), ("T-open", None),
                                   ("T-open", 1), ("T-open", " "), ("T-open", "HIGH"),
                                   ("T-open", "critical"), ("missing", "high"), ("T-closed", "high")]:
            with self.assertRaises(ValueError, msg=(ticket_id, priority)):
                self.app.set_priority(ticket_id, priority)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertNotIn("priority", self.app.get("T-open"))

    def test_priority_queue_empty_creates_nothing(self):
        self.assertEqual(self.app.priority_queue(), [])
        self.assertFalse(self.app.path.exists())
        self._closed_ticket("T-closed")
        self.assertEqual(self.app.priority_queue(), [])

    def test_cli_priority_set_and_queue(self):
        self.app.open_ticket("T-1", "Alice", "A")
        self.app.open_ticket("T-2", "Bob", "B")
        payload = self.root / "priorities.json"
        payload.write_text(json.dumps([{"ticket_id": "T-2", "priority": "urgent"},
                                       {"ticket_id": "missing", "priority": "high"}]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "priority-set", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(SupportDesk(self.root).get("T-2")["priority"], "urgent")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "priority-queue"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(i["ticket"]["ticket_id"], i["priority"]) for i in json.loads(result.stdout)],
                         [("T-2", "urgent"), ("T-1", "normal")])

    def test_publish_knowledge_persists_and_source_untouched(self):
        self._closed_ticket()
        entry = self.app.publish_knowledge(" KB-1 ", "T")
        self.assertEqual(entry, {"article_id": "KB-1", "source_ticket_id": "T", "title": "Download", "content": "Sent link"})
        again = SupportDesk(self.root)
        self.assertEqual(again.search_knowledge(), [entry])
        self.assertEqual(again.get("T")["status"], "closed")

    def test_publish_knowledge_from_untimed_closed_ticket(self):
        self.app.open_ticket("T", "Alice", "No clock")
        self.app.assign("T", "Bob")
        self.app.close("T", "Fixed")
        entry = self.app.publish_knowledge("KB-1", "T")
        self.assertEqual((entry["title"], entry["content"]), ("No clock", "Fixed"))

    def test_publish_knowledge_rejects_bad_input_without_writing(self):
        self._closed_ticket()
        self.app.open_ticket("T-open", "Alice", "Open")
        before = self.app.path.read_bytes()
        for article_id, ticket_id in [(None, "T"), (1, "T"), (" ", "T"), ("KB", None), ("KB", 1), ("KB", " "),
                                      ("KB", "missing"), ("KB", "T-open")]:
            with self.assertRaises(ValueError, msg=(article_id, ticket_id)):
                self.app.publish_knowledge(article_id, ticket_id)
        self.assertEqual(before, self.app.path.read_bytes())
        self.app.publish_knowledge("KB", "T")
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB", "T")
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB-2", "T")

    def test_search_knowledge_terms_casefold_and_field_split(self):
        self._closed_ticket("T-1", subject="Reset PASSWORD", resolution="Sent reset link")
        self._closed_ticket("T-2", subject="Billing", resolution="Refunded the PASSWORD reset fee")
        self.app.publish_knowledge("KB-2", "T-2")
        self.app.publish_knowledge("KB-1", "T-1")
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge()], ["KB-1", "KB-2"])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge(None)], ["KB-1", "KB-2"])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("  password  RESET ")], ["KB-1", "KB-2"])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("billing")], ["KB-2"])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("password billing")], ["KB-2"])
        self.assertEqual(self.app.search_knowledge("password missing"), [])
        self.assertEqual(self.app.search_knowledge("link,"), [])

    def test_search_knowledge_rejects_bad_query_and_never_writes(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        before = self.app.path.read_bytes()
        for query in [1, 1.5, True, [], {}, " ", "  \t "]:
            with self.assertRaises(ValueError, msg=query):
                self.app.search_knowledge(query)
        self.app.search_knowledge("nothing matches")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_search_knowledge_empty_directory_creates_no_file(self):
        self.assertEqual(self.app.search_knowledge(), [])
        self.assertEqual(self.app.search_knowledge("anything"), [])
        self.assertFalse(self.app.path.exists())

    def test_ticket_changes_preserve_knowledge_entries(self):
        self._closed_ticket("T-1")
        self.app.publish_knowledge("KB-1", "T-1")
        self.app.open_ticket("T-2", "Alice", "Other")
        self.app.assign("T-2", "Bob")
        self.app.note("T-2", "Checking")
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge()], ["KB-1"])

    def test_cli_knowledge_publish_and_search(self):
        self._closed_ticket()
        payload = self.root / "publish.json"
        payload.write_text(json.dumps([{"article_id": "KB-2", "ticket_id": "T"}, {"article_id": "KB-1", "ticket_id": "T"}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-publish", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge()], ["KB-2"])
        query = self.root / "query.json"
        query.write_text(json.dumps({"query": None}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-search", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["article_id"] for e in json.loads(result.stdout)], ["KB-2"])
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-search"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["article_id"] for e in json.loads(result.stdout)], ["KB-2"])

if __name__ == "__main__":
    unittest.main()
