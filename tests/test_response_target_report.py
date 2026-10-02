import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from support_desk import SupportDesk

AS_OF = 100
DEFAULT_TARGETS = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
PRIORITY_ORDER = ["urgent", "high", "normal", "low"]


def empty_group(priority):
    return {"priority": priority, "target_minutes": DEFAULT_TARGETS[priority],
            "responded": 0, "on_time": 0, "late": 0, "pending": 0, "overdue": 0,
            "untimed": 0, "closed_without_response": 0, "on_time_rate": None}


NORMAL_DEFAULT = {"priority": "normal", "target_minutes": 30,
                  "responded": 2, "on_time": 1, "late": 1, "pending": 2, "overdue": 1,
                  "untimed": 1, "closed_without_response": 1, "on_time_rate": 0.5}

NORMAL_OVERRIDDEN = {"priority": "normal", "target_minutes": 31,
                     "responded": 2, "on_time": 2, "late": 0, "pending": 2, "overdue": 0,
                     "untimed": 1, "closed_without_response": 1, "on_time_rate": 1.0}


class ResponseTargetReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = SupportDesk(self.root)

    def _close(self, ticket_id, resolution="Done"):
        self.app.assign(ticket_id, "Eve")
        self.app.close(ticket_id, resolution)

    def _normal_sample(self):
        # 两张已响应工单，耗时 30 与 31 分钟，后一张已关闭
        self.app.open_ticket("T-resp-30", "Alice", "A", opened_at=10)
        self.app.respond("T-resp-30", "On it", 40)
        self.app.open_ticket("T-resp-31", "Bob", "B", opened_at=10)
        self.app.respond("T-resp-31", "On it", 41)
        self._close("T-resp-31")
        # 两张未关闭且未响应工单，等待 30 与 31 分钟
        self.app.open_ticket("T-wait-30", "Cara", "C", opened_at=70)
        self.app.open_ticket("T-wait-31", "Dan", "D", opened_at=69)
        # 一张无登记时间的已关闭工单
        self.app.open_ticket("T-untimed-closed", "Eve", "E")
        self._close("T-untimed-closed")
        # 一张有登记时间但未响应的已关闭工单
        self.app.open_ticket("T-closed-noresp", "Fay", "F", opened_at=10)
        self._close("T-closed-noresp")

    def _group(self, report, priority):
        return next(group for group in report["groups"] if group["priority"] == priority)

    def test_normal_sample_default_targets_and_group_order(self):
        self._normal_sample()
        report = self.app.response_target_report(AS_OF)
        self.assertEqual(report["as_of"], AS_OF)
        self.assertEqual([group["priority"] for group in report["groups"]], PRIORITY_ORDER)
        self.assertEqual(self._group(report, "normal"), NORMAL_DEFAULT)
        for priority in ("urgent", "high", "low"):
            self.assertEqual(self._group(report, priority), empty_group(priority))
        # 未设置优先级的旧工单归入 normal，且不补写 priority 字段
        self.assertNotIn("priority", self.app.get("T-wait-30"))

    def test_report_is_identical_after_recreating_desk_on_same_directory(self):
        self._normal_sample()
        first = self.app.response_target_report(AS_OF)
        second = SupportDesk(self.root).response_target_report(AS_OF)
        self.assertEqual(first, second)
        self.assertEqual(self._group(second, "normal"), NORMAL_DEFAULT)

    def test_normal_target_override_keeps_other_defaults(self):
        self._normal_sample()
        report = self.app.response_target_report(AS_OF, {"normal": 31})
        self.assertEqual(self._group(report, "normal"), NORMAL_OVERRIDDEN)
        for priority in ("urgent", "high", "low"):
            self.assertEqual(self._group(report, priority), empty_group(priority))

    def test_targets_omitted_null_and_empty_object_all_use_defaults(self):
        self._normal_sample()
        omitted = self.app.response_target_report(AS_OF)
        null = self.app.response_target_report(AS_OF, None)
        empty = self.app.response_target_report(AS_OF, {})
        self.assertEqual(omitted, null)
        self.assertEqual(omitted, empty)
        self.assertEqual(self._group(omitted, "normal"), NORMAL_DEFAULT)

    def test_empty_directory_reports_zero_groups_and_creates_no_file(self):
        report = self.app.response_target_report(0)
        self.assertEqual(report["as_of"], 0)
        self.assertEqual(report["groups"], [empty_group(priority) for priority in PRIORITY_ORDER])
        self.assertFalse(self.app.path.exists())

    def test_report_does_not_mutate_data_or_backfill_fields(self):
        self._normal_sample()
        before = self.app.path.read_bytes()
        self.app.response_target_report(AS_OF)
        self.app.response_target_report(AS_OF, {"normal": 31})
        self.assertEqual(before, self.app.path.read_bytes())
        untimed = self.app.get("T-untimed-closed")
        self.assertNotIn("opened_at", untimed)
        self.assertNotIn("first_response", untimed)

    def test_rejects_bad_as_of_and_targets_without_writing(self):
        self._normal_sample()
        before = self.app.path.read_bytes()
        for as_of in [-1, True, 1.5, "100", None]:
            with self.assertRaises(ValueError, msg=as_of):
                self.app.response_target_report(as_of)
        for targets in [[1], "normal", 5, True,
                        {"critical": 5}, {"NORMAL": 5}, {"normal": 0}, {"normal": -1},
                        {"normal": True}, {"normal": 30.0}, {"normal": "30"}, {"normal": None}]:
            with self.assertRaises(ValueError, msg=targets):
                self.app.response_target_report(AS_OF, targets)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_missing_or_unknown_arguments_raise_type_error(self):
        self._normal_sample()
        before = self.app.path.read_bytes()
        with self.assertRaises(TypeError):
            self.app.response_target_report()
        with self.assertRaises(TypeError):
            self.app.response_target_report(AS_OF, {"normal": 31}, "extra")
        with self.assertRaises(TypeError):
            self.app.response_target_report(as_of=AS_OF, unknown=1)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_future_opened_at_rejected_and_equal_minute_allowed(self):
        self.app.open_ticket("T-future", "Alice", "A", opened_at=101)
        self.app.open_ticket("T-boundary", "Bob", "B", opened_at=AS_OF)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(AS_OF)
        self.assertEqual(before, self.app.path.read_bytes())
        report = self.app.response_target_report(101)
        normal = self._group(report, "normal")
        self.assertEqual((normal["pending"], normal["overdue"]), (2, 0))

    def test_future_responded_at_rejected_and_equal_minute_allowed(self):
        self.app.open_ticket("T", "Alice", "A", opened_at=10)
        self.app.respond("T", "On it", 50)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(49)
        self.assertEqual(before, self.app.path.read_bytes())
        report = self.app.response_target_report(50)
        normal = self._group(report, "normal")
        self.assertEqual((normal["responded"], normal["on_time"]), (1, 0))

    def test_closed_ticket_with_future_opened_at_is_also_rejected(self):
        self.app.open_ticket("T-closed-future", "Alice", "A", opened_at=101)
        self._close("T-closed-future")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.response_target_report(AS_OF)
        self.assertEqual(before, self.app.path.read_bytes())
        report = self.app.response_target_report(101)
        self.assertEqual(self._group(report, "normal")["closed_without_response"], 1)

    def test_failure_creates_no_directory_or_file(self):
        fresh = self.root / "missing"
        app = SupportDesk(fresh)
        with self.assertRaises(ValueError):
            app.response_target_report(-1)
        with self.assertRaises(ValueError):
            app.response_target_report(AS_OF, {"normal": 0})
        self.assertFalse(fresh.exists())

    def _run_cli(self, *argv):
        return subprocess.run([sys.executable, "-m", "support_desk", "--root", str(self.root), *argv],
                              text=True, capture_output=True)

    def test_cli_report_with_object_and_array_input(self):
        self._normal_sample()
        payload = self.root / "report.json"
        payload.write_text(json.dumps({"as_of": AS_OF}), encoding="utf-8")
        result = self._run_cli("response-target-report", str(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._group(json.loads(result.stdout), "normal"), NORMAL_DEFAULT)
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([{"as_of": AS_OF},
                                     {"as_of": AS_OF, "targets": {"normal": 31}}]), encoding="utf-8")
        result = self._run_cli("response-target-report", str(batch))
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual([self._group(value, "normal") for value in values],
                         [NORMAL_DEFAULT, NORMAL_OVERRIDDEN])

    def test_cli_report_failure_outputs_error_and_no_partial_report(self):
        self._normal_sample()
        before = self.app.path.read_bytes()
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"as_of": -1}), encoding="utf-8")
        failed = self._run_cli("response-target-report", str(bad))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        # 批量输入中后续查询失败：不输出部分报表，数据不变
        batch = self.root / "batch-bad.json"
        batch.write_text(json.dumps([{"as_of": AS_OF}, {"as_of": "100"}]), encoding="utf-8")
        failed = self._run_cli("response-target-report", str(batch))
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(failed.stdout, "")
        self.assertEqual(before, self.app.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
