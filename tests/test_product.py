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

    def test_open_without_time_keeps_legacy_fields(self):
        ticket = self.app.open_ticket("T", "Alice", "Download")
        self.assertNotIn("opened_at", ticket)
        self.assertNotIn("first_response", ticket)
        self.assertEqual(SupportDesk(self.root).get("T").keys(), ticket.keys())

    def test_open_with_time_and_first_response_flow(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=10)
        ticket = self.app.get("T")
        self.assertEqual((ticket["opened_at"], ticket["first_response"]), (10, None))
        responded = self.app.respond(" T ", "  Hello  ", 10)
        self.assertEqual(responded["first_response"], {"message": "Hello", "responded_at": 10})
        self.assertEqual(responded["status"], "open")
        reloaded = SupportDesk(self.root).get("T")
        self.assertEqual(reloaded["ticket_id"], "T")
        self.assertEqual(reloaded["first_response"]["message"], "Hello")

    def test_respond_rejections_leave_data_unchanged(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=10)
        self.app.respond("T", "Hello", 15)
        self.app.assign("T", "Bob")
        self.app.close("T", "Done")
        reopened = SupportDesk(self.root)
        with self.assertRaises(ValueError):
            reopened.respond("T", "Again", 20)
        with self.assertRaises(ValueError):
            reopened.respond("T", "Hello", 15)
        reopened.open_ticket("U", "Bob", "Login", opened_at=5)
        with self.assertRaises(ValueError):
            reopened.respond("U", "Early", 4)
        reopened.open_ticket("V", "Cara", "Email")
        with self.assertRaises(ValueError):
            reopened.respond("V", "No time", 1)
        with self.assertRaises(ValueError):
            reopened.respond("W", "Missing", 1)
        with self.assertRaises(ValueError):
            reopened.respond("U", "  ", 6)
        for bad in (True, "10", 1.5, -1):
            with self.assertRaises(ValueError):
                reopened.open_ticket("X-" + repr(bad), "Dan", "Fax", opened_at=bad)
        self.assertEqual(reopened.get("U")["first_response"], None)
        self.assertEqual(reopened.get("V").get("opened_at"), None)
        self.assertEqual(reopened.get("T")["first_response"],
                         {"message": "Hello", "responded_at": 15})

    def test_response_stats_counts_and_durations(self):
        self.assertEqual(SupportDesk(self.root / "empty").response_stats(),
                         {"timed": 0, "responded": 0, "pending": 0, "untimed": 0,
                          "average_minutes": None, "max_minutes": None})
        self.app.open_ticket("T1", "a", "s", opened_at=0)
        self.app.open_ticket("T2", "b", "s", opened_at=10)
        self.app.open_ticket("T3", "c", "s")
        self.app.respond("T1", "m", 30)
        self.app.respond("T2", "m", 10)
        self.app.assign("T1", "Bob")
        self.app.close("T1", "fixed")
        stats = self.app.response_stats()
        self.assertEqual((stats["timed"], stats["responded"], stats["pending"], stats["untimed"]), (2, 2, 0, 1))
        self.assertEqual(stats["average_minutes"], 15.0)
        self.assertEqual(stats["max_minutes"], 30)

    def test_stats_cli_takes_no_input_and_does_not_write(self):
        self.app.open_ticket("T1", "a", "s", opened_at=0)
        before = self.app.path.read_bytes()
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-stats"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["timed"], 1)
        self.assertEqual(before, self.app.path.read_bytes())

if __name__ == "__main__":
    unittest.main()
