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

if __name__ == "__main__":
    unittest.main()
