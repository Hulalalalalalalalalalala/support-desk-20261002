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

if __name__ == "__main__":
    unittest.main()
