import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contact_flow import ContactFlow

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = ContactFlow(self.root)

    def test_normalized_email_and_duplicate_rejected(self):
        self.app.add_contact("A", "Alice", "Alice@EXAMPLE.test", "Books")
        with self.assertRaises(ValueError):
            self.app.add_contact("B", "Bob", "alice@example.test", "Other")
        self.assertEqual(ContactFlow(self.root).find("books")[0]["email"], "alice@example.test")

    def test_timeline_is_chronological_and_contact_scoped(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.follow_up("A", "2026-10-02", "Second")
        self.app.follow_up("B", "2026-10-01", "Other")
        self.app.follow_up("A", "2026-10-01", "First")
        self.assertEqual([r["note"] for r in self.app.timeline("A")], ["First", "Second"])

    def test_invalid_date_does_not_add_followup(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.follow_up("A", "2026-02-30", "Invalid")
        self.assertEqual(before, self.app.path.read_bytes())

    def _two_contacts_with_followups(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Toys")
        self.app.follow_up("A", "2026-10-02", "A second")
        self.app.follow_up("B", "2026-10-01", "B first")
        self.app.follow_up("A", "2026-10-01", "A first")
        self.app.follow_up("B", "2026-10-01", "B second")

    def test_merge_moves_followups_and_deletes_source(self):
        self._two_contacts_with_followups()
        result = self.app.merge_contacts("A", "B")
        self.assertEqual(result["moved_followups"], 2)
        self.assertEqual(result["contact"]["contact_id"], "B")
        self.assertEqual(result["contact"]["name"], "Bob")
        self.assertEqual(result["contact"]["email"], "b@example.test")
        self.assertEqual(result["contact"]["organization"], "Toys")
        self.assertEqual([c["contact_id"] for c in self.app.find()], ["B"])
        self.assertEqual([r["note"] for r in self.app.timeline("B")],
                         ["B first", "A first", "B second", "A second"])
        with self.assertRaises(ValueError):
            self.app.timeline("A")
        with self.assertRaises(ValueError):
            self.app.follow_up("A", "2026-10-03", "late")

    def test_merge_persists_and_survives_reopen(self):
        self._two_contacts_with_followups()
        self.app.merge_contacts(" A ", " B ")
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["B"])
        self.assertEqual([r["note"] for r in reopened.timeline("B")],
                         ["B first", "A first", "B second", "A second"])

    def test_merge_without_followups(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Toys")
        result = self.app.merge_contacts("A", "B")
        self.assertEqual(result["moved_followups"], 0)
        self.assertEqual(self.app.timeline("B"), [])

    def test_merge_invalid_arguments_leave_file_untouched(self):
        self._two_contacts_with_followups()
        before = self.app.path.read_bytes()
        for kwargs in ({"source_id": "  ", "target_id": "B"},
                       {"source_id": 1, "target_id": "B"},
                       {"source_id": "A", "target_id": "a"},
                       {"source_id": "A", "target_id": "A "},
                       {"source_id": "A", "target_id": "X"},
                       {"source_id": "X", "target_id": "B"}):
            with self.assertRaises(ValueError):
                self.app.merge_contacts(**kwargs)
        self.assertEqual(before, self.app.path.read_bytes())
        # Case-sensitive lookup: lowercase ids do not match stored ids.
        self.assertEqual([c["contact_id"] for c in self.app.find()], ["A", "B"])

    def test_merge_twice_fails_without_changing_first_result(self):
        self._two_contacts_with_followups()
        self.assertEqual(self.app.merge_contacts("A", "B")["moved_followups"], 2)
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.merge_contacts("A", "B")
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(len(self.app.timeline("B")), 4)

    def test_merge_without_data_file_creates_none(self):
        self.assertFalse(self.app.path.exists())
        with self.assertRaises(ValueError):
            self.app.merge_contacts("A", "B")
        self.assertFalse(self.app.path.exists())

    def test_cli_merge_success_and_error(self):
        self._two_contacts_with_followups()
        source = self.root / "merge.json"
        source.write_text(json.dumps({"source_id": "A", "target_id": "B"}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "merge", str(source)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["moved_followups"], 2)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"source_id": "A", "target_id": "B"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "merge", str(bad)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertTrue(failed.stderr.strip())
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

    def test_cli_merge_batch_stops_after_first_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Toys")
        self.app.add_contact("C", "Carol", "c@example.test", "Tools")
        self.app.follow_up("A", "2026-10-01", "from A")
        batch = self.root / "batch.json"
        batch.write_text(json.dumps([
            {"source_id": "A", "target_id": "B"},
            {"source_id": "A", "target_id": "C"},
            {"source_id": "C", "target_id": "B"},
        ]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "merge", str(batch)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        # First merge landed; the second failed and the third never ran.
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["B", "C"])
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("B")], ["from A"])

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([r["on"] for r in value], ["2026-10-01", "2026-10-02"])
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

if __name__ == "__main__":
    unittest.main()
