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

    def _responded(self, ticket_id, at, priority=None):
        self.app.open_ticket(ticket_id, "Alice", "A", opened_at=0)
        self.app.respond(ticket_id, "hi", at)
        if priority is not None:
            self.app.set_priority(ticket_id, priority)

    def test_follow_up_queue_last_answer_waiting_and_overdue_boundary(self):
        self._responded("T-first-only", 10)
        self.app.reply("T-first-only", "again", 70)
        queue = self.app.follow_up_queue(130)
        self.assertEqual(set(queue), {"as_of", "items"})
        self.assertEqual(queue["as_of"], 130)
        only = next(i for i in queue["items"] if i["ticket"]["ticket_id"] == "T-first-only")
        self.assertEqual((only["last_answered_at"], only["waiting_minutes"], only["overdue"], only["priority"]),
                         (70, 60, False, "normal"))
        self.assertEqual(set(only), {"ticket", "priority", "last_answered_at", "waiting_minutes", "overdue"})
        self.assertEqual(only["ticket"], self.app.get("T-first-only"))
        self.assertTrue(next(i for i in self.app.follow_up_queue(131)["items"]
                             if i["ticket"]["ticket_id"] == "T-first-only")["overdue"])

    def test_follow_up_queue_uses_first_response_when_replies_missing_or_empty(self):
        self._responded("T-no-replies", 20)
        data = self.app._read()
        data["tickets"]["T-no-replies"]["replies"] = []
        self.app._write(data)
        ticket = self.app.open_ticket("T-untimed", "Bob", "B")
        data = self.app._read()
        data["tickets"]["T-untimed"]["first_response"] = {"message": "m", "responded_at": 30}
        self.app._write(data)
        queue = self.app.follow_up_queue(100)
        rows = {i["ticket"]["ticket_id"]: i for i in queue["items"]}
        self.assertEqual(rows["T-no-replies"]["last_answered_at"], 20)
        self.assertEqual(rows["T-untimed"]["last_answered_at"], 30)

    def test_follow_up_queue_excludes_closed_null_empty_and_unresponded(self):
        self._responded("T-closed", 5)
        self.app.assign("T-closed", "Eve")
        self.app.close("T-closed", "Done")
        self.app.open_ticket("T-empty", "Bob", "B", opened_at=5)  # first_response forced to {}
        data = self.app._read()
        data["tickets"]["T-empty"]["first_response"] = {}
        self.app._write(data)
        self.app.open_ticket("T-null", "Bob", "B", opened_at=5)  # first_response stays null
        self.app.open_ticket("T-pending", "Cara", "C", opened_at=5)
        queue = self.app.follow_up_queue(10)
        self.assertEqual(queue["items"], [])

    def test_follow_up_queue_sorts_by_time_then_priority_then_id(self):
        self._responded("T-low", 10, "low")
        self._responded("T-high", 10, "high")
        self._responded("T-urgent", 10, "urgent")
        self._responded("T-normal-b", 10)
        self._responded("T-normal-a", 10)
        self._responded("T-early", 5)
        queue = self.app.follow_up_queue(100)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]],
                         ["T-early", "T-urgent", "T-high", "T-normal-a", "T-normal-b", "T-low"])

    def test_follow_up_queue_rejects_future_response_or_reply(self):
        self._responded("T-future-reply", 10)
        self.app.reply("T-future-reply", "later", 200)
        self._responded("T-ok", 10)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.follow_up_queue(130)
        self.assertEqual(before, self.app.path.read_bytes())
        # excluded (closed) tickets with future response/reply records do not participate in the check
        self.app.assign("T-future-reply", "Eve")
        self.app.close("T-future-reply", "Done")
        queue = self.app.follow_up_queue(130)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-ok"])

    def test_follow_up_queue_reopens_keep_history_and_refresh_on_reply(self):
        self._responded("T", 10)
        self.app.reply("T", "again", 70)
        self.app.assign("T", "Eve")
        self.app.close("T", "Done")
        self.app.reopen_ticket("T", "back")
        queue = self.app.follow_up_queue(130)
        self.assertEqual(queue["items"][0]["last_answered_at"], 70)
        self.app.reply("T", "new", 120)
        self.assertEqual(self.app.follow_up_queue(130)["items"][0]["last_answered_at"], 120)

    def test_follow_up_queue_bad_arguments_and_signature(self):
        for as_of in (True, 1.5, "40", None, -1):
            with self.assertRaises(ValueError, msg=as_of):
                self.app.follow_up_queue(as_of)
        for target in (0, -5, True, 60.0, "60", None):
            with self.assertRaises(ValueError, msg=target):
                self.app.follow_up_queue(40, target)
        with self.assertRaises(TypeError):
            self.app.follow_up_queue()
        with self.assertRaises(TypeError):
            self.app.follow_up_queue(40, bogus=1)

    def test_follow_up_queue_empty_directory_creates_no_file(self):
        queue = self.app.follow_up_queue(0)
        self.assertEqual(queue, {"as_of": 0, "items": []})
        self.assertFalse(self.app.path.exists())

    def test_cli_follow_up_queue(self):
        self._responded("T-1", 10)
        self.app.reply("T-1", "again", 70)
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"as_of": 130}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "follow-up-queue", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["as_of"], 130)
        self.assertEqual([(i["ticket"]["ticket_id"], i["waiting_minutes"], i["overdue"]) for i in value["items"]],
                         [("T-1", 60, False)])
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"as_of": -1}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "follow-up-queue", str(bad)], text=True, capture_output=True)
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

    def test_update_knowledge_persists_and_keeps_identifiers(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        entry = self.app.update_knowledge(" KB-1 ", "  New Title\nLine 2! ", "New body.  Punctuation: 标点。")
        self.assertEqual(entry, {"article_id": "KB-1", "source_ticket_id": "T",
                                 "title": "New Title\nLine 2!", "content": "New body.  Punctuation: 标点。"})
        reloaded = SupportDesk(self.root)
        self.assertEqual(reloaded.search_knowledge(), [entry])
        # resubmitting identical normalized content succeeds without adding records
        again = reloaded.update_knowledge("KB-1", "New Title\nLine 2!", "New body.  Punctuation: 标点。")
        self.assertEqual(again, entry)
        self.assertEqual(len(reloaded.search_knowledge()), 1)
        source = reloaded.get("T")
        self.assertEqual((source["subject"], source["resolution"]), ("Download", "Sent link"))

    def test_update_knowledge_search_uses_current_and_snapshots_keep_history(self):
        self._closed_ticket("T-src", subject="下载指引", resolution="旧办法")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-jia", "甲", "需要下载", opened_at=5)
        self.app.respond_with_knowledge("T-jia", "KB-1", 6)
        self.app.update_knowledge("KB-1", "下载指引", "新办法")
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("旧办法")], [])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("新办法")], ["KB-1"])
        self.assertEqual(self.app.get("T-jia")["first_response"]["message"], "旧办法")
        self.assertEqual(self.app.get("T-jia")["first_response"]["knowledge"]["content"], "旧办法")
        self.app.open_ticket("T-yi", "乙", "需要下载", opened_at=7)
        self.app.respond_with_knowledge("T-yi", "KB-1", 8)
        self.assertEqual(self.app.get("T-yi")["first_response"]["message"], "新办法")
        # repeated revisions follow the same rule
        self.app.update_knowledge("KB-1", "下载指引", "最新下载流程")
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("新办法")], [])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("最新下载流程")], ["KB-1"])
        self.assertEqual(self.app.get("T-jia")["first_response"]["message"], "旧办法")
        self.assertEqual(self.app.get("T-yi")["first_response"]["message"], "新办法")
        self.assertEqual(self.app.get("T-jia")["first_response"]["responded_at"], 6)

    def test_update_knowledge_rejects_bad_input_without_writing(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        before = self.app.path.read_bytes()
        for article_id, title, content in [
            (None, "New", "Body"), (1, "New", "Body"), (" ", "New", "Body"),
            ("KB-1", None, "Body"), ("KB-1", 1, "Body"), ("KB-1", " ", "Body"),
            ("KB-1", "New", None), ("KB-1", "New", 1), ("KB-1", "New", " "),
            ("kb-1", "New", "Body"), ("KB-X", "New", "Body"),
        ]:
            with self.assertRaises(ValueError, msg=(article_id, title, content)):
                self.app.update_knowledge(article_id, title, content)
        with self.assertRaises(TypeError):
            self.app.update_knowledge("KB-1", "New")
        with self.assertRaises(TypeError):
            self.app.update_knowledge("KB-1", "New", "Body", "extra")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.search_knowledge()[0]["content"], "Sent link")

    def test_update_knowledge_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.update_knowledge("KB-1", "New", "Body")
        self.assertFalse(fresh.exists())

    def test_cli_update_knowledge(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        payload = self.root / "update.json"
        payload.write_text(json.dumps({"article_id": "KB-1", "title": "新标题", "content": "新正文"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-update", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"article_id": "KB-1", "source_ticket_id": "T", "title": "新标题", "content": "新正文"})
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"article_id": "KB-1"}), encoding="utf-8")
        missing_arg = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-update", str(bad)], text=True, capture_output=True)
        self.assertEqual(missing_arg.returncode, 2)
        self.assertIn("error", json.loads(missing_arg.stderr))
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([
            {"article_id": "KB-1", "title": "第二批", "content": "正文二"},
            {"article_id": "KB-X", "title": "缺失", "content": "正文"},
            {"article_id": "KB-1", "title": "不应执行", "content": "正文三"},
        ]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-update", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        current = SupportDesk(self.root).search_knowledge()[0]
        self.assertEqual((current["title"], current["content"]), ("第二批", "正文二"))

    def _knowledge_response_setup(self):
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        entry = self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-tgt", "Alice", "Need download", opened_at=5)
        return entry

    def test_respond_with_knowledge_persists_snapshot(self):
        entry = self._knowledge_response_setup()
        self.app.assign("T-tgt", "Bob")
        self.app.note("T-tgt", "Checking")
        self.app.set_priority("T-tgt", "high")
        ticket = self.app.respond_with_knowledge(" T-tgt ", "KB-1", 7)
        self.assertEqual(ticket["first_response"],
                         {"message": "Sent link", "responded_at": 7, "knowledge": dict(entry)})
        self.assertEqual((ticket["status"], ticket["assignee"], ticket["notes"],
                          ticket["resolution"], ticket["priority"]),
                         ("open", "Bob", ["Checking"], None, "high"))
        reloaded = SupportDesk(self.root).get("T-tgt")
        self.assertEqual(reloaded["first_response"],
                         {"message": "Sent link", "responded_at": 7, "knowledge": dict(entry)})
        # knowledge entry and its source ticket stay untouched
        self.assertEqual(SupportDesk(self.root).search_knowledge(), [entry])
        source = self.app.get("T-src")
        self.assertEqual((source["status"], source["resolution"]), ("closed", "Sent link"))

    def test_respond_with_knowledge_allows_equal_minute_and_unassigned_target(self):
        self._knowledge_response_setup()
        ticket = self.app.respond_with_knowledge("T-tgt", "KB-1", 5)
        self.assertIsNone(ticket["assignee"])
        self.assertEqual(ticket["first_response"]["responded_at"], 5)

    def test_respond_with_knowledge_shares_uniqueness_with_respond(self):
        self._knowledge_response_setup()
        self.app.respond("T-tgt", "Manual", 6)
        with self.assertRaises(ValueError):
            self.app.respond_with_knowledge("T-tgt", "KB-1", 7)
        self.app.open_ticket("T-other", "Bob", "Other", opened_at=5)
        self.app.respond_with_knowledge("T-other", "KB-1", 6)
        with self.assertRaises(ValueError):
            self.app.respond("T-other", "Manual", 7)

    def test_respond_with_knowledge_counts_in_stats_queue_report(self):
        self._knowledge_response_setup()
        self.app.respond_with_knowledge("T-tgt", "KB-1", 6)
        stats = self.app.response_stats()
        self.assertEqual((stats["timed"], stats["responded"], stats["pending"]), (1, 1, 0))
        self.assertEqual((stats["average_minutes"], stats["max_minutes"]), (1, 1))
        queue = self.app.response_queue(10)
        self.assertEqual(queue["items"], [])
        report = self.app.response_target_report(10)
        normal = next(group for group in report["groups"] if group["priority"] == "normal")
        self.assertEqual((normal["responded"], normal["on_time"], normal["late"]), (1, 1, 0))
        priorities = [(item["ticket"]["ticket_id"], item["priority"]) for item in self.app.priority_queue()]
        self.assertEqual(priorities, [("T-tgt", "normal")])

    def test_respond_with_knowledge_rejects_bad_input_without_writing(self):
        self._knowledge_response_setup()
        self.app.open_ticket("T-untimed", "Bob", "No clock")
        self._closed_ticket("T-closed", subject="Other", resolution="Done")
        before = self.app.path.read_bytes()
        for ticket_id, article_id, responded_at in [
            (None, "KB-1", 6), (1, "KB-1", 6), (" ", "KB-1", 6),
            ("T-tgt", None, 6), ("T-tgt", 1, 6), ("T-tgt", " ", 6),
            ("T-tgt", "KB-1", True), ("T-tgt", "KB-1", 1.5),
            ("T-tgt", "KB-1", "6"), ("T-tgt", "KB-1", None),
            ("T-tgt", "KB-1", -1), ("T-tgt", "KB-1", 4),
            ("missing", "KB-1", 6), ("T-closed", "KB-1", 6),
            ("T-untimed", "KB-1", 6), ("T-tgt", "kb-1", 6),
            ("T-tgt", "KB-X", 6),
        ]:
            with self.assertRaises(ValueError, msg=(ticket_id, article_id, responded_at)):
                self.app.respond_with_knowledge(ticket_id, article_id, responded_at)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertIsNone(self.app.get("T-tgt")["first_response"])

    def test_respond_with_knowledge_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.respond_with_knowledge("T", "KB-1", 3)
        self.assertFalse(fresh.exists())

    def test_cli_respond_with_knowledge(self):
        self._knowledge_response_setup()
        self.app.open_ticket("T-other", "Bob", "Other", opened_at=5)
        payload = self.root / "respond.json"
        payload.write_text(json.dumps({"ticket_id": "T-tgt", "article_id": "KB-1", "responded_at": 6}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-respond", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["first_response"]["message"], "Sent link")
        self.assertEqual(value["first_response"]["knowledge"]["article_id"], "KB-1")
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([
            {"ticket_id": "T-other", "article_id": "KB-1", "responded_at": 6},
            {"ticket_id": "T-tgt", "article_id": "KB-1", "responded_at": 7},
        ]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-respond", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(SupportDesk(self.root).get("T-other")["first_response"]["responded_at"], 6)

    def _response_report_scenario(self):
        # normal group: two responded tickets (30 and 31 minutes; the 31-minute one is closed)
        self.app.open_ticket("T-responded-30", "Alice", "A", opened_at=70)
        self.app.respond("T-responded-30", "On it", 100)
        self.app.open_ticket("T-responded-31", "Bob", "B", opened_at=69)
        self.app.assign("T-responded-31", "Eve")
        self.app.respond("T-responded-31", "Done", 100)
        self.app.close("T-responded-31", "Resolved")
        # two pending tickets waiting 30 and 31 minutes
        self.app.open_ticket("T-pending-30", "Cara", "C", opened_at=70)
        self.app.open_ticket("T-pending-31", "Dan", "D", opened_at=69)
        # one closed ticket without opened_at
        self.app.open_ticket("T-untimed-closed", "Fay", "F")
        self.app.assign("T-untimed-closed", "Gus")
        self.app.close("T-untimed-closed", "Done")
        # one closed ticket with opened_at but no first response
        self.app.open_ticket("T-closed-unanswered", "Han", "G", opened_at=70)
        self.app.assign("T-closed-unanswered", "Ivy")
        self.app.close("T-closed-unanswered", "Closed anyway")

    def _report_groups(self, report):
        return {group["priority"]: group for group in report["groups"]}

    def test_response_target_report_normal_counts_group_order_and_recreation(self):
        self._response_report_scenario()
        report = self.app.response_target_report(100)
        self.assertEqual(report["as_of"], 100)
        self.assertEqual([g["priority"] for g in report["groups"]],
                         ["urgent", "high", "normal", "low"])
        normal = self._report_groups(report)["normal"]
        self.assertEqual(normal["target_minutes"], 30)
        self.assertEqual((normal["responded"], normal["on_time"], normal["late"],
                          normal["pending"], normal["overdue"], normal["untimed"],
                          normal["closed_without_response"]), (2, 1, 1, 2, 1, 1, 1))
        self.assertEqual(normal["on_time_rate"], 0.5)
        for name in ("urgent", "high", "low"):
            group = self._report_groups(report)[name]
            self.assertEqual((group["responded"], group["on_time"], group["late"],
                              group["pending"], group["overdue"], group["untimed"],
                              group["closed_without_response"]), (0, 0, 0, 0, 0, 0, 0))
            self.assertIsNone(group["on_time_rate"])
        # recreating SupportDesk in the same directory gives the same report
        recreated = SupportDesk(self.root).response_target_report(100)
        self.assertEqual(recreated, report)
        # read-only: no backfilled priority or clock fields, unchanged bytes
        self.assertNotIn("priority", self.app.get("T-pending-30"))
        self.assertNotIn("opened_at", self.app.get("T-untimed-closed"))
        before = self.app.path.read_bytes()
        self.app.response_target_report(100)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_response_target_report_override_normal_target_to_31(self):
        self._response_report_scenario()
        report = self.app.response_target_report(100, {"normal": 31})
        groups = self._report_groups(report)
        # uncovered priorities keep their default targets
        self.assertEqual([groups[name]["target_minutes"] for name in ("urgent", "high", "normal", "low")],
                         [5, 15, 31, 60])
        normal = groups["normal"]
        self.assertEqual((normal["responded"], normal["on_time"], normal["late"],
                          normal["pending"], normal["overdue"], normal["untimed"],
                          normal["closed_without_response"]), (2, 2, 0, 2, 0, 1, 1))
        self.assertEqual(normal["on_time_rate"], 1.0)

    def test_response_target_report_omitted_null_and_empty_targets_use_defaults(self):
        self._response_report_scenario()
        omitted = self.app.response_target_report(100)
        self.assertEqual(self.app.response_target_report(100, None), omitted)
        self.assertEqual(self.app.response_target_report(100, {}), omitted)
        self.assertEqual([g["target_minutes"] for g in omitted["groups"]], [5, 15, 30, 60])

    def test_response_target_report_old_tickets_default_to_normal_without_backfill(self):
        self.app.open_ticket("T-old", "Alice", "Old", opened_at=70)
        self.app.open_ticket("T-urgent", "Bob", "U", opened_at=0)
        self.app.set_priority("T-urgent", "urgent")
        self.app.respond("T-urgent", "Seen", 10)
        groups = self._report_groups(self.app.response_target_report(100))
        self.assertEqual((groups["urgent"]["responded"], groups["urgent"]["on_time"],
                          groups["urgent"]["late"], groups["urgent"]["on_time_rate"]),
                         (1, 0, 1, 0.0))
        # 30-minute wait against the 30-minute normal target is not overdue
        self.assertEqual((groups["normal"]["pending"], groups["normal"]["overdue"]), (1, 0))
        self.assertNotIn("priority", self.app.get("T-old"))

    def test_response_target_report_empty_data_is_all_zeros_and_creates_nothing(self):
        report = self.app.response_target_report(0)
        self.assertEqual(report["as_of"], 0)
        self.assertEqual([g["priority"] for g in report["groups"]],
                         ["urgent", "high", "normal", "low"])
        self.assertEqual([g["target_minutes"] for g in report["groups"]], [5, 15, 30, 60])
        for group in report["groups"]:
            self.assertEqual((group["responded"], group["on_time"], group["late"],
                              group["pending"], group["overdue"], group["untimed"],
                              group["closed_without_response"]), (0, 0, 0, 0, 0, 0, 0))
            self.assertIsNone(group["on_time_rate"])
        self.assertFalse(self.app.path.exists())
        # a query in a directory that does not exist creates neither directory nor file
        missing = self.root / "missing"
        fresh_report = SupportDesk(missing).response_target_report(0, {})
        self.assertEqual(fresh_report, report)
        self.assertFalse(missing.exists())

    def test_response_target_report_allows_minutes_equal_to_as_of(self):
        self.app.open_ticket("T-pending", "Alice", "A", opened_at=100)
        self.app.open_ticket("T-closed", "Bob", "B", opened_at=90)
        self.app.assign("T-closed", "Eve")
        self.app.respond("T-closed", "Seen", 100)
        self.app.close("T-closed", "Done")
        normal = self._report_groups(self.app.response_target_report(100))["normal"]
        self.assertEqual((normal["responded"], normal["pending"],
                          normal["closed_without_response"], normal["on_time"]), (1, 1, 0, 1))

    def test_response_target_report_rejects_future_opened_at_including_closed(self):
        self.app.open_ticket("T-future-open", "Alice", "A", opened_at=101)
        self.app.open_ticket("T-future-closed", "Bob", "B", opened_at=101)
        self.app.assign("T-future-closed", "Eve")
        self.app.close("T-future-closed", "Done")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(100)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_response_target_report_rejects_future_response_of_open_ticket(self):
        self.app.open_ticket("T", "Alice", "A", opened_at=90)
        self.app.respond("T", "Seen", 101)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(100)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_response_target_report_rejects_future_response_of_closed_ticket(self):
        self.app.open_ticket("T", "Alice", "A", opened_at=90)
        self.app.assign("T", "Eve")
        self.app.respond("T", "Seen", 101)
        self.app.close("T", "Done")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(100)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_response_target_report_rejects_bad_arguments_without_writing(self):
        self._response_report_scenario()
        before = self.app.path.read_bytes()
        for as_of in (-1, True, 1.5, "100", None):
            with self.assertRaises(ValueError, msg=as_of):
                self.app.response_target_report(as_of)
        bad_targets = [
            [], "x", 5, True,                # non-object targets other than null
            {"critical": 10},                # unknown priority key
            {"normal": 0}, {"normal": -1},   # non-positive integers
            {"normal": True},                # booleans are rejected even though int-like
            {"normal": 30.0}, {"normal": "30"}, {"normal": None},
        ]
        for targets in bad_targets:
            with self.assertRaises(ValueError, msg=targets):
                self.app.response_target_report(100, targets)
        with self.assertRaises(TypeError):
            self.app.response_target_report()
        with self.assertRaises(TypeError):
            self.app.response_target_report(100, {}, extra=1)
        self.assertEqual(before, self.app.path.read_bytes())
        # successful and failed queries leave every ticket structurally untouched
        self.app.response_target_report(100)
        with self.assertRaises(ValueError):
            self.app.response_target_report(100, {"normal": 0})
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_response_target_report(self):
        self._response_report_scenario()
        expected_counts = (2, 1, 1, 2, 1, 1, 1)
        for body in ({"as_of": 100}, {"as_of": 100, "targets": None},
                     {"as_of": 100, "targets": {}}):
            payload = self.root / "report.json"
            payload.write_text(json.dumps(body), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-target-report", str(payload)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            normal = self._report_groups(json.loads(result.stdout))["normal"]
            self.assertEqual((normal["responded"], normal["on_time"], normal["late"],
                              normal["pending"], normal["overdue"], normal["untimed"],
                              normal["closed_without_response"]), expected_counts)
        # an array runs a batch of independent queries and returns an array of reports
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"as_of": 100},
                                     {"as_of": 100, "targets": {"normal": 31}}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-target-report", str(batch)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)
        self.assertEqual(len(reports), 2)
        self.assertEqual(self._report_groups(reports[0])["normal"]["late"], 1)
        self.assertEqual((self._report_groups(reports[1])["normal"]["on_time"],
                          self._report_groups(reports[1])["normal"]["overdue"]), (2, 0))

    def test_cli_response_target_report_failure_stops_batch_without_partial_output(self):
        self._response_report_scenario()
        before = self.app.path.read_bytes()
        batch = self.root / "bad.json"
        batch.write_text(json.dumps([{"as_of": 100}, {"as_of": -1}, {"as_of": 100}]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-target-report", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        # single-object failures and signature problems follow the same contract
        for body in ({"as_of": 100, "targets": {"critical": 3}},
                     {"as_of": 100, "targets": {"normal": 1.5}},
                     {}, {"as_of": 100, "extra": 1}):
            payload = self.root / "one.json"
            payload.write_text(json.dumps(body), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "response-target-report", str(payload)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, body)
            self.assertIn("error", json.loads(result.stderr))
            self.assertEqual(result.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_response_target_report_empty_directory_creates_nothing(self):
        missing = self.root / "missing"
        payload = self.root / "report.json"
        payload.write_text(json.dumps({"as_of": 0}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(missing), "response-target-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([g["priority"] for g in report["groups"]],
                         ["urgent", "high", "normal", "low"])
        self.assertEqual(sum(g["responded"] + g["pending"] for g in report["groups"]), 0)
        self.assertFalse(missing.exists())

    def test_set_knowledge_enabled_persists_and_repeat_is_quiet(self):
        self._closed_ticket()
        entry = self.app.publish_knowledge("KB-1", "T")
        result = self.app.set_knowledge_enabled(" KB-1 ", False)
        self.assertEqual(result, {"article": entry, "enabled": False})
        again = self.app.set_knowledge_enabled("KB-1", False)
        self.assertEqual(again, {"article": entry, "enabled": False})
        reloaded = SupportDesk(self.root)
        self.assertEqual(reloaded.search_knowledge(), [])
        restored = reloaded.set_knowledge_enabled("KB-1", True)
        self.assertEqual(restored, {"article": entry, "enabled": True})
        self.assertEqual(SupportDesk(self.root).search_knowledge(), [entry])

    def test_set_knowledge_enabled_rejects_bad_input_without_writing(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        before = self.app.path.read_bytes()
        for article_id, enabled in [(None, True), (1, True), (" ", True), ("kb-1", True),
                                    ("KB-X", True), ("KB-1", None), ("KB-1", 1), ("KB-1", 0),
                                    ("KB-1", 1.0), ("KB-1", "true"), ("KB-1", []), ("KB-1", {})]:
            with self.assertRaises(ValueError, msg=(article_id, enabled)):
                self.app.set_knowledge_enabled(article_id, enabled)
        with self.assertRaises(TypeError):
            self.app.set_knowledge_enabled("KB-1")
        with self.assertRaises(TypeError):
            self.app.set_knowledge_enabled("KB-1", True, "extra")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.search_knowledge()[0]["content"], "Sent link")

    def test_set_knowledge_enabled_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.set_knowledge_enabled("KB-1", False)
        self.assertFalse(fresh.exists())

    def test_disabled_article_hidden_from_search_and_respond(self):
        self._knowledge_response_setup()
        self._closed_ticket("T-src-2", subject="Billing", resolution="Refunded")
        self.app.publish_knowledge("KB-2", "T-src-2")
        self.app.set_knowledge_enabled("KB-1", False)
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge()], ["KB-2"])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge(None)], ["KB-2"])
        self.assertEqual(self.app.search_knowledge("download"), [])
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge("refunded")], ["KB-2"])
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.respond_with_knowledge("T-tgt", "KB-1", 6)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertIsNone(self.app.get("T-tgt")["first_response"])
        self.app.set_knowledge_enabled("KB-1", True)
        ticket = self.app.respond_with_knowledge("T-tgt", "KB-1", 6)
        self.assertEqual(ticket["first_response"]["message"], "Sent link")
        self.assertEqual([e["article_id"] for e in self.app.search_knowledge()], ["KB-1", "KB-2"])

    def test_disable_keeps_fields_occupancy_snapshots_and_allows_update(self):
        self._knowledge_response_setup()
        self.app.respond_with_knowledge("T-tgt", "KB-1", 6)
        self.app.set_knowledge_enabled("KB-1", False)
        # the four fields, the identifier and the source ticket's publish occupancy stay
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB-1", "T-src")
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB-2", "T-src")
        # a disabled article can still be revised without being re-enabled
        entry = self.app.update_knowledge("KB-1", "Download", "New link")
        self.assertEqual(entry, {"article_id": "KB-1", "source_ticket_id": "T-src",
                                 "title": "Download", "content": "New link"})
        self.assertEqual(self.app.search_knowledge(), [])
        # the saved first response keeps its original message and snapshot
        saved = self.app.get("T-tgt")["first_response"]
        self.assertEqual(saved["message"], "Sent link")
        self.assertEqual(saved["knowledge"],
                         {"article_id": "KB-1", "source_ticket_id": "T-src",
                          "title": "Download", "content": "Sent link"})
        # entries never expose the enabled state through existing read paths
        self.app.set_knowledge_enabled("KB-1", True)
        self.assertNotIn("enabled", self.app.search_knowledge()[0])
        self.app.open_ticket("T-new", "Bob", "Need download", opened_at=7)
        ticket = self.app.respond_with_knowledge("T-new", "KB-1", 8)
        self.assertEqual(sorted(ticket["first_response"]["knowledge"]),
                         ["article_id", "content", "source_ticket_id", "title"])

    def test_cli_knowledge_enabled_set(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        payload = self.root / "disable.json"
        payload.write_text(json.dumps({"article_id": "KB-1", "enabled": False}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-enabled-set", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["enabled"], False)
        self.assertEqual(value["article"],
                         {"article_id": "KB-1", "source_ticket_id": "T", "title": "Download", "content": "Sent link"})
        self.assertEqual(SupportDesk(self.root).search_knowledge(), [])
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"article_id": "KB-1", "enabled": True},
                                     {"article_id": "KB-1", "enabled": "yes"},
                                     {"article_id": "KB-1", "enabled": False}]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-enabled-set", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        self.assertEqual([e["article_id"] for e in SupportDesk(self.root).search_knowledge()], ["KB-1"])

    def test_reopen_persists_history_and_preserves_fields(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=5)
        self.app.assign("T", "Bob")
        self.app.note("T", "Checked")
        self.app.set_category("T", "network")
        self.app.set_priority("T", "high")
        self.app.respond("T", "On it", 6)
        self.app.close("T", "Sent link")
        ticket = self.app.reopen_ticket(" T ", "  Issue\nrecurred: 再次故障!  ")
        self.assertEqual((ticket["status"], ticket["resolution"]), ("open", None))
        self.assertEqual(ticket["reopen_history"],
                         [{"reason": "Issue\nrecurred: 再次故障!", "resolution": "Sent link"}])
        self.assertEqual((ticket["customer"], ticket["subject"], ticket["assignee"],
                          ticket["notes"], ticket["category"], ticket["priority"]),
                         ("Alice", "Download", "Bob", ["Checked"], "network", "high"))
        self.assertEqual(ticket["opened_at"], 5)
        self.assertEqual(ticket["first_response"], {"message": "On it", "responded_at": 6})
        reloaded = SupportDesk(self.root).get("T")
        self.assertEqual(reloaded["status"], "open")
        self.assertIsNone(reloaded["resolution"])
        self.assertEqual(reloaded["reopen_history"],
                         [{"reason": "Issue\nrecurred: 再次故障!", "resolution": "Sent link"}])
        # re-enters the work queue, but a prior first response cannot be registered again
        self.assertEqual([i["ticket"]["ticket_id"] for i in self.app.priority_queue()], ["T"])
        self.assertEqual(self.app.response_queue(10)["items"], [])
        with self.assertRaises(ValueError):
            self.app.respond("T", "again", 7)

    def test_reopen_close_cycle_appends_and_keeps_all_other_state(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=5)
        self.app.assign("T", "Bob")
        self.app.close("T", "First fix")
        self.app.reopen_ticket("T", "first regression")
        self.app.note("T", "Rechecked")
        self.app.close("T", "Second fix")
        ticket = self.app.reopen_ticket("T", "second regression")
        self.assertEqual([(h["reason"], h["resolution"]) for h in ticket["reopen_history"]],
                         [("first regression", "First fix"), ("second regression", "Second fix")])
        self.assertEqual((ticket["status"], ticket["resolution"], ticket["notes"], ticket["assignee"]),
                         ("open", None, ["Rechecked"], "Bob"))
        self.assertEqual(ticket["opened_at"], 5)
        # an untimed old ticket gains no clock fields on reopen and counts as untimed
        self.app.open_ticket("T-untimed", "Bob", "No clock")
        self.app.assign("T-untimed", "Eve")
        self.app.close("T-untimed", "Done")
        untimed = self.app.reopen_ticket("T-untimed", "regression")
        self.assertNotIn("opened_at", untimed)
        self.assertNotIn("first_response", untimed)
        queue = self.app.response_queue(20)
        self.assertEqual(queue["untimed"], 1)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T"])

    def test_reopen_rejects_bad_input_without_writing(self):
        self.app.open_ticket("T-open", "Alice", "Open")
        self._closed_ticket("T-closed")
        before = self.app.path.read_bytes()
        for ticket_id, reason in [("missing", "r"), ("T-open", "r"),
                                 ("T-closed", None), ("T-closed", 1), ("T-closed", " "),
                                 (None, "r"), (1, "r"), (" ", "r")]:
            with self.assertRaises(ValueError, msg=(ticket_id, reason)):
                self.app.reopen_ticket(ticket_id, reason)
        with self.assertRaises(TypeError):
            self.app.reopen_ticket("T-closed")
        with self.assertRaises(TypeError):
            self.app.reopen_ticket("T-closed", "r", "extra")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertNotIn("reopen_history", self.app.get("T-closed"))

    def test_reopen_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.reopen_ticket("T", "reason")
        self.assertFalse(fresh.exists())

    def test_reopen_keeps_knowledge_occupancy_and_snapshots(self):
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-tgt", "Alice", "Need download", opened_at=5)
        self.app.respond_with_knowledge("T-tgt", "KB-1", 6)
        self.app.reopen_ticket("T-src", "regression")
        # reopened is not closed, so it cannot publish; occupancy is not released either
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB-2", "T-src")
        self.app.close("T-src", "New resolution")
        with self.assertRaises(ValueError):
            self.app.publish_knowledge("KB-2", "T-src")
        # published entries and saved response snapshots stay untouched
        self.assertEqual(self.app.search_knowledge()[0]["content"], "Sent link")
        saved = self.app.get("T-tgt")["first_response"]
        self.assertEqual(saved["knowledge"]["content"], "Sent link")
        # a ticket that was never published can publish after close-reopen-close
        self.app.open_ticket("T-new", "Bob", "Other")
        self.app.assign("T-new", "Eve")
        self.app.close("T-new", "First")
        self.app.reopen_ticket("T-new", "again")
        self.app.close("T-new", "Second")
        entry = self.app.publish_knowledge("KB-3", "T-new")
        self.assertEqual(entry["content"], "Second")

    def test_cli_reopen(self):
        self._closed_ticket()
        payload = self.root / "reopen.json"
        payload.write_text(json.dumps({"ticket_id": " T ", "reason": "  Still broken\n多行。 "}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "reopen", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        ticket = json.loads(result.stdout)
        self.assertEqual((ticket["status"], ticket["resolution"]), ("open", None))
        self.assertEqual(ticket["reopen_history"], [{"reason": "Still broken\n多行。", "resolution": "Sent link"}])
        # reopening an open ticket fails with stderr JSON, exit 2 and empty stdout
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "reopen", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        # batch stops at the first error; the first success persists
        self.app.close("T", "Second fix")
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"ticket_id": "T", "reason": "again"},
                                     {"ticket_id": "T", "reason": "fails while open"},
                                     {"ticket_id": "T", "reason": "never runs"}]), encoding="utf-8")
        failed_batch = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "reopen", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed_batch.returncode, 2)
        self.assertIn("error", json.loads(failed_batch.stderr))
        self.assertEqual(failed_batch.stdout, "")
        history = SupportDesk(self.root).get("T")["reopen_history"]
        self.assertEqual([(h["reason"], h["resolution"]) for h in history],
                         [("Still broken\n多行。", "Sent link"), ("again", "Second fix")])

    def test_knowledge_history_starts_at_revision_one_after_publish(self):
        self._closed_ticket()
        entry = self.app.publish_knowledge(" KB-1 ", "T")
        self.assertEqual(self.app.knowledge_history("KB-1"),
                         [{"revision": 1, "title": "Download", "content": "Sent link"}])
        self.assertEqual(self.app.knowledge_history(" KB-1 "), self.app.knowledge_history("KB-1"))
        reloaded = SupportDesk(self.root)
        self.assertEqual(reloaded.knowledge_history("KB-1"),
                         [{"revision": 1, "title": entry["title"], "content": entry["content"]}])

    def test_update_knowledge_appends_revisions_but_identical_submit_adds_none(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        self.app.update_knowledge("KB-1", "Download", "Second")
        self.app.update_knowledge("KB-1", "New Title", "Third")
        self.assertEqual([(h["revision"], h["title"], h["content"]) for h in self.app.knowledge_history("KB-1")],
                         [(1, "Download", "Sent link"), (2, "Download", "Second"), (3, "New Title", "Third")])
        before = self.app.path.read_bytes()
        again = self.app.update_knowledge(" KB-1 ", "  New Title  ", "Third")
        self.assertEqual((again["title"], again["content"]), ("New Title", "Third"))
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-1")], [1, 2, 3])

    def test_revision_numbers_are_independent_per_article(self):
        self._closed_ticket("T-1", subject="A", resolution="a")
        self._closed_ticket("T-2", subject="B", resolution="b")
        self.app.publish_knowledge("KB-1", "T-1")
        self.app.publish_knowledge("KB-2", "T-2")
        self.app.update_knowledge("KB-1", "A", "a2")
        self.app.update_knowledge("KB-1", "A", "a3")
        self.app.update_knowledge("KB-2", "B", "b2")
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-1")], [1, 2, 3])
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-2")], [1, 2])

    def test_restore_appends_without_rewriting_old_revisions(self):
        self._closed_ticket("T", subject="标题", resolution="甲")
        self.app.publish_knowledge("KB-1", "T")
        self.app.update_knowledge("KB-1", "标题", "乙")
        entry = self.app.restore_knowledge("KB-1", 1)
        self.assertEqual(entry, {"article_id": "KB-1", "source_ticket_id": "T",
                                 "title": "标题", "content": "甲"})
        self.assertEqual([(h["revision"], h["content"]) for h in self.app.knowledge_history("KB-1")],
                         [(1, "甲"), (2, "乙"), (3, "甲")])
        # restoring the current content succeeds without adding a revision
        before = self.app.path.read_bytes()
        again = self.app.restore_knowledge("KB-1", 1)
        self.assertEqual(again["content"], "甲")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(len(self.app.knowledge_history("KB-1")), 3)
        # a later modification continues from the highest revision number
        self.app.update_knowledge("KB-1", "标题", "丁")
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-1")], [1, 2, 3, 4])
        # historical versions are never rewritten
        self.assertEqual([h["content"] for h in self.app.knowledge_history("KB-1")],
                         ["甲", "乙", "甲", "丁"])

    def test_restore_persists_and_search_and_new_responses_use_current(self):
        self._closed_ticket("T-src", subject="下载指引", resolution="旧办法")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-old", "甲", "需要下载", opened_at=5)
        self.app.respond_with_knowledge("T-old", "KB-1", 6)
        self.app.update_knowledge("KB-1", "下载指引", "新办法")
        self.app.restore_knowledge("KB-1", 1)
        reloaded = SupportDesk(self.root)
        self.assertEqual([h["content"] for h in reloaded.knowledge_history("KB-1")],
                         ["旧办法", "新办法", "旧办法"])
        self.assertEqual([e["article_id"] for e in reloaded.search_knowledge("旧办法")], ["KB-1"])
        self.assertEqual(reloaded.search_knowledge("新办法"), [])
        saved = reloaded.get("T-old")["first_response"]
        self.assertEqual((saved["message"], saved["knowledge"]["content"], saved["responded_at"]),
                         ("旧办法", "旧办法", 6))
        reloaded.open_ticket("T-new", "乙", "需要下载", opened_at=7)
        ticket = reloaded.respond_with_knowledge("T-new", "KB-1", 8)
        self.assertEqual(ticket["first_response"]["message"], "旧办法")

    def test_disabled_article_history_and_restore_keep_enabled_state(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        self.app.update_knowledge("KB-1", "Download", "Second")
        self.app.set_knowledge_enabled("KB-1", False)
        # a disabled article can be queried and restored
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-1")], [1, 2])
        entry = self.app.restore_knowledge("KB-1", 1)
        self.assertEqual((entry["title"], entry["content"]), ("Download", "Sent link"))
        self.assertEqual(self.app.search_knowledge(), [])
        # toggling enabled state adds no revision
        self.assertEqual([h["revision"] for h in SupportDesk(self.root).knowledge_history("KB-1")],
                         [1, 2, 3])
        SupportDesk(self.root).set_knowledge_enabled("KB-1", True)
        self.assertEqual([e["article_id"] for e in SupportDesk(self.root).search_knowledge()], ["KB-1"])

    def test_old_article_without_history_uses_current_as_revision_one_without_backfill(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({"knowledge": {
            "OLD": {"article_id": "OLD", "source_ticket_id": "T0",
                    "title": "旧标题", "content": "旧正文"}}}, ensure_ascii=False), encoding="utf-8")
        app = SupportDesk(legacy)
        self.assertEqual(app.knowledge_history("OLD"),
                         [{"revision": 1, "title": "旧标题", "content": "旧正文"}])
        # reads never backfill the missing history
        stored = json.loads((legacy / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("knowledge_history", stored)
        # restoring revision 1 (identical to current) succeeds without writing history
        before = (legacy / "data.json").read_bytes()
        self.assertEqual(app.restore_knowledge("OLD", 1)["content"], "旧正文")
        self.assertEqual((legacy / "data.json").read_bytes(), before)
        # an actual change seeds revision 1 from the old content and appends revision 2
        app.update_knowledge("OLD", "旧标题", "新正文")
        self.assertEqual(app.knowledge_history("OLD"),
                         [{"revision": 1, "title": "旧标题", "content": "旧正文"},
                          {"revision": 2, "title": "旧标题", "content": "新正文"}])
        # revisions before the stored history cannot be invented
        with self.assertRaises(ValueError):
            SupportDesk(legacy).restore_knowledge("OLD", 3)

    def test_knowledge_history_rejects_bad_input_without_writing(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        before = self.app.path.read_bytes()
        for article_id in [None, 1, True, " ", "kb-1", "KB-X"]:
            with self.assertRaises(ValueError, msg=article_id):
                self.app.knowledge_history(article_id)
        with self.assertRaises(TypeError):
            self.app.knowledge_history()
        with self.assertRaises(TypeError):
            self.app.knowledge_history("KB-1", "extra")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_restore_knowledge_rejects_bad_input_without_writing(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        self.app.update_knowledge("KB-1", "Download", "Second")
        before = self.app.path.read_bytes()
        for article_id in [None, 1, True, " ", "kb-1", "KB-X"]:
            with self.assertRaises(ValueError, msg=article_id):
                self.app.restore_knowledge(article_id, 1)
        for revision in [True, False, 1.0, 1.5, "1", None, 0, -1]:
            with self.assertRaises(ValueError, msg=revision):
                self.app.restore_knowledge("KB-1", revision)
        with self.assertRaises(ValueError):
            self.app.restore_knowledge("KB-1", 3)
        with self.assertRaises(TypeError):
            self.app.restore_knowledge("KB-1")
        with self.assertRaises(TypeError):
            self.app.restore_knowledge("KB-1", 1, "extra")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([h["revision"] for h in self.app.knowledge_history("KB-1")], [1, 2])
        self.assertEqual(self.app.search_knowledge()[0]["content"], "Second")

    def test_knowledge_history_and_restore_failure_create_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.knowledge_history("KB-1")
        with self.assertRaises(ValueError):
            app.restore_knowledge("KB-1", 1)
        self.assertFalse(fresh.exists())

    def test_cli_knowledge_history_and_restore(self):
        self._closed_ticket()
        self.app.publish_knowledge("KB-1", "T")
        self.app.update_knowledge("KB-1", "Download", "Second")
        query = self.root / "history.json"
        query.write_text(json.dumps({"article_id": " KB-1 "}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-history", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(h["revision"], h["content"]) for h in json.loads(result.stdout)],
                         [(1, "Sent link"), (2, "Second")])
        restore = self.root / "restore.json"
        restore.write_text(json.dumps({"article_id": "KB-1", "revision": 1}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-restore", str(restore)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"article_id": "KB-1", "source_ticket_id": "T",
                          "title": "Download", "content": "Sent link"})
        # bad revision reaches stderr as JSON with exit 2
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"article_id": "KB-1", "revision": "9"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-restore", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        # batch stops at the first error and keeps the earlier success
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"article_id": "KB-1", "revision": 2},
                                     {"article_id": "KB-X", "revision": 1},
                                     {"article_id": "KB-1", "revision": 1}]), encoding="utf-8")
        failed_batch = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), "knowledge-restore", str(batch)], text=True, capture_output=True)
        self.assertEqual(failed_batch.returncode, 2)
        self.assertIn("error", json.loads(failed_batch.stderr))
        self.assertEqual(failed_batch.stdout, "")
        self.assertEqual([h["content"] for h in SupportDesk(self.root).knowledge_history("KB-1")],
                         ["Sent link", "Second", "Sent link", "Second"])

    def _reply_scenario(self, ticket_id="T-tgt", knowledge_first=False):
        # A closed source ticket publishes KB-1; the target opens at minute 5 and receives
        # its first response at minute 6, either manual or from the knowledge article.
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket(ticket_id, "Alice", "Need download", opened_at=5)
        if knowledge_first:
            self.app.respond_with_knowledge(ticket_id, "KB-1", 6)
        else:
            self.app.respond(ticket_id, "Manual first", 6)
        return ticket_id

    def _kb1_snapshot(self, content="Sent link"):
        return {"article_id": "KB-1", "source_ticket_id": "T-src",
                "title": "Download", "content": content}

    def test_reply_alternates_kinds_after_manual_first_response_and_persists(self):
        ticket_id = self._reply_scenario()
        self.app.assign(ticket_id, "Bob")
        self.app.note(ticket_id, "Checked")
        self.app.set_category(ticket_id, "network")
        self.app.set_priority(ticket_id, "high")
        ticket = self.app.reply(" T-tgt ", "  Plain follow-up\nline two! ", 7)
        ticket = self.app.reply_with_knowledge(ticket_id, "KB-1", 8)
        ticket = self.app.reply(ticket_id, "Second plain", 8)
        ticket = self.app.reply_with_knowledge(ticket_id, " KB-1 ", 10)
        self.assertEqual(ticket["replies"], [
            {"message": "Plain follow-up\nline two!", "replied_at": 7},
            {"message": "Sent link", "replied_at": 8, "knowledge": self._kb1_snapshot()},
            {"message": "Second plain", "replied_at": 8},
            {"message": "Sent link", "replied_at": 10, "knowledge": self._kb1_snapshot()},
        ])
        self.assertEqual(set(ticket["replies"][0]), {"message", "replied_at"})
        self.assertEqual(set(ticket["replies"][1]), {"message", "replied_at", "knowledge"})
        # the successful call returns the complete stored ticket
        self.assertEqual(ticket, self.app.get(ticket_id))
        self.assertEqual((ticket["customer"], ticket["subject"], ticket["status"],
                          ticket["assignee"], ticket["notes"], ticket["category"],
                          ticket["priority"], ticket["resolution"], ticket["opened_at"]),
                         ("Alice", "Need download", "open", "Bob", ["Checked"],
                          "network", "high", None, 5))
        self.assertEqual(ticket["first_response"], {"message": "Manual first", "responded_at": 6})
        # recreating SupportDesk reads identical replies and surrounding fields
        self.assertEqual(SupportDesk(self.root).get(ticket_id), ticket)

    def test_reply_alternates_kinds_after_knowledge_first_response(self):
        ticket_id = self._reply_scenario(knowledge_first=True)
        ticket = self.app.reply_with_knowledge(ticket_id, "KB-1", 7)
        ticket = self.app.reply(ticket_id, "Manual follow-up", 8)
        ticket = self.app.reply_with_knowledge(ticket_id, "KB-1", 8)
        self.assertEqual(ticket["replies"], [
            {"message": "Sent link", "replied_at": 7, "knowledge": self._kb1_snapshot()},
            {"message": "Manual follow-up", "replied_at": 8},
            {"message": "Sent link", "replied_at": 8, "knowledge": self._kb1_snapshot()},
        ])
        self.assertEqual(ticket["first_response"],
                         {"message": "Sent link", "responded_at": 6, "knowledge": self._kb1_snapshot()})
        self.assertEqual(SupportDesk(self.root).get(ticket_id), ticket)

    def test_reply_strips_ends_keeps_internal_text_and_matches_ids_case_sensitively(self):
        ticket_id = self._reply_scenario()
        ticket = self.app.reply("  T-tgt  ", "  答复：第一步；\n  第二步 (done)!  ", 7)
        self.assertEqual(ticket["replies"][-1]["message"], "答复：第一步；\n  第二步 (done)!")
        ticket = self.app.reply_with_knowledge(ticket_id, "  KB-1  ", 8)
        self.assertEqual(ticket["replies"][-1]["knowledge"]["article_id"], "KB-1")
        before = self.app.path.read_bytes()
        for bad_id in ("t-tgt", "T-TGT", " t-tgt "):
            with self.assertRaises(ValueError, msg=bad_id):
                self.app.reply(bad_id, "x", 9)
        with self.assertRaises(ValueError):
            self.app.reply_with_knowledge(ticket_id, "kb-1", 9)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(SupportDesk(self.root).get(ticket_id)["replies"], ticket["replies"])

    def test_reply_allows_equal_minutes_and_appends_duplicate_submissions(self):
        ticket_id = self._reply_scenario()
        first = self.app.reply(ticket_id, "Same text", 6)
        self.assertEqual(first["replies"], [{"message": "Same text", "replied_at": 6}])
        self.app.reply(ticket_id, "Same text", 6)
        self.app.reply_with_knowledge(ticket_id, "KB-1", 6)
        replies = SupportDesk(self.root).get(ticket_id)["replies"]
        self.assertEqual([(r["message"], r["replied_at"]) for r in replies],
                         [("Same text", 6), ("Same text", 6), ("Sent link", 6)])

    def test_reply_rejects_times_earlier_than_first_response_or_last_reply(self):
        ticket_id = self._reply_scenario()
        self.app.reply(ticket_id, "At eight", 8)
        before = self.app.path.read_bytes()
        for replied_at in (5, 7):
            with self.assertRaises(ValueError, msg=replied_at):
                self.app.reply(ticket_id, "Too early", replied_at)
            with self.assertRaises(ValueError, msg=replied_at):
                self.app.reply_with_knowledge(ticket_id, "KB-1", replied_at)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.get(ticket_id)["replies"],
                         [{"message": "At eight", "replied_at": 8}])

    def test_knowledge_reply_uses_current_content_and_keeps_snapshot_through_changes(self):
        ticket_id = self._reply_scenario()
        self.app.reply_with_knowledge(ticket_id, "KB-1", 7)
        self.app.update_knowledge("KB-1", "Download", "Second")
        self.app.reply_with_knowledge(ticket_id, "KB-1", 8)
        self.app.restore_knowledge("KB-1", 1)
        self.app.set_knowledge_enabled("KB-1", False)
        saved = [
            {"message": "Sent link", "replied_at": 7, "knowledge": self._kb1_snapshot("Sent link")},
            {"message": "Second", "replied_at": 8, "knowledge": self._kb1_snapshot("Second")},
        ]
        self.assertEqual(self.app.get(ticket_id)["replies"], saved)
        self.assertEqual(self.app.get(ticket_id)["first_response"],
                         {"message": "Manual first", "responded_at": 6})
        # a disabled article cannot be referenced by a new reply
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reply_with_knowledge(ticket_id, "KB-1", 9)
        self.assertEqual(before, self.app.path.read_bytes())
        # after re-enabling, the restored current content is used; old replies stay as saved
        self.app.set_knowledge_enabled("KB-1", True)
        ticket = self.app.reply_with_knowledge(ticket_id, "KB-1", 9)
        self.assertEqual(ticket["replies"][-1],
                         {"message": "Sent link", "replied_at": 9, "knowledge": self._kb1_snapshot("Sent link")})
        self.assertEqual(SupportDesk(self.root).get(ticket_id)["replies"][:2], saved)

    def test_reply_with_knowledge_rejects_disabled_but_accepts_legacy_enabled_article(self):
        # an old article without any knowledge_enabled record is still considered enabled
        legacy = self.root / "legacy"
        legacy.mkdir()
        doc = {
            "tickets": {"T": {
                "ticket_id": "T", "customer": "Alice", "subject": "Need download",
                "status": "open", "assignee": None, "notes": [], "resolution": None,
                "opened_at": 5, "first_response": {"message": "On it", "responded_at": 6},
            }},
            "knowledge": {"KB-old": {
                "article_id": "KB-old", "source_ticket_id": "T0",
                "title": "旧标题", "content": "旧正文",
            }},
        }
        (legacy / "data.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        legacy_app = SupportDesk(legacy)
        ticket = legacy_app.reply_with_knowledge("T", "KB-old", 8)
        self.assertEqual(ticket["replies"], [
            {"message": "旧正文", "replied_at": 8,
             "knowledge": {"article_id": "KB-old", "source_ticket_id": "T0",
                           "title": "旧标题", "content": "旧正文"}},
        ])
        # an explicitly disabled article cannot be newly referenced
        ticket_id = self._reply_scenario()
        self.app.set_knowledge_enabled("KB-1", False)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reply_with_knowledge(ticket_id, "KB-1", 7)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertNotIn("replies", self.app.get(ticket_id))

    def test_reply_blocked_when_closed_and_reopen_keeps_history_and_time_order(self):
        ticket_id = self._reply_scenario()
        self.app.assign(ticket_id, "Bob")
        self.app.reply(ticket_id, "Before close", 7)
        self.app.reply_with_knowledge(ticket_id, "KB-1", 8)
        self.app.close(ticket_id, "Resolved")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reply(ticket_id, "Late", 9)
        with self.assertRaises(ValueError):
            self.app.reply_with_knowledge(ticket_id, "KB-1", 9)
        self.assertEqual(before, self.app.path.read_bytes())
        self.app.reopen_ticket(ticket_id, "regression")
        ticket = self.app.get(ticket_id)
        self.assertEqual([(r["message"], r["replied_at"]) for r in ticket["replies"]],
                         [("Before close", 7), ("Sent link", 8)])
        # the original chronology still bounds new replies
        for replied_at in (5, 7):
            with self.assertRaises(ValueError, msg=replied_at):
                self.app.reply(ticket_id, "Too early", replied_at)
        ticket = self.app.reply(ticket_id, "After reopen", 9)
        self.assertEqual([r["replied_at"] for r in ticket["replies"]], [7, 8, 9])
        self.assertEqual((ticket["status"], ticket["resolution"], ticket["assignee"],
                          ticket["first_response"]["responded_at"]),
                         ("open", None, "Bob", 6))
        self.assertEqual(SupportDesk(self.root).get(ticket_id), ticket)

    def test_old_ticket_without_replies_is_not_backfilled_until_first_append(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        doc = {"tickets": {"T": {
            "ticket_id": "T", "customer": "Alice", "subject": "Download",
            "status": "open", "assignee": None, "notes": [], "resolution": None,
            "opened_at": 5, "first_response": {"message": "On it", "responded_at": 6},
        }}}
        path = legacy / "data.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        app = SupportDesk(legacy)
        self.assertNotIn("replies", app.get("T"))
        before = path.read_bytes()
        app.get("T")
        self.assertEqual(before, path.read_bytes())
        with self.assertRaises(ValueError):
            app.reply("T", "Too early", 3)
        self.assertNotIn("replies",
                         json.loads(path.read_text(encoding="utf-8"))["tickets"]["T"])
        ticket = app.reply(" T ", "  First follow-up ", 7)
        self.assertEqual(ticket["replies"], [{"message": "First follow-up", "replied_at": 7}])
        self.assertEqual(SupportDesk(legacy).get("T")["replies"],
                         [{"message": "First follow-up", "replied_at": 7}])

    def test_unassigned_ticket_can_still_reply(self):
        self.app.open_ticket("T", "Alice", "Download", opened_at=5)
        self.app.respond("T", "On it", 6)
        ticket = self.app.reply("T", "Thanks", 7)
        self.assertIsNone(ticket["assignee"])
        self.assertEqual(ticket["replies"], [{"message": "Thanks", "replied_at": 7}])

    def test_reply_rejects_bad_input_without_writing(self):
        ticket_id = self._reply_scenario()
        self.app.open_ticket("T-untimed", "Bob", "No clock")
        self.app.open_ticket("T-pending", "Cara", "Waiting", opened_at=5)
        self.app.open_ticket("T-closed", "Dan", "Done", opened_at=5)
        self.app.assign("T-closed", "Eve")
        self.app.respond("T-closed", "Seen", 6)
        self.app.close("T-closed", "Fixed")
        self.app.set_knowledge_enabled("KB-1", False)
        before = self.app.path.read_bytes()
        plain_bad = [
            (None, "m", 7), (1, "m", 7), (" ", "m", 7),
            (ticket_id, None, 7), (ticket_id, 1, 7), (ticket_id, " ", 7),
            (ticket_id, "m", -1), (ticket_id, "m", True), (ticket_id, "m", 1.5),
            (ticket_id, "m", "7"), (ticket_id, "m", None),
            ("missing", "m", 7), ("T-closed", "m", 7),
            ("T-untimed", "m", 7), ("T-pending", "m", 7),
        ]
        for ticket_id_value, message, replied_at in plain_bad:
            with self.assertRaises(ValueError, msg=(ticket_id_value, message, replied_at)):
                self.app.reply(ticket_id_value, message, replied_at)
        knowledge_bad = [
            (None, "KB-1", 7), (1, "KB-1", 7), (" ", "KB-1", 7),
            (ticket_id, None, 7), (ticket_id, 1, 7), (ticket_id, " ", 7),
            (ticket_id, "KB-1", -1), (ticket_id, "KB-1", True), (ticket_id, "KB-1", 1.5),
            (ticket_id, "KB-1", "7"), (ticket_id, "KB-1", None),
            ("missing", "KB-1", 7), ("T-closed", "KB-1", 7),
            ("T-untimed", "KB-1", 7), ("T-pending", "KB-1", 7),
            (ticket_id, "kb-1", 7), (ticket_id, "KB-X", 7), (ticket_id, "KB-1", 7),
        ]
        for ticket_id_value, article_id, replied_at in knowledge_bad:
            with self.assertRaises(ValueError, msg=(ticket_id_value, article_id, replied_at)):
                self.app.reply_with_knowledge(ticket_id_value, article_id, replied_at)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertNotIn("replies", self.app.get(ticket_id))
        self.assertNotIn("replies", self.app.get("T-pending"))

    def test_reply_rejects_missing_and_unknown_arguments(self):
        ticket_id = self._reply_scenario()
        with self.assertRaises(TypeError):
            self.app.reply(ticket_id, "m")
        with self.assertRaises(TypeError):
            self.app.reply(ticket_id)
        with self.assertRaises(TypeError):
            self.app.reply(ticket_id, "m", 7, "extra")
        with self.assertRaises(TypeError):
            self.app.reply_with_knowledge(ticket_id, "KB-1")
        with self.assertRaises(TypeError):
            self.app.reply_with_knowledge(ticket_id)
        with self.assertRaises(TypeError):
            self.app.reply_with_knowledge(ticket_id, "KB-1", 7, extra=1)

    def test_reply_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.reply("T", "m", 6)
        with self.assertRaises(ValueError):
            app.reply_with_knowledge("T", "KB-1", 6)
        self.assertFalse(fresh.exists())

    def test_cli_reply_and_knowledge_reply(self):
        ticket_id = self._reply_scenario()

        def run(action, body):
            payload = self.root / (action + ".json")
            payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                   str(self.root), action, str(payload)],
                                  text=True, capture_output=True)

        result = run("reply", {"ticket_id": " T-tgt ", "message": "  CLI reply\nline two. ",
                               "replied_at": 7})
        self.assertEqual(result.returncode, 0, result.stderr)
        ticket = json.loads(result.stdout)
        self.assertEqual(ticket["replies"][-1],
                         {"message": "CLI reply\nline two.", "replied_at": 7})
        result = run("knowledge-reply", {"ticket_id": ticket_id, "article_id": " KB-1 ",
                                         "replied_at": 8})
        self.assertEqual(result.returncode, 0, result.stderr)
        ticket = json.loads(result.stdout)
        self.assertEqual(ticket["replies"][-1]["message"], "Sent link")
        self.assertEqual(ticket["replies"][-1]["knowledge"], self._kb1_snapshot())
        # failures exit 2 with empty stdout and an error JSON on stderr
        failed = run("reply", {"ticket_id": ticket_id, "message": "Too early", "replied_at": 5})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        failed = run("knowledge-reply", {"ticket_id": ticket_id, "article_id": "KB-X",
                                         "replied_at": 9})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        failed = run("reply", {"ticket_id": ticket_id, "message": "missing time"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        # arrays stop at the first error: earlier successes persist, later rows never run
        batch = self.root / "reply-batch.json"
        batch.write_text(json.dumps([
            {"ticket_id": ticket_id, "message": "batch one", "replied_at": 9},
            {"ticket_id": "missing", "message": "fails here", "replied_at": 10},
            {"ticket_id": ticket_id, "message": "never runs", "replied_at": 11},
        ]), encoding="utf-8")
        failed_batch = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                       str(self.root), "reply", str(batch)],
                                      text=True, capture_output=True)
        self.assertEqual(failed_batch.returncode, 2)
        self.assertEqual(failed_batch.stdout, "")
        self.assertIn("error", json.loads(failed_batch.stderr))
        batch = self.root / "knowledge-batch.json"
        batch.write_text(json.dumps([
            {"ticket_id": ticket_id, "article_id": "KB-1", "replied_at": 9},
            {"ticket_id": ticket_id, "article_id": "KB-X", "replied_at": 10},
            {"ticket_id": ticket_id, "article_id": "KB-1", "replied_at": 11},
        ]), encoding="utf-8")
        failed_batch = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                       str(self.root), "knowledge-reply", str(batch)],
                                      text=True, capture_output=True)
        self.assertEqual(failed_batch.returncode, 2)
        self.assertEqual(failed_batch.stdout, "")
        self.assertIn("error", json.loads(failed_batch.stderr))
        replies = SupportDesk(self.root).get(ticket_id)["replies"]
        self.assertEqual([(r["message"], r["replied_at"]) for r in replies],
                         [("CLI reply\nline two.", 7), ("Sent link", 8),
                          ("batch one", 9), ("Sent link", 9)])
        # a rejected call against a missing data root creates neither directory nor file
        missing = self.root / "missing"
        payload = self.root / "missing-reply.json"
        payload.write_text(json.dumps({"ticket_id": "T", "message": "m", "replied_at": 1}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(missing), "reply", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertFalse(missing.exists())

    def _usage_scenario(self):
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self._closed_ticket("T-src2", subject="Password", resolution="Reset it")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.publish_knowledge("kb-2", "T-src2")
        # T-1: knowledge first response at 5, two knowledge replies at 8, manual reply at 9
        self.app.open_ticket("T-1", "Alice", "Need help", opened_at=5)
        self.app.respond_with_knowledge("T-1", "KB-1", 5)
        self.app.reply_with_knowledge("T-1", "KB-1", 8)
        self.app.reply_with_knowledge("T-1", "KB-1", 8)
        self.app.reply("T-1", "Anything else?", 9)
        # T-2: manual first response at 5, knowledge reply at 6
        self.app.open_ticket("T-2", "Bob", "Other", opened_at=4)
        self.app.respond("T-2", "Hi", 5)
        self.app.reply_with_knowledge("T-2", "KB-1", 6)

    def test_knowledge_usage_report_counts_range_and_recreation(self):
        self._usage_scenario()
        report = self.app.knowledge_usage_report(5, 9)
        self.assertEqual([row["article"]["article_id"] for row in report], ["KB-1", "kb-2"])
        first, second = report
        self.assertEqual(first["article"],
                         {"article_id": "KB-1", "source_ticket_id": "T-src",
                          "title": "Download", "content": "Sent link"})
        self.assertEqual(first["enabled"], True)
        self.assertEqual((first["first_responses"], first["replies"],
                          first["ticket_count"], first["last_used_at"]), (1, 3, 2, 8))
        self.assertEqual((second["first_responses"], second["replies"],
                          second["ticket_count"], second["last_used_at"]), (0, 0, 0, None))
        # [5, 8) counts the first response at 5 and the reply at 6, but not the replies at 8
        bounded = self.app.knowledge_usage_report(5, 8)
        self.assertEqual((bounded[0]["first_responses"], bounded[0]["replies"],
                          bounded[0]["ticket_count"], bounded[0]["last_used_at"]), (1, 1, 2, 6))
        # equal bounds count nothing; omitted until is unbounded
        empty = self.app.knowledge_usage_report(5, 5)
        self.assertEqual((empty[0]["first_responses"], empty[0]["replies"],
                          empty[0]["ticket_count"], empty[0]["last_used_at"]), (0, 0, 0, None))
        unbounded = self.app.knowledge_usage_report(6)
        self.assertEqual((unbounded[0]["first_responses"], unbounded[0]["replies"],
                          unbounded[0]["ticket_count"], unbounded[0]["last_used_at"]), (0, 3, 2, 8))
        self.assertEqual(self.app.knowledge_usage_report(), SupportDesk(self.root).knowledge_usage_report())

    def test_knowledge_usage_report_keeps_counts_through_changes(self):
        self._usage_scenario()
        self.app.update_knowledge("KB-1", "Download v2", "New link")
        self.app.restore_knowledge("KB-1", 1)
        self.app.set_knowledge_enabled("KB-1", False)
        self.app.assign("T-1", "Bob")
        self.app.close("T-1", "Done")
        self.app.reopen_ticket("T-1", "not fixed")
        report = SupportDesk(self.root).knowledge_usage_report(0)
        first = report[0]
        # article shows current content and current enabled state; stored references are unchanged
        self.assertEqual((first["article"]["title"], first["article"]["content"]),
                         ("Download", "Sent link"))
        self.assertEqual(first["enabled"], False)
        self.assertEqual((first["first_responses"], first["replies"],
                          first["ticket_count"], first["last_used_at"]), (1, 3, 2, 8))

    def test_knowledge_usage_report_legacy_tickets_and_empty_directory(self):
        # old tickets without first_response or replies are treated as empty records
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-legacy", "Alice", "Old")
        report = self.app.knowledge_usage_report(0)
        self.assertEqual(len(report), 1)
        self.assertEqual((report[0]["first_responses"], report[0]["replies"],
                          report[0]["ticket_count"], report[0]["last_used_at"]), (0, 0, 0, None))
        # no knowledge at all, or a missing directory, returns []
        empty = SupportDesk(self.root / "missing")
        self.assertEqual(empty.knowledge_usage_report(), [])
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(SupportDesk(self.root / "other").knowledge_usage_report(3, 4), [])
        self.assertFalse((self.root / "other").exists())

    def test_knowledge_usage_report_rejects_bad_arguments_without_writing(self):
        self._usage_scenario()
        before = self.app.path.read_bytes()
        for bad in (None, True, 1.5, "5", -1):
            with self.assertRaises(ValueError):
                self.app.knowledge_usage_report(bad)
        for bad in (True, 2.5, "9", -2):
            with self.assertRaises(ValueError):
                self.app.knowledge_usage_report(0, bad)
        with self.assertRaises(ValueError):
            self.app.knowledge_usage_report(9, 8)
        with self.assertRaises(TypeError):
            self.app.knowledge_usage_report(0, 9, 10)
        with self.assertRaises(TypeError):
            self.app.knowledge_usage_report(since=0, unknown=1)
        self.assertEqual(before, self.app.path.read_bytes())
        # a failed query against a missing root creates neither directory nor file
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            SupportDesk(missing).knowledge_usage_report(-1)
        self.assertFalse(missing.exists())

    def test_cli_knowledge_usage_report(self):
        self._usage_scenario()
        payload = self.root / "usage.json"
        payload.write_text(json.dumps({"since": 5, "until": 9}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "knowledge-usage-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([row["article"]["article_id"] for row in report], ["KB-1", "kb-2"])
        self.assertEqual((report[0]["first_responses"], report[0]["replies"],
                          report[0]["ticket_count"], report[0]["last_used_at"]), (1, 3, 2, 8))
        # defaults work without an input file
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "knowledge-usage-report"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["last_used_at"], 8)
        # failures exit 2 with an error object on stderr and nothing on stdout
        payload.write_text(json.dumps({"since": None}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "knowledge-usage-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        payload.write_text(json.dumps({"since": 0, "extra": 1}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "knowledge-usage-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        # an empty root prints [] and creates nothing
        missing = self.root / "missing"
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(missing),
                                 "knowledge-usage-report"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertFalse(missing.exists())

    def _search_field_scenario(self):
        # A handful of independent tickets; each unique keyword lives in exactly
        # one searchable place. Times are local simulated minutes.
        self.app.open_ticket("T-customer", "Custkw Anderson", "Subject one")
        self.app.open_ticket("T-subject", "Alice", "Subjkw request")
        self.app.open_ticket("T-note", "Bob", "Subject two")
        self.app.note("T-note", "Notekw recorded")
        self.app.open_ticket("T-resolution", "Carol", "Subject three")
        self.app.assign("T-resolution", "Eve")
        self.app.close("T-resolution", "Resolkw finished")
        self.app.open_ticket("T-response", "Dan", "Subject four", opened_at=5)
        self.app.respond("T-response", "Firstkw first reply body", 6)
        self.app.reply("T-response", "Replykw later reply body", 7)

    def _search_ids(self, query, **kwargs):
        return [ticket["ticket_id"] for ticket in self.app.search_tickets(query, **kwargs)["items"]]

    def test_search_tickets_matches_every_searchable_field_and_returns_full_tickets(self):
        self._search_field_scenario()
        self.assertEqual(self._search_ids("CUSTKW"), ["T-customer"])
        self.assertEqual(self._search_ids("subjkw"), ["T-subject"])
        self.assertEqual(self._search_ids("NOTEKW"), ["T-note"])
        # a closed ticket is searched, and the resolution participates
        self.assertEqual(self._search_ids("resolkw"), ["T-resolution"])
        self.assertEqual(self._search_ids("firstkw"), ["T-response"])
        self.assertEqual(self._search_ids("replykw"), ["T-response"])
        # terms may land in different records: first response body and a later reply
        self.assertEqual(self._search_ids("firstkw replykw"), ["T-response"])
        # each term must fit one ticket: no ticket carries both of these keywords
        self.assertEqual(self._search_ids("custkw subjkw"), [])
        # items are complete stored tickets, total counts before pagination
        result = self.app.search_tickets("firstkw")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"], [self.app.get("T-response")])
        self.assertEqual(result["items"][0]["replies"][-1]["message"], "Replykw later reply body")

    def test_search_tickets_knowledge_matches_saved_message_bodies_not_snapshot_fields(self):
        self._closed_ticket("T-src", subject="Source subject", resolution="Knowkw saved body")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-kr", "Alice", "Need help", opened_at=5)
        self.app.respond_with_knowledge("T-kr", "KB-1", 6)
        self.app.reply_with_knowledge("T-kr", "KB-1", 7)
        # the saved content is searchable through both the first response and a reply
        self.assertEqual(self._search_ids("knowkw"), ["T-kr", "T-src"])
        saved = self.app.get("T-kr")
        self.assertEqual(saved["first_response"]["message"], "Knowkw saved body")
        self.assertEqual(saved["replies"][-1]["message"], "Knowkw saved body")
        # snapshot metadata and the source ticket id never participate:
        # "kb-1"/"T-src" appear only inside the snapshot and the source ticket's id,
        # and the snapshot title "Source subject" is not indexed for T-kr
        for term in ("kb-1", "t-src", "source", "source subject"):
            self.assertEqual(self._search_ids(term), ["T-src"] if "source" in term else [], term)

    def test_search_tickets_trims_splits_on_whitespace_casefolds_and_matches_punctuation_literally(self):
        self.app.open_ticket("T-cf", "Nina", "Reset PASSWORD Straße")
        self.app.open_ticket("T-p", "Owen", "Done, finally.")
        self.app.open_ticket("T-z", "客户甲", "无法登录，请重试")
        ids = self._search_ids
        # ends are trimmed, remaining whitespace splits terms, term order is irrelevant
        self.assertEqual(ids("  password  reset "), ["T-cf"])
        self.assertEqual(ids("\tPASSWORD\n straße "), ["T-cf"])
        # Unicode casefold works in both directions
        self.assertEqual(ids("STRASSE"), ["T-cf"])
        self.assertEqual(ids("straße"), ["T-cf"])
        self.assertEqual(ids("strasse"), ["T-cf"])
        self.assertEqual(ids("NINA"), ["T-cf"])
        # punctuation is matched literally, including Chinese punctuation
        self.assertEqual(ids("done,"), ["T-p"])
        self.assertEqual(ids("finally."), ["T-p"])
        self.assertEqual(ids("finally,"), [])
        self.assertEqual(ids("登录，请"), ["T-z"])
        self.assertEqual(ids("登录,请"), [])
        # a single term must be contiguous; it cannot bridge a gap inside one field
        self.assertEqual(ids("etpass"), [])
        self.assertEqual(ids("resetpassword"), [])

    def test_search_tickets_terms_do_not_cross_fields_or_records(self):
        self.app.open_ticket("T-a", "Zed", "Alpha")
        self.app.note("T-a", "Beta")
        self.app.open_ticket("T-b", "Beta Person", "Gamma")
        self.app.open_ticket("T-c", "foot", "Other")
        self.app.note("T-c", "ball")
        self.app.open_ticket("T-d", "Qux", "foo", opened_at=5)
        self.app.respond("T-d", "first", 6)
        self.app.reply("T-d", "bar one", 7)
        self.app.reply("T-d", "two baz", 8)
        ids = self._search_ids
        # different terms may match different fields of one ticket ...
        self.assertEqual(ids("alpha beta"), ["T-a"])
        # ... and different reply records of one ticket ...
        self.assertEqual(ids("bar baz"), ["T-d"])
        # ... but one term cannot be glued across two fields or two records
        self.assertEqual(ids("football"), [])
        self.assertEqual(ids("barbaz"), [])
        # alpha belongs only to T-a, gamma only to T-b
        self.assertEqual(ids("alpha gamma"), [])

    def test_search_tickets_ignores_ticket_id_assignee_category(self):
        self.app.open_ticket("T-secret", "Alice", "Visible words")
        self.app.assign("T-secret", "uniquename_zoe")
        self.app.set_category("T-secret", "uniquecat_zeta")
        for term in ("zoe", "zeta", "uniquename_zoe", "uniquecat_zeta", "t-secret", "T-SECRET"):
            self.assertEqual(self._search_ids(term), [], term)
        self.assertEqual(self._search_ids("visible"), ["T-secret"])

    def _search_filter_scenario(self):
        # five tickets sharing one searchable word, spanning status and category
        self.app.open_ticket("T-1", "Alice", "Sharedword item")
        self.app.set_category("T-1", "billing")
        self.app.open_ticket("T-2", "Bob", "Sharedword item")
        self.app.set_category("T-2", "billing")
        self.app.assign("T-2", "Eve")
        self.app.close("T-2", "Done")
        self.app.open_ticket("T-3", "Carol", "Sharedword item")
        self.app.set_category("T-3", "network")
        self.app.open_ticket("T-4", "Dan", "Sharedword item")
        self.app.assign("T-4", "Fay")
        self.app.close("T-4", "Done")
        self.app.open_ticket("T-5", "Gus", "Sharedword item")

    def test_search_tickets_status_and_category_apply_as_intersection(self):
        self._search_filter_scenario()
        all_ids = ["T-1", "T-2", "T-3", "T-4", "T-5"]
        self.assertEqual(self._search_ids("sharedword"), all_ids)
        self.assertEqual(self._search_ids("sharedword", status=None), all_ids)
        self.assertEqual(self._search_ids("sharedword", status="open"), ["T-1", "T-3", "T-5"])
        self.assertEqual(self._search_ids("sharedword", status="closed"), ["T-2", "T-4"])
        self.assertEqual(self._search_ids("sharedword", category="billing"), ["T-1", "T-2"])
        self.assertEqual(self._search_ids("sharedword", category="network"), ["T-3"])
        self.assertEqual(self._search_ids("sharedword", category=None), ["T-4", "T-5"])
        # the two conditions intersect
        self.assertEqual(self._search_ids("sharedword", status="open", category="billing"), ["T-1"])
        self.assertEqual(self._search_ids("sharedword", status="closed", category="billing"), ["T-2"])
        self.assertEqual(self._search_ids("sharedword", status="open", category="network"), ["T-3"])
        self.assertEqual(self._search_ids("sharedword", status="open", category=None), ["T-5"])
        self.assertEqual(self._search_ids("sharedword", status="closed", category=None), ["T-4"])
        self.assertEqual(self._search_ids("sharedword", category="missing"), [])
        result = self.app.search_tickets("sharedword", status="closed", category="billing")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["status"], "closed")
        self.assertEqual(result["items"][0]["category"], "billing")

    def test_search_tickets_category_omitted_null_trimming_case_and_internal_space(self):
        self._search_filter_scenario()
        self.app.open_ticket("T-6", "Han", "Sharedword item")
        self.app.set_category("T-6", "bill ing")
        # omitted category imposes no restriction; explicit null means uncategorized only
        self.assertEqual(self._search_ids("sharedword"),
                         ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6"])
        self.assertEqual(self._search_ids("sharedword", category=None), ["T-4", "T-5"])
        # ends are trimmed; comparison is case sensitive; internal whitespace is kept
        self.assertEqual(self._search_ids("sharedword", category=" billing "), ["T-1", "T-2"])
        self.assertEqual(self._search_ids("sharedword", category="bill ing"), ["T-6"])
        self.assertEqual(self._search_ids("sharedword", category=" bill ing "), ["T-6"])
        self.assertEqual(self._search_ids("sharedword", category="bill  ing"), [])
        self.assertEqual(self._search_ids("sharedword", category="Billing"), [])
        for bad in (1, True, [], {}, " ", "\t"):
            with self.assertRaises(ValueError, msg=bad):
                self.app.search_tickets("sharedword", category=bad)

    def test_search_tickets_sorts_by_case_sensitive_ticket_id_before_paginating(self):
        for ticket_id in ("t-a2", "a", "T-b", "B", "T-a1"):
            self.app.open_ticket(ticket_id, "Alice", "Pagedkw content")
        ordered = ["B", "T-a1", "T-b", "a", "t-a2"]
        self.assertEqual(self._search_ids("pagedkw"), ordered)
        self.assertEqual(self._search_ids("pagedkw", limit=2), ordered[:2])
        self.assertEqual(self._search_ids("pagedkw", offset=2, limit=2), ordered[2:4])
        self.assertEqual(self._search_ids("pagedkw", offset=4), [ordered[-1]])
        result = self.app.search_tickets("pagedkw", offset=3)
        self.assertEqual(result["total"], 5)
        self.assertEqual([t["ticket_id"] for t in result["items"]], ordered[3:])
        # total is the pre-pagination count even when the page is empty
        beyond = self.app.search_tickets("pagedkw", offset=5)
        self.assertEqual(beyond, {"total": 5, "items": []})

    def test_search_tickets_pagination_defaults_limit_100_and_offset_beyond_total(self):
        for index in range(1, 26):
            self.app.open_ticket("T-%02d" % index, "Alice", "Manykw content %d" % index)
        ordered = ["T-%02d" % index for index in range(1, 26)]
        default = self.app.search_tickets("manykw")
        self.assertEqual((default["total"], len(default["items"])), (25, 20))
        self.assertEqual([t["ticket_id"] for t in default["items"]], ordered[:20])
        self.assertEqual(self._search_ids("manykw", offset=0), ordered[:20])
        self.assertEqual(self._search_ids("manykw", offset=20), ordered[20:])
        self.assertEqual(self._search_ids("manykw", offset=24), ["T-25"])
        self.assertEqual(self.app.search_tickets("manykw", offset=25),
                         {"total": 25, "items": []})
        self.assertEqual(self.app.search_tickets("manykw", offset=100),
                         {"total": 25, "items": []})
        # the maximum page length is accepted and returns everything
        full = self.app.search_tickets("manykw", limit=100)
        self.assertEqual((full["total"], [t["ticket_id"] for t in full["items"]]), (25, ordered))
        self.assertEqual(self._search_ids("manykw", limit=1), ["T-01"])

    def test_search_tickets_no_match_and_empty_data(self):
        self.assertEqual(self.app.search_tickets("anything"), {"total": 0, "items": []})
        self.assertFalse(self.app.path.exists())
        self.app.open_ticket("T", "Alice", "Real content")
        self.assertEqual(self.app.search_tickets("zzz nomatch"), {"total": 0, "items": []})

    def test_search_tickets_legacy_tickets_missing_category_replies_or_empty_first_response(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        doc = {"tickets": {
            "L-open": {"ticket_id": "L-open", "customer": "Legacy Cust",
                       "subject": "Legkw open plain", "status": "open",
                       "assignee": None, "notes": [], "resolution": None},
            "L-closed": {"ticket_id": "L-closed", "customer": "Legacy Cust",
                         "subject": "Legkw closed plain", "status": "closed",
                         "assignee": "a", "notes": ["Legnotekw inside"],
                         "resolution": "Legreskw fixed"},
            "L-nullfr": {"ticket_id": "L-nullfr", "customer": "Legacy Cust",
                         "subject": "Legkw null response", "status": "open",
                         "assignee": None, "notes": [], "resolution": None,
                         "opened_at": 3, "first_response": None},
            "L-empty": {"ticket_id": "L-empty", "customer": "Legacy Cust",
                        "subject": "Legkw empty response", "status": "open",
                        "assignee": None, "notes": [], "resolution": None,
                        "opened_at": 3, "first_response": {},
                        "replies": [{"message": "", "replied_at": 9},
                                    {"message": "Legreplykw here", "replied_at": 10}]},
        }}
        path = legacy / "data.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        app = SupportDesk(legacy)
        self.assertEqual(app.search_tickets("legkw")["total"], 4)
        self.assertEqual([t["ticket_id"] for t in app.search_tickets("legkw")["items"]],
                         ["L-closed", "L-empty", "L-nullfr", "L-open"])
        self.assertEqual([t["ticket_id"] for t in app.search_tickets("legkw", status="open")["items"]],
                         ["L-empty", "L-nullfr", "L-open"])
        self.assertEqual([t["ticket_id"] for t in app.search_tickets("legkw", category=None)["items"]],
                         ["L-closed", "L-empty", "L-nullfr", "L-open"])
        self.assertEqual([t["ticket_id"] for t in app.search_tickets(
            "legkw", status="closed", category=None)["items"]], ["L-closed"])
        self.assertEqual(app.search_tickets("legnotekw")["items"][0]["ticket_id"], "L-closed")
        self.assertEqual(app.search_tickets("legreskw")["items"][0]["ticket_id"], "L-closed")
        self.assertEqual(app.search_tickets("legreplykw")["items"][0]["ticket_id"], "L-empty")
        # empty first response / reply messages neither match nor break the query
        self.assertEqual(app.search_tickets("legkw", status="open")["total"], 3)
        # raw tickets are returned whole with no backfilled fields
        raw = json.loads(path.read_text(encoding="utf-8"))
        for ticket_id in ("L-open", "L-closed", "L-nullfr", "L-empty"):
            self.assertEqual(app.get(ticket_id), raw["tickets"][ticket_id])
        self.assertNotIn("category", raw["tickets"]["L-closed"])
        self.assertNotIn("replies", raw["tickets"]["L-closed"])
        before = path.read_bytes()
        app.search_tickets("legkw")
        app.search_tickets("legreplykw", status="open", category=None, offset=1, limit=1)
        self.assertEqual(path.read_bytes(), before)

    def test_search_tickets_knowledge_revisions_restore_and_disable_keep_saved_results(self):
        self._closed_ticket("T-src", subject="Old title zeta", resolution="Knowkw saved body")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.open_ticket("T-kr", "Alice", "Need help", opened_at=5)
        self.app.respond_with_knowledge("T-kr", "KB-1", 6)
        self.app.reply_with_knowledge("T-kr", "KB-1", 7)
        self.assertEqual(self._search_ids("knowkw"), ["T-kr", "T-src"])
        # revising the article never rewrites saved reply bodies
        self.app.update_knowledge("KB-1", "Brand title", "Brandkw new body")
        self.assertEqual(self._search_ids("knowkw"), ["T-kr", "T-src"])
        self.assertEqual(self._search_ids("brandkw"), [])
        # a response saved after the revision is hit through its own saved body
        self.app.open_ticket("T-new", "Bob", "Other", opened_at=10)
        self.app.respond_with_knowledge("T-new", "KB-1", 11)
        self.assertEqual(self._search_ids("brandkw"), ["T-new"])
        # restoring an older revision and disabling the article leave existing replies indexed
        self.app.restore_knowledge("KB-1", 1)
        self.app.set_knowledge_enabled("KB-1", False)
        self.assertEqual(self._search_ids("knowkw"), ["T-kr", "T-src"])
        self.assertEqual(self._search_ids("brandkw"), ["T-new"])
        saved = self.app.get("T-kr")
        self.assertEqual(saved["first_response"]["message"], "Knowkw saved body")
        self.assertEqual([r["message"] for r in saved["replies"]], ["Knowkw saved body"])

    def test_search_tickets_rejects_bad_arguments_without_writing(self):
        self._search_field_scenario()
        before = self.app.path.read_bytes()
        for query in (None, 1, 1.5, True, [], {}, " ", "  \t\n "):
            with self.assertRaises(ValueError, msg=query):
                self.app.search_tickets(query)
        for status in ("OPEN", "closed ", "", 0, 1, True, [], {}):
            with self.assertRaises(ValueError, msg=status):
                self.app.search_tickets("custkw", status=status)
        for category in (1, True, [], {}, " ", "\t"):
            with self.assertRaises(ValueError, msg=category):
                self.app.search_tickets("custkw", category=category)
        for offset in (True, False, 1.0, -1, "0", None, 1.5):
            with self.assertRaises(ValueError, msg=offset):
                self.app.search_tickets("custkw", offset=offset)
        for limit in (True, False, 20.0, "20", None, 0, 101, -1):
            with self.assertRaises(ValueError, msg=limit):
                self.app.search_tickets("custkw", limit=limit)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_search_tickets_missing_query_and_unknown_arguments_raise_type_error(self):
        with self.assertRaises(TypeError):
            self.app.search_tickets()
        with self.assertRaises(TypeError):
            self.app.search_tickets(status="open")
        with self.assertRaises(TypeError):
            self.app.search_tickets("custkw", unknown=1)
        with self.assertRaises(TypeError):
            self.app.search_tickets("custkw", 0, "billing", 0, 20, "extra")

    def test_search_tickets_is_read_only_success_and_failure_never_create_files(self):
        self._search_filter_scenario()
        before = self.app.path.read_bytes()
        for kwargs in (
            {}, {"status": "open"}, {"status": "closed"}, {"category": None},
            {"category": "billing"}, {"status": "closed", "category": "billing"},
            {"offset": 2, "limit": 1}, {"offset": 50}, {"limit": 100},
        ):
            self.app.search_tickets("sharedword", **kwargs)
        with self.assertRaises(ValueError):
            self.app.search_tickets(" ")
        with self.assertRaises(ValueError):
            self.app.search_tickets("sharedword", status="nope")
        with self.assertRaises(ValueError):
            self.app.search_tickets("sharedword", offset=-1)
        self.assertEqual(self.app.path.read_bytes(), before)
        # queries against a directory that does not exist create neither it nor data.json
        missing = self.root / "missing"
        fresh = SupportDesk(missing)
        self.assertEqual(fresh.search_tickets("anything"), {"total": 0, "items": []})
        with self.assertRaises(ValueError):
            fresh.search_tickets(" ")
        with self.assertRaises(ValueError):
            fresh.search_tickets("anything", limit=0)
        self.assertFalse(missing.exists())

    def test_search_tickets_results_are_consistent_after_recreating_desk(self):
        self._search_filter_scenario()
        for kwargs in (
            {}, {"status": "open"}, {"category": None}, {"category": "billing"},
            {"status": "closed", "category": "billing"}, {"offset": 1, "limit": 2},
        ):
            self.assertEqual(SupportDesk(self.root).search_tickets("sharedword", **kwargs),
                             self.app.search_tickets("sharedword", **kwargs))

    def _cli_search(self, body):
        payload = self.root / "search-input.json"
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return subprocess.run([sys.executable, "-m", "support_desk", "--root",
                               str(self.root), "ticket-search", str(payload)],
                              text=True, capture_output=True)

    def test_cli_ticket_search_object_and_array_preserve_input_order(self):
        self._search_filter_scenario()
        result = self._cli_search({"query": "sharedword", "status": "open"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         self.app.search_tickets("sharedword", status="open"))
        # a null category survives the JSON boundary
        result = self._cli_search({"query": "sharedword", "category": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([t["ticket_id"] for t in json.loads(result.stdout)["items"]], ["T-4", "T-5"])
        requests = [
            {"query": "sharedword", "status": "closed"},
            {"query": "sharedword", "limit": 1},
            {"query": "sharedword", "offset": 3, "limit": 1},
        ]
        payload = self.root / "batch.json"
        payload.write_text(json.dumps(requests), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                str(self.root), "ticket-search", str(payload)],
                               text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        rows = json.loads(batch.stdout)
        self.assertEqual(len(rows), 3)
        self.assertEqual([t["ticket_id"] for t in rows[0]["items"]], ["T-2", "T-4"])
        self.assertEqual([t["ticket_id"] for t in rows[1]["items"]], ["T-1"])
        self.assertEqual([t["ticket_id"] for t in rows[2]["items"]], ["T-4"])
        self.assertEqual([row["total"] for row in rows], [2, 5, 5])

    def test_cli_ticket_search_failure_exits_2_with_empty_stdout_and_stops_batch(self):
        self._search_filter_scenario()
        for body in (
            {"query": " "},
            {"query": "sharedword", "status": "nope"},
            {},
            {"query": "sharedword", "unknown": 1},
            {"query": "sharedword", "offset": -1},
            {"query": "sharedword", "limit": True},
            {"query": 123},
        ):
            result = self._cli_search(body)
            self.assertEqual(result.returncode, 2, body)
            self.assertEqual(result.stdout, "", body)
            self.assertIn("error", json.loads(result.stderr), body)
        # a failure mid-array prints no partial results at all
        payload = self.root / "bad-batch.json"
        payload.write_text(json.dumps([{"query": "sharedword"},
                                       {"query": "sharedword", "limit": 0},
                                       {"query": "sharedword"}]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "ticket-search", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

    def test_cli_ticket_search_missing_root_and_read_only(self):
        self._search_filter_scenario()
        before = self.app.path.read_bytes()
        result = self._cli_search({"query": "sharedword"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)
        missing = self.root / "missing"
        payload = self.root / "empty-query.json"
        payload.write_text(json.dumps({"query": "anything"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(missing), "ticket-search", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"total": 0, "items": []})
        self.assertFalse(missing.exists())
        bad = self.root / "bad-query.json"
        bad.write_text(json.dumps({"query": " "}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(missing), "ticket-search", str(bad)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertFalse(missing.exists())

    def _review_scenario(self):
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self._closed_ticket("T-src2", subject="Password", resolution="Reset it")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.publish_knowledge("KB-2", "T-src2")
        # T-a: KB-1 first response and KB-1 replies at indexes 0 and 3; the manual
        # reply and the KB-2 reply in between are not references to KB-1
        self.app.open_ticket("T-a", "Alice", "Need help", opened_at=5)
        self.app.respond_with_knowledge("T-a", "KB-1", 5)
        self.app.reply_with_knowledge("T-a", "KB-1", 6)
        self.app.reply("T-a", "manual note", 7)
        self.app.reply_with_knowledge("T-a", "KB-2", 8)
        self.app.reply_with_knowledge("T-a", "KB-1", 9)
        # T-A: manual first response plus one KB-1 reply
        self.app.open_ticket("T-A", "Ann", "Case", opened_at=5)
        self.app.respond("T-A", "Hi", 6)
        self.app.reply_with_knowledge("T-A", "KB-1", 7)
        # T-b: manual first response plus one KB-1 reply
        self.app.open_ticket("T-b", "Bob", "Other", opened_at=4)
        self.app.respond("T-b", "Hi", 5)
        self.app.reply_with_knowledge("T-b", "KB-1", 6)
        # T-c: closed ticket with a KB-1 first response; excluded while closed
        self.app.open_ticket("T-c", "Cara", "Closed one", opened_at=3)
        self.app.respond_with_knowledge("T-c", "KB-1", 4)
        self.app.assign("T-c", "Eve")
        self.app.close("T-c", "Done")
        # every saved KB-1 snapshot is now older than the current content
        self.app.update_knowledge("KB-1", "Download v2", "New link")

    def test_knowledge_review_queue_lists_stale_references_in_order(self):
        self._review_scenario()
        queue = self.app.knowledge_review_queue("KB-1")
        self.assertEqual(set(queue), {"total", "items"})
        self.assertEqual(queue["total"], 3)
        # case-sensitive ticket_id order: uppercase sorts before lowercase
        self.assertEqual([item["ticket"]["ticket_id"] for item in queue["items"]],
                         ["T-A", "T-a", "T-b"])
        first, second, third = queue["items"]
        self.assertEqual(set(first), {"ticket", "references"})
        self.assertEqual(first["references"], [{"kind": "reply", "index": 0}])
        self.assertEqual(second["references"], [{"kind": "first_response", "index": None},
                                                {"kind": "reply", "index": 0},
                                                {"kind": "reply", "index": 3}])
        self.assertEqual(third["references"], [{"kind": "reply", "index": 0}])
        self.assertEqual(second["ticket"], self.app.get("T-a"))
        # a recreated desk returns the same queue
        self.assertEqual(queue, SupportDesk(self.root).knowledge_review_queue("KB-1"))

    def test_knowledge_review_queue_current_content_and_other_articles_are_not_stale(self):
        self._closed_ticket("T-src", subject="Download", resolution="Sent link")
        self._closed_ticket("T-src2", subject="Password", resolution="Reset it")
        self.app.publish_knowledge("KB-1", "T-src")
        self.app.publish_knowledge("KB-2", "T-src2")
        self.app.open_ticket("T-1", "Alice", "Need help", opened_at=5)
        self.app.respond_with_knowledge("T-1", "KB-1", 5)
        self.app.reply_with_knowledge("T-1", "KB-1", 6)
        self.app.reply_with_knowledge("T-1", "KB-2", 7)
        self.assertEqual(self.app.knowledge_review_queue("KB-1"), {"total": 0, "items": []})
        # a no-op update keeps every snapshot current
        self.app.update_knowledge("KB-1", "Download", "Sent link")
        self.assertEqual(self.app.knowledge_review_queue("KB-1"), {"total": 0, "items": []})
        # changing KB-2 does not affect KB-1's queue; KB-2's queue lists only its own
        self.app.update_knowledge("KB-2", "Password v2", "New reset")
        self.assertEqual(self.app.knowledge_review_queue("KB-1"), {"total": 0, "items": []})
        queue = self.app.knowledge_review_queue("KB-2")
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["items"][0]["ticket"]["ticket_id"], "T-1")
        self.assertEqual(queue["items"][0]["references"], [{"kind": "reply", "index": 1}])

    def test_knowledge_review_queue_revision_and_enabled_state_do_not_matter(self):
        self._review_scenario()
        # a fresh reference to the current content is not stale
        self.app.open_ticket("T-d", "Dan", "Fresh", opened_at=10)
        self.app.respond_with_knowledge("T-d", "KB-1", 10)
        # an explicit old revision whose content differs from the current one is stale
        self.app.open_ticket("T-e", "Eve", "Old rev", opened_at=10)
        self.app.respond_with_knowledge("T-e", "KB-1", 10, revision=1)
        queue = self.app.knowledge_review_queue("KB-1")
        ids = [item["ticket"]["ticket_id"] for item in queue["items"]]
        self.assertEqual(queue["total"], 4)
        self.assertNotIn("T-d", ids)
        self.assertIn("T-e", ids)
        # disabled articles are still queryable
        self.app.set_knowledge_enabled("KB-1", False)
        self.assertEqual(self.app.knowledge_review_queue("KB-1")["total"], 4)

    def test_knowledge_review_queue_closed_reopen_and_restore_reevaluate(self):
        self._review_scenario()
        self.assertEqual(self.app.knowledge_review_queue("KB-1")["total"], 3)
        # reopening the closed ticket re-evaluates it against the current content
        self.app.reopen_ticket("T-c", "not fixed")
        queue = self.app.knowledge_review_queue("KB-1")
        self.assertEqual(queue["total"], 4)
        self.assertEqual(queue["items"][-1]["ticket"]["ticket_id"], "T-c")
        self.assertEqual(queue["items"][-1]["references"],
                         [{"kind": "first_response", "index": None}])
        # closing again excludes it
        self.app.assign("T-c", "Eve")
        self.app.close("T-c", "Done again")
        self.assertEqual(self.app.knowledge_review_queue("KB-1")["total"], 3)
        # restoring the original content makes every saved snapshot current again
        self.app.restore_knowledge("KB-1", 1)
        self.assertEqual(self.app.knowledge_review_queue("KB-1"), {"total": 0, "items": []})
        # the stored snapshots themselves are never rewritten
        ticket = self.app.get("T-a")
        self.assertEqual(ticket["first_response"]["knowledge"]["content"], "Sent link")
        self.assertEqual(ticket["replies"][0]["knowledge"]["title"], "Download")

    def test_knowledge_review_queue_pagination(self):
        self._review_scenario()
        queue = self.app.knowledge_review_queue("KB-1", limit=2)
        self.assertEqual(queue["total"], 3)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-A", "T-a"])
        queue = self.app.knowledge_review_queue("KB-1", offset=2, limit=2)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-b"])
        # offset at or beyond the total keeps the real total with an empty page
        self.assertEqual(self.app.knowledge_review_queue("KB-1", offset=3),
                         {"total": 3, "items": []})
        self.assertEqual(self.app.knowledge_review_queue("KB-1", offset=10, limit=100),
                         {"total": 3, "items": []})
        # defaults are offset 0 and limit 20
        self.assertEqual(len(self.app.knowledge_review_queue("KB-1")["items"]), 3)
        # article_id is trimmed before the case-sensitive lookup
        self.assertEqual(self.app.knowledge_review_queue(" KB-1 ")["total"], 3)

    def test_knowledge_review_queue_legacy_records(self):
        knowledge = {"article_id": "KB-1", "source_ticket_id": "T-src",
                     "title": "Old title", "content": "Old content"}
        base = {"customer": "Alice", "subject": "Old", "status": "open",
                "assignee": None, "notes": [], "resolution": None, "opened_at": 1}
        data = {
            # a legacy article without stored history still compares against current content
            "knowledge": {"KB-1": {"article_id": "KB-1", "source_ticket_id": "T-src",
                                   "title": "New title", "content": "New content"}},
            "tickets": {
                "T-stale": {**base, "ticket_id": "T-stale",
                            "first_response": {"message": "Old content", "responded_at": 2,
                                               "knowledge": knowledge}},
                "T-reply": {**base, "ticket_id": "T-reply", "first_response": {},
                            "replies": [{"message": "Old content", "replied_at": 3,
                                         "knowledge": knowledge}]},
                "T-none": {**base, "ticket_id": "T-none"},
                "T-null": {**base, "ticket_id": "T-null", "first_response": None},
                "T-empty": {**base, "ticket_id": "T-empty", "first_response": {}},
            },
        }
        self.app.path.write_text(json.dumps(data), encoding="utf-8")
        queue = self.app.knowledge_review_queue("KB-1")
        self.assertEqual(queue["total"], 2)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]],
                         ["T-reply", "T-stale"])
        self.assertEqual(queue["items"][0]["references"], [{"kind": "reply", "index": 0}])
        self.assertEqual(queue["items"][1]["references"],
                         [{"kind": "first_response", "index": None}])
        # the query never backfills missing fields
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertNotIn("first_response", stored["tickets"]["T-none"])
        self.assertNotIn("replies", stored["tickets"]["T-null"])

    def test_knowledge_review_queue_rejects_bad_arguments_without_writing(self):
        self._review_scenario()
        before = self.app.path.read_bytes()
        for bad in (None, 1, 1.5, True, "", "  ", ["KB-1"]):
            with self.assertRaises(ValueError):
                self.app.knowledge_review_queue(bad)
        # the lookup is case-sensitive and unknown articles are rejected
        with self.assertRaises(ValueError):
            self.app.knowledge_review_queue("kb-1")
        with self.assertRaises(ValueError):
            self.app.knowledge_review_queue("KB-X")
        for bad in (-1, True, 1.5, "0", None):
            with self.assertRaises(ValueError):
                self.app.knowledge_review_queue("KB-1", offset=bad)
        for bad in (0, 101, -1, True, 1.5, "20", None):
            with self.assertRaises(ValueError):
                self.app.knowledge_review_queue("KB-1", limit=bad)
        with self.assertRaises(TypeError):
            self.app.knowledge_review_queue()
        with self.assertRaises(TypeError):
            self.app.knowledge_review_queue("KB-1", unknown=1)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_knowledge_review_queue_is_read_only(self):
        self._review_scenario()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.knowledge_review_queue("KB-1")["total"], 3)
        self.assertEqual(self.app.path.read_bytes(), before)
        # no data means the article does not exist, and the failure creates nothing
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            SupportDesk(missing).knowledge_review_queue("KB-1")
        self.assertFalse(missing.exists())

    def test_cli_knowledge_review_queue(self):
        self._review_scenario()
        payload = self.root / "review.json"
        payload.write_text(json.dumps({"article_id": "KB-1", "limit": 2}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["total"], 3)
        self.assertEqual([i["ticket"]["ticket_id"] for i in value["items"]], ["T-A", "T-a"])
        self.assertEqual(value["items"][1]["references"],
                         [{"kind": "first_response", "index": None},
                          {"kind": "reply", "index": 0},
                          {"kind": "reply", "index": 3}])
        # array input preserves the input order
        payload.write_text(json.dumps([{"article_id": "KB-1", "offset": 3},
                                       {"article_id": "KB-2"}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         [{"total": 3, "items": []}, {"total": 0, "items": []}])
        # failures exit 2 with an error object on stderr and nothing on stdout
        payload.write_text(json.dumps({"article_id": "KB-X"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        # a failure mid-array prints no partial results at all
        payload.write_text(json.dumps([{"article_id": "KB-1"},
                                       {"article_id": "KB-1", "limit": 0}]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        # a failure against a missing root creates neither directory nor file
        missing = self.root / "missing"
        payload.write_text(json.dumps({"article_id": "KB-1"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(missing), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertFalse(missing.exists())

    def test_review_knowledge_confirms_stale_references(self):
        self._review_scenario()
        # make KB-2 stale too, so its queue is observable
        self.app.update_knowledge("KB-2", "Password v2", "New reset")
        result = self.app.review_knowledge(" T-a ", " KB-1 ")
        self.assertEqual(result, {"ticket_id": "T-a", "article_id": "KB-1", "reviewed_count": 3})
        # the full queue is unchanged, but T-a has nothing left pending
        self.assertEqual(self.app.knowledge_review_queue("KB-1")["total"], 3)
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(queue["total"], 2)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-A", "T-b"])
        # the confirmation survives a recreated desk
        self.assertEqual(SupportDesk(self.root).knowledge_review_queue("KB-1", pending_only=True),
                         queue)
        # repeating the review succeeds and still counts the stale references
        self.assertEqual(self.app.review_knowledge("T-a", "KB-1")["reviewed_count"], 3)
        # other articles are unaffected: T-a's stale KB-2 reply is still pending
        queue = self.app.knowledge_review_queue("KB-2", pending_only=True)
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["items"][0]["references"], [{"kind": "reply", "index": 2}])

    def test_review_knowledge_zero_count_writes_nothing(self):
        self._review_scenario()
        self.app.restore_knowledge("KB-1", 1)
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.review_knowledge("T-a", "KB-1"),
                         {"ticket_id": "T-a", "article_id": "KB-1", "reviewed_count": 0})
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_review_knowledge_disabled_article_and_disabled_queue(self):
        self._review_scenario()
        self.app.set_knowledge_enabled("KB-1", False)
        self.assertEqual(self.app.review_knowledge("T-a", "KB-1")["reviewed_count"], 3)
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-A", "T-b"])

    def test_review_knowledge_content_change_invalidates_and_restore_revalidates(self):
        self._review_scenario()
        self.app.review_knowledge("T-a", "KB-1")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 2)
        # changing the current content invalidates the confirmations
        self.app.update_knowledge("KB-1", "Download v3", "Link v3")
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(queue["total"], 3)
        self.assertEqual(queue["items"][1]["references"],
                         [{"kind": "first_response", "index": None},
                          {"kind": "reply", "index": 0},
                          {"kind": "reply", "index": 3}])
        # restoring the reviewed content revalidates the confirmations
        self.app.restore_knowledge("KB-1", 2)
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 2)
        # a different content invalidates them again
        self.app.update_knowledge("KB-1", "Download v4", "Link v4")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 3)

    def test_review_knowledge_only_covers_references_existing_at_submission(self):
        self._review_scenario()
        self.app.review_knowledge("T-a", "KB-1")
        # a later reply with the same stale snapshot needs its own review
        self.app.reply_with_knowledge("T-a", "KB-1", 10, revision=1)
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(queue["total"], 3)
        self.assertEqual(queue["items"][1]["ticket"]["ticket_id"], "T-a")
        self.assertEqual(queue["items"][1]["references"], [{"kind": "reply", "index": 4}])
        # the next review covers every stale reference, already-confirmed ones included
        self.assertEqual(self.app.review_knowledge("T-a", "KB-1")["reviewed_count"], 4)
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 2)

    def test_review_knowledge_close_and_reopen(self):
        self._review_scenario()
        self.app.review_knowledge("T-a", "KB-1")
        self.app.assign("T-a", "Eve")
        self.app.close("T-a", "Done")
        with self.assertRaises(ValueError):
            self.app.review_knowledge("T-a", "KB-1")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 2)
        # reopening keeps the confirmations, judged against the current content
        self.app.reopen_ticket("T-a", "not fixed")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 2)
        self.app.update_knowledge("KB-1", "Download v3", "Link v3")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True)["total"], 3)

    def test_review_knowledge_does_not_rewrite_other_data(self):
        self._review_scenario()
        before = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.app.review_knowledge("T-a", "KB-1")
        after = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(set(after) - set(before), {"knowledge_reviews"})
        for key in before:
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.app.get("T-a")["first_response"]["knowledge"]["content"], "Sent link")

    def test_review_knowledge_rejects_bad_arguments_without_writing(self):
        self._review_scenario()
        before = self.app.path.read_bytes()
        for bad in (None, 1, 1.5, True, "", "  ", ["T-a"]):
            with self.assertRaises(ValueError):
                self.app.review_knowledge(bad, "KB-1")
            with self.assertRaises(ValueError):
                self.app.review_knowledge("T-a", bad)
        # the lookups are case-sensitive
        with self.assertRaises(ValueError):
            self.app.review_knowledge("t-a", "KB-1")
        with self.assertRaises(ValueError):
            self.app.review_knowledge("T-a", "kb-1")
        with self.assertRaises(ValueError):
            self.app.review_knowledge("T-X", "KB-1")
        with self.assertRaises(ValueError):
            self.app.review_knowledge("T-a", "KB-X")
        # closed tickets cannot be reviewed
        with self.assertRaises(ValueError):
            self.app.review_knowledge("T-c", "KB-1")
        with self.assertRaises(TypeError):
            self.app.review_knowledge()
        with self.assertRaises(TypeError):
            self.app.review_knowledge("T-a")
        with self.assertRaises(TypeError):
            self.app.review_knowledge("T-a", "KB-1", unknown=1)
        self.assertEqual(self.app.path.read_bytes(), before)
        # a failure against a missing root creates neither directory nor file
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            SupportDesk(missing).review_knowledge("T-a", "KB-1")
        self.assertFalse(missing.exists())

    def test_knowledge_review_queue_pending_only_filters_and_paginates(self):
        self._review_scenario()
        for bad in (0, 1, "true", None, 1.5):
            with self.assertRaises(ValueError):
                self.app.knowledge_review_queue("KB-1", pending_only=bad)
        # omitted or false keeps the full result
        full = self.app.knowledge_review_queue("KB-1")
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=False), full)
        self.app.review_knowledge("T-a", "KB-1")
        # T-a has no remaining references and drops out of the total
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(queue["total"], 2)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue["items"]], ["T-A", "T-b"])
        # partial confirmation: only the unconfirmed references remain
        self.app.reply_with_knowledge("T-a", "KB-1", 10, revision=1)
        queue = self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(queue["total"], 3)
        self.assertEqual(queue["items"][1]["references"], [{"kind": "reply", "index": 4}])
        # sorting and pagination apply after filtering
        page = self.app.knowledge_review_queue("KB-1", pending_only=True, offset=1, limit=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual([i["ticket"]["ticket_id"] for i in page["items"]], ["T-a"])
        self.assertEqual(self.app.knowledge_review_queue("KB-1", pending_only=True, offset=3),
                         {"total": 3, "items": []})
        # pending_only is read-only
        before = self.app.path.read_bytes()
        self.app.knowledge_review_queue("KB-1", pending_only=True)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_knowledge_review(self):
        self._review_scenario()
        payload = self.root / "review-one.json"
        payload.write_text(json.dumps({"ticket_id": " T-a ", "article_id": "KB-1"}),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"ticket_id": "T-a", "article_id": "KB-1", "reviewed_count": 3})
        # array input preserves the input order and per-item successes
        payload.write_text(json.dumps([{"ticket_id": "T-A", "article_id": "KB-1"},
                                       {"ticket_id": "T-b", "article_id": "KB-1"}]),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         [{"ticket_id": "T-A", "article_id": "KB-1", "reviewed_count": 1},
                          {"ticket_id": "T-b", "article_id": "KB-1", "reviewed_count": 1}])
        payload.write_text(json.dumps({"article_id": "KB-1", "pending_only": True}),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review-queue", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"total": 0, "items": []})
        # failures exit 2 with an error object on stderr and nothing on stdout
        payload.write_text(json.dumps([{"ticket_id": "T-a", "article_id": "KB-1"},
                                       {"ticket_id": "T-X", "article_id": "KB-1"}]),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "knowledge-review", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        # a failure against a missing root creates neither directory nor file
        missing = self.root / "missing"
        payload.write_text(json.dumps({"ticket_id": "T-a", "article_id": "KB-1"}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(missing), "knowledge-review", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertFalse(missing.exists())

    def test_auto_assign_balances_load_and_counts_immediately(self):
        self.app.open_ticket("T-old", "Alice", "A", opened_at=1)
        self.app.assign("T-old", "A")
        self.app.open_ticket("T-1", "Bob", "B", opened_at=2)
        self.app.open_ticket("T-2", "Cara", "C", opened_at=3)
        result = self.app.auto_assign(["A", "B"], max_open=2)
        self.assertEqual([t["ticket_id"] for t in result["assigned"]], ["T-1", "T-2"])
        self.assertEqual([t["assignee"] for t in result["assigned"]], ["B", "A"])
        self.assertEqual(result["remaining"], [])
        again = SupportDesk(self.root).auto_assign(["A", "B"], max_open=2)
        self.assertEqual(again, {"assigned": [], "remaining": []})

    def test_auto_assign_orders_by_priority_time_and_id(self):
        self.app.open_ticket("T-low", "A", "a", opened_at=1)
        self.app.set_priority("T-low", "low")
        self.app.open_ticket("T-untimed", "B", "b")
        self.app.open_ticket("T-b", "C", "c", opened_at=5)
        self.app.open_ticket("T-a", "D", "d", opened_at=5)
        self.app.open_ticket("T-urgent", "E", "e", opened_at=9)
        self.app.set_priority("T-urgent", "urgent")
        result = self.app.auto_assign(["x"], max_open=10)
        self.assertEqual([t["ticket_id"] for t in result["assigned"]],
                         ["T-urgent", "T-a", "T-b", "T-untimed", "T-low"])

    def test_auto_assign_skips_closed_and_assigned_and_counts_open_load(self):
        self.app.open_ticket("T-closed", "A", "a")
        self.app.assign("T-closed", "A")
        self.app.close("T-closed", "Done")
        self.app.open_ticket("T-taken", "B", "b")
        self.app.assign("T-taken", "A")
        self.app.open_ticket("T-open", "C", "c")
        result = self.app.auto_assign(["A"], max_open=2)
        self.assertEqual([t["ticket_id"] for t in result["assigned"]], ["T-open"])
        # the closed ticket does not count, so one slot remains after T-taken
        self.assertEqual(result["remaining"], [])

    def test_auto_assign_no_capacity_returns_all_candidates_untouched(self):
        self.app.open_ticket("T-taken", "A", "a")
        self.app.assign("T-taken", "A")
        self.app.open_ticket("T-waiting", "B", "b")
        before = self.app.path.read_bytes()
        result = self.app.auto_assign(["A"], max_open=1)
        self.assertEqual(result, {"assigned": [], "remaining": ["T-waiting"]})
        self.assertEqual(before, self.app.path.read_bytes())

    def test_auto_assign_missing_root_returns_empty_without_creating(self):
        missing = self.root / "missing"
        result = SupportDesk(missing).auto_assign(["A"])
        self.assertEqual(result, {"assigned": [], "remaining": []})
        self.assertFalse(missing.exists())

    def test_auto_assign_normalizes_names_and_rejects_bad_input(self):
        self.app.open_ticket("T", "A", "a")
        result = self.app.auto_assign(["  Ann  "])
        self.assertEqual(result["assigned"][0]["assignee"], "Ann")
        before = self.app.path.read_bytes()
        for bad in (None, "Ann", [], {}, ["Ann", " Ann"], ["Ann", ""], [1], [None]):
            with self.assertRaises(ValueError):
                self.app.auto_assign(bad)
        for bad_cap in (0, -1, True, 1.5, "2", None):
            with self.assertRaises(ValueError):
                self.app.auto_assign(["A"], bad_cap)
        with self.assertRaises(TypeError):
            self.app.auto_assign()
        with self.assertRaises(TypeError):
            self.app.auto_assign(["A"], unknown=1)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_auto_assign(self):
        self.app.open_ticket("T-1", "A", "a", opened_at=1)
        payload = self.root / "input.json"
        payload.write_text(json.dumps({"assignees": ["A", "B"], "max_open": 1}),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "auto-assign", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([t["ticket_id"] for t in value["assigned"]], ["T-1"])
        self.assertEqual(value["remaining"], [])
        payload.write_text(json.dumps({"assignees": []}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "auto-assign", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

    def test_handover_distributes_by_load_and_persists(self):
        self.app.open_ticket("T-old", "Alice", "A", opened_at=1)
        self.app.assign("T-old", "A")
        self.app.open_ticket("T-1", "Bob", "B", opened_at=2)
        self.app.assign("T-1", "S")
        self.app.open_ticket("T-2", "Cara", "C", opened_at=3)
        self.app.assign("T-2", "S")
        self.app.open_ticket("T-closed", "Dan", "D")
        self.app.assign("T-closed", "S")
        self.app.close("T-closed", "Done")
        result = self.app.handover(" S ", ["A", " B "], " 轮 岗 ", 10, max_open=3)
        self.assertEqual([t["ticket_id"] for t in result], ["T-1", "T-2"])
        self.assertEqual([t["assignee"] for t in result], ["B", "A"])
        for ticket, target in zip(result, ("B", "A")):
            self.assertEqual(ticket["transfer_history"],
                             [{"from_assignee": "S", "to_assignee": target,
                               "reason": "轮 岗", "transferred_at": 10}])
        again = SupportDesk(self.root)
        self.assertEqual(again.get("T-1")["assignee"], "B")
        self.assertEqual(again.get("T-1")["transfer_history"], result[0]["transfer_history"])
        self.assertEqual(again.get("T-closed")["assignee"], "S")
        self.assertNotIn("transfer_history", again.get("T-closed"))
        report = again.assignee_workload_report(10)
        loads = {group["assignee"]: group["open"] for group in report["groups"]}
        self.assertEqual(loads, {"A": 2, "B": 1})

    def test_handover_orders_by_priority_time_and_id(self):
        self.app.open_ticket("T-low", "A", "a", opened_at=1)
        self.app.set_priority("T-low", "low")
        self.app.open_ticket("T-untimed", "B", "b")
        self.app.open_ticket("T-b", "C", "c", opened_at=5)
        self.app.open_ticket("T-a", "D", "d", opened_at=5)
        self.app.open_ticket("T-urgent", "E", "e", opened_at=9)
        self.app.set_priority("T-urgent", "urgent")
        for ticket_id in ("T-low", "T-untimed", "T-b", "T-a", "T-urgent"):
            self.app.assign(ticket_id, "S")
        result = self.app.handover("S", ["R"], "r", 9, max_open=10)
        self.assertEqual([t["ticket_id"] for t in result],
                         ["T-urgent", "T-a", "T-b", "T-untimed", "T-low"])

    def test_handover_capacity_shortfall_rejects_without_changes(self):
        self.app.open_ticket("T-taken", "A", "a")
        self.app.assign("T-taken", "A")
        self.app.open_ticket("T-1", "B", "b")
        self.app.assign("T-1", "S")
        self.app.open_ticket("T-2", "C", "c")
        self.app.assign("T-2", "S")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.handover("S", ["A", "B"], "r", 0, max_open=1)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.get("T-1")["assignee"], "S")
        self.assertNotIn("transfer_history", self.app.get("T-1"))

    def test_handover_time_regression_rejected_and_equal_allowed(self):
        self.app.open_ticket("T-1", "A", "a", opened_at=10)
        self.app.assign("T-1", "S")
        self.app.open_ticket("T-2", "B", "b", opened_at=1)
        self.app.assign("T-2", "X")
        self.app.transfer_ticket("T-2", "S", "move", 20)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.handover("S", ["R"], "r", 9)
        with self.assertRaises(ValueError):
            self.app.handover("S", ["R"], "r", 19)
        self.assertEqual(before, self.app.path.read_bytes())
        result = self.app.handover("S", ["R"], "r", 20)
        self.assertEqual([t["ticket_id"] for t in result], ["T-2", "T-1"])
        self.assertEqual(len(self.app.get("T-2")["transfer_history"]), 2)

    def test_handover_untimed_ticket_skips_time_check_without_backfill(self):
        self.app.open_ticket("T", "A", "a")
        self.app.assign("T", "S")
        result = self.app.handover("S", ["R"], "r", 0)
        self.assertEqual([t["ticket_id"] for t in result], ["T"])
        ticket = self.app.get("T")
        self.assertNotIn("opened_at", ticket)
        self.assertEqual(ticket["transfer_history"][0]["transferred_at"], 0)

    def test_handover_empty_selection_returns_empty_without_creating(self):
        missing = self.root / "missing"
        self.assertEqual(SupportDesk(missing).handover("S", ["R"], "r", 0), [])
        self.assertFalse(missing.exists())
        self.app.open_ticket("T", "A", "a")
        self.app.assign("T", "X")
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.handover("S", ["R"], "r", 0), [])
        self.assertEqual(before, self.app.path.read_bytes())

    def test_handover_rejects_bad_input_without_writing(self):
        self.app.open_ticket("T", "A", "a")
        self.app.assign("T", "S")
        before = self.app.path.read_bytes()
        for bad_source in (None, "", "  ", 1, ["S"]):
            with self.assertRaises(ValueError):
                self.app.handover(bad_source, ["R"], "r", 0)
        for bad_list in (None, "R", [], {}, ["R", " R "], ["R", ""], [1], [None], ["S"], ["R", "s".upper()]):
            with self.assertRaises(ValueError):
                self.app.handover("S", bad_list, "r", 0)
        for bad_reason in (None, "", "  ", 1):
            with self.assertRaises(ValueError):
                self.app.handover("S", ["R"], bad_reason, 0)
        for bad_time in (-1, True, 1.5, "0", None):
            with self.assertRaises(ValueError):
                self.app.handover("S", ["R"], "r", bad_time)
        for bad_cap in (0, -1, True, 1.5, "5", None):
            with self.assertRaises(ValueError):
                self.app.handover("S", ["R"], "r", 0, bad_cap)
        with self.assertRaises(TypeError):
            self.app.handover("S", ["R"], "r")
        with self.assertRaises(TypeError):
            self.app.handover("S", ["R"], "r", 0, unknown=1)
        self.assertEqual(before, self.app.path.read_bytes())
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            SupportDesk(missing).handover("S", [], "r", 0)
        self.assertFalse(missing.exists())

    def test_handover_keeps_other_fields_and_appends_history(self):
        self.app.open_ticket("T", "A", "a", opened_at=1)
        self.app.assign("T", "S")
        self.app.set_priority("T", "high")
        self.app.set_category("T", "billing")
        self.app.note("T", "checked")
        self.app.respond("T", "hello", 2)
        result = self.app.handover("S", ["R"], "r", 5)
        ticket = result[0]
        self.assertEqual(ticket["priority"], "high")
        self.assertEqual(ticket["category"], "billing")
        self.assertEqual(ticket["notes"], ["checked"])
        self.assertEqual(ticket["first_response"], {"message": "hello", "responded_at": 2})
        again = self.app.handover("R", ["S"], "back", 5)
        self.assertEqual([t["ticket_id"] for t in again], ["T"])
        history = self.app.get("T")["transfer_history"]
        self.assertEqual([(h["from_assignee"], h["to_assignee"]) for h in history],
                         [("S", "R"), ("R", "S")])

    def test_cli_handover(self):
        self.app.open_ticket("T-1", "A", "a", opened_at=1)
        self.app.assign("T-1", "S")
        self.app.open_ticket("T-2", "B", "b", opened_at=2)
        self.app.assign("T-2", "S")
        payload = self.root / "input.json"
        payload.write_text(json.dumps({"source_assignee": "S", "assignees": ["R"],
                                       "reason": "r", "transferred_at": 5}),
                           encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "handover", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([t["ticket_id"] for t in value], ["T-1", "T-2"])
        self.assertEqual([t["assignee"] for t in value], ["R", "R"])
        payload.write_text(json.dumps({"source_assignee": "S", "assignees": [],
                                       "reason": "r", "transferred_at": 5}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "handover", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

    def test_cli_handover_batch_keeps_earlier_successes(self):
        self.app.open_ticket("T-1", "A", "a", opened_at=1)
        self.app.assign("T-1", "S")
        self.app.open_ticket("T-2", "B", "b", opened_at=2)
        self.app.assign("T-2", "X")
        payload = self.root / "input.json"
        payload.write_text(json.dumps([
            {"source_assignee": "S", "assignees": ["R"], "reason": "r", "transferred_at": 5},
            {"source_assignee": "X", "assignees": [], "reason": "r", "transferred_at": 5},
        ]), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root",
                                 str(self.root), "handover", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(SupportDesk(self.root).get("T-1")["assignee"], "R")
        self.assertEqual(SupportDesk(self.root).get("T-2")["assignee"], "X")

    def _received_ticket(self, ticket_id, opened_at=0):
        self.app.open_ticket(ticket_id, "Alice", "A", opened_at=opened_at)
        return ticket_id

    def test_receive_persists_trims_ends_keeps_internal_text_and_appends_duplicates(self):
        self._received_ticket("T", 5)
        ticket = self.app.receive(" T ", "  还没好\n请尽快处理!  ", 10)
        self.assertEqual(ticket["customer_messages"],
                         [{"message": "还没好\n请尽快处理!", "received_at": 10}])
        self.assertEqual(set(ticket["customer_messages"][0]), {"message", "received_at"})
        self.assertEqual(ticket, self.app.get("T"))
        self.app.receive("T", "还没好\n请尽快处理!", 10)
        self.app.receive("T", "again", 10)
        messages = SupportDesk(self.root).get("T")["customer_messages"]
        self.assertEqual([(m["message"], m["received_at"]) for m in messages],
                         [("还没好\n请尽快处理!", 10), ("还没好\n请尽快处理!", 10), ("again", 10)])

    def test_receive_field_is_added_only_on_first_success(self):
        self._received_ticket("T", 5)
        self.assertNotIn("customer_messages", self.app.get("T"))
        self.app.receive("T", "m", 6)
        self.assertIn("customer_messages", self.app.get("T"))

    def test_receive_allows_equal_minutes_but_rejects_regression(self):
        self._received_ticket("T", 5)
        self.app.receive("T", "at ten", 10)
        self.app.receive("T", "same minute", 10)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.receive("T", "too early", 9)
        with self.assertRaises(ValueError):
            self.app.receive("T", "before opened_at", 4)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([m["received_at"] for m in self.app.get("T")["customer_messages"]], [10, 10])

    def test_receive_requires_open_ticket_with_opened_at_assignment_independent(self):
        self._received_ticket("T-open", 5)
        self.app.open_ticket("T-untimed", "Bob", "B")
        self.app.open_ticket("T-closed", "Cara", "C", opened_at=5)
        self.app.assign("T-closed", "Eve")
        self.app.respond("T-closed", "Seen", 6)
        self.app.close("T-closed", "Done")
        # an unassigned, unresponded open ticket still receives follow-ups
        ticket = self.app.receive("T-open", "m", 6)
        self.assertIsNone(ticket["assignee"])
        for ticket_id in ("missing", "T-untimed", "T-closed"):
            with self.assertRaises(ValueError, msg=ticket_id):
                self.app.receive(ticket_id, "m", 6)
        with self.assertRaises(ValueError):
            self.app.receive("t-open", "m", 6)

    def test_receive_rejects_bad_input_without_writing(self):
        self._received_ticket("T", 5)
        before = self.app.path.read_bytes()
        for ticket_id, message, received_at in [
            (None, "m", 6), (1, "m", 6), (" ", "m", 6),
            ("T", None, 6), ("T", 1, 6), ("T", " ", 6),
            ("T", "m", -1), ("T", "m", True), ("T", "m", 1.5),
            ("T", "m", "6"), ("T", "m", None),
        ]:
            with self.assertRaises(ValueError, msg=(ticket_id, message, received_at)):
                self.app.receive(ticket_id, message, received_at)
        with self.assertRaises(TypeError):
            self.app.receive("T", "m")
        with self.assertRaises(TypeError):
            self.app.receive("T")
        with self.assertRaises(TypeError):
            self.app.receive("T", "m", 6, "extra")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertNotIn("customer_messages", self.app.get("T"))

    def test_receive_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.receive("T", "m", 6)
        self.assertFalse(fresh.exists())

    def test_customer_queue_counts_waiting_and_overdue_boundary(self):
        self._received_ticket("T", 0)
        self.app.respond("T", "answer", 10)
        # follow-up at 30 stays unanswered; 30-minute wait against target 30 is not overdue
        self.app.receive("T", "later", 30)
        queue = self.app.customer_queue(60)
        self.assertEqual(len(queue), 1)
        row = queue[0]
        self.assertEqual(set(row), {"ticket", "count", "waiting_minutes", "overdue"})
        self.assertEqual((row["count"], row["waiting_minutes"], row["overdue"]), (1, 30, False))
        self.assertEqual(row["ticket"], self.app.get("T"))
        self.assertTrue(self.app.customer_queue(61)[0]["overdue"])

    def test_customer_queue_boundary_is_latest_answer_and_same_minute_is_covered(self):
        self._received_ticket("T", 0)
        self.app.respond("T", "first", 10)
        # earlier than or equal to the first answer: covered
        self.app.receive("T", "q-before", 5)
        self.app.receive("T", "q-same", 10)
        self.app.reply("T", "second", 20)
        # strictly later than the latest answer: unanswered; q@20 is covered, q@21 is not
        self.app.receive("T", "q-reply-same", 20)
        self.app.receive("T", "q-unanswered", 21)
        self.app.receive("T", "q-last", 40)
        queue = self.app.customer_queue(50)
        row = next(i for i in queue if i["ticket"]["ticket_id"] == "T")
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["waiting_minutes"], 29)

    def test_customer_queue_without_any_answer_all_follow_ups_unanswered(self):
        self._received_ticket("T", 0)
        self.app.receive("T", "one", 5)
        self.app.receive("T", "two", 8)
        row = next(i for i in self.app.customer_queue(40)
                   if i["ticket"]["ticket_id"] == "T")
        self.assertEqual((row["count"], row["waiting_minutes"]), (2, 35))

    def test_customer_queue_excludes_closed_answered_and_historyless(self):
        self._received_ticket("T-closed", 0)
        self.app.receive("T-closed", "q", 5)
        self.app.assign("T-closed", "Eve")
        self.app.close("T-closed", "Done")
        self._received_ticket("T-answered", 0)
        self.app.respond("T-answered", "r", 10)
        self.app.receive("T-answered", "q", 10)
        self._received_ticket("T-no-history", 0)
        self.app.respond("T-no-history", "r", 5)
        self._received_ticket("T-empty", 0)
        data = self.app._read()
        data["tickets"]["T-empty"]["first_response"] = {}
        self.app._write(data)
        self.assertEqual(self.app.customer_queue(20), [])
        # null / empty first response still means "no answer": follow-ups there are pending
        self.app.receive("T-empty", "q", 10)
        self.assertEqual([i["ticket"]["ticket_id"] for i in self.app.customer_queue(20)], ["T-empty"])

    def test_customer_queue_sorts_by_earliest_unanswered_then_ticket_id(self):
        self._received_ticket("T-b", 0)
        self.app.receive("T-b", "q", 10)
        self._received_ticket("T-a", 0)
        self.app.receive("T-a", "q", 10)
        self._received_ticket("T-early", 0)
        self.app.receive("T-early", "q", 5)
        queue = self.app.customer_queue(40)
        self.assertEqual([i["ticket"]["ticket_id"] for i in queue], ["T-early", "T-a", "T-b"])

    def test_customer_queue_raises_if_any_follow_up_or_answer_is_later_than_as_of(self):
        self._received_ticket("T-msg", 0)
        self.app.respond("T-msg", "r", 5)
        self.app.receive("T-msg", "q", 40)
        self._received_ticket("T-ans", 0)
        self.app.receive("T-ans", "q", 5)
        self.app.respond("T-ans", "r", 40)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.customer_queue(30)
        self.assertEqual(before, self.app.path.read_bytes())
        # a closed ticket with future records does not participate in the check
        self.app.assign("T-msg", "Eve")
        self.app.close("T-msg", "Done")
        with self.assertRaises(ValueError):
            self.app.customer_queue(30)
        self.app.assign("T-ans", "Fay")
        self.app.close("T-ans", "Done")
        self.assertEqual(self.app.customer_queue(30), [])

    def test_customer_queue_close_reopen_keeps_history_and_re_judges(self):
        self._received_ticket("T", 0)
        self.app.receive("T", "q", 5)
        self.assertEqual(len(self.app.customer_queue(10)), 1)
        self.app.assign("T", "Eve")
        self.app.close("T", "Done")
        self.assertEqual(self.app.customer_queue(10), [])
        self.app.reopen_ticket("T", "back")
        row = self.app.customer_queue(10)[0]
        self.assertEqual(row["ticket"]["ticket_id"], "T")
        self.assertEqual(row["ticket"]["customer_messages"], [{"message": "q", "received_at": 5}])
        # a new answer covering the outstanding follow-up removes it from the queue
        self.app.respond("T", "answer", 10)
        self.assertEqual(self.app.customer_queue(10), [])

    def test_customer_queue_bad_arguments_and_empty_directory(self):
        for as_of in (True, 1.5, "40", None, -1):
            with self.assertRaises(ValueError, msg=as_of):
                self.app.customer_queue(as_of)
        for target in (0, -5, True, 30.0, "30", None):
            with self.assertRaises(ValueError, msg=target):
                self.app.customer_queue(40, target)
        with self.assertRaises(TypeError):
            self.app.customer_queue()
        with self.assertRaises(TypeError):
            self.app.customer_queue(40, bogus=1)
        self.assertEqual(self.app.customer_queue(0), [])
        self.assertFalse(self.app.path.exists())
        missing = self.root / "missing"
        self.assertEqual(SupportDesk(missing).customer_queue(0), [])
        self.assertFalse(missing.exists())

    def test_customer_queue_does_not_affect_notes_search_or_stats(self):
        self._received_ticket("T", 0)
        self.app.receive("T", "uniquefollowupword", 5)
        # follow-ups are not notes and do not appear in ticket search or response stats
        self.assertEqual(self.app.get("T")["notes"], [])
        self.assertEqual(self.app.search_tickets("uniquefollowupword")["items"], [])
        self.assertEqual(self.app.response_stats()["responded"], 0)
        before = self.app.path.read_bytes()
        self.app.customer_queue(10)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_cli_receive_and_customer_queue(self):
        self._received_ticket("T-1", 0)
        self.app.respond("T-1", "answer", 10)
        payload = self.root / "receive.json"
        payload.write_text(json.dumps([
            {"ticket_id": " T-1 ", "message": "  CLI question\nline two. ", "received_at": 30},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "receive", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        ticket = json.loads(result.stdout)[0]
        self.assertEqual(ticket["customer_messages"],
                         [{"message": "CLI question\nline two.", "received_at": 30}])
        query = self.root / "queue.json"
        query.write_text(json.dumps({"as_of": 60}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "customer-queue", str(query)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([(i["ticket"]["ticket_id"], i["count"], i["waiting_minutes"], i["overdue"])
                          for i in value], [("T-1", 1, 30, False)])
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"as_of": -1}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "customer-queue", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        bad_receive = self.root / "bad-receive.json"
        bad_receive.write_text(json.dumps({"ticket_id": "missing", "message": "m",
                                           "received_at": 1}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root),
                                 "receive", str(bad_receive)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))

if __name__ == "__main__":
    unittest.main()
