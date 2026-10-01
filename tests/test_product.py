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

if __name__ == "__main__":
    unittest.main()
