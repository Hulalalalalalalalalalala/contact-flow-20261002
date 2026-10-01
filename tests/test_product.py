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

    def test_merge_moves_followups_and_removes_source(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.follow_up("B", "2026-10-02", "Source later")
        self.app.follow_up("A", "2026-10-01", "Target first")
        self.app.follow_up("B", "2026-10-01", "Source same day")
        result = self.app.merge_contacts(" B ", "A")
        self.assertEqual(set(result), {"contact", "moved_followups"})
        self.assertEqual(result["moved_followups"], 2)
        self.assertEqual(result["contact"], {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["A"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["Target first", "Source same day", "Source later"])
        with self.assertRaises(ValueError):
            reopened.follow_up("B", "2026-10-03", "Gone")
        with self.assertRaises(ValueError):
            reopened.timeline("B")
        with self.assertRaises(ValueError):
            reopened.merge_contacts("B", "A")

    def test_merge_without_followups_moves_zero(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        result = self.app.merge_contacts("B", "A")
        self.assertEqual(result["moved_followups"], 0)
        self.assertEqual(ContactFlow(self.root).timeline("A"), [])

    def test_merge_rejects_bad_arguments_without_writing(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        for source, target in [("A", " A "), ("", "A"), ("  ", "A"), (None, "A"), ("B", "A"), ("A", "B")]:
            with self.assertRaises(ValueError):
                self.app.merge_contacts(source, target)
        self.assertEqual(before, self.app.path.read_bytes())

    def test_merge_without_data_file_creates_nothing(self):
        with self.assertRaises(ValueError):
            self.app.merge_contacts("A", "B")
        self.assertFalse(self.app.path.exists())

    def test_cli_merge_object_and_array(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        self.app.follow_up("B", "2026-10-01", "Note")
        payload = self.root / "merge.json"
        payload.write_text(json.dumps({"source_id": "B", "target_id": "A"}), encoding="utf-8")
        merged = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "merge", str(payload)], text=True, capture_output=True)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertEqual(json.loads(merged.stdout)["moved_followups"], 1)
        payload.write_text(json.dumps([{"source_id": "C", "target_id": "A"}, {"source_id": "B", "target_id": "A"}]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "merge", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        json.loads(partial.stderr)
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A"])

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([r["on"] for r in value], ["2026-10-01", "2026-10-02"])
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

    def test_tags_default_empty_for_new_and_legacy_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.assertEqual(self.app.get_tags("A"), [])
        legacy = {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test", "organization": "Books"}}}
        self.app.path.parent.mkdir(parents=True, exist_ok=True)
        self.app.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(ContactFlow(self.root).get_tags("L"), [])

    def test_set_tags_normalizes_dedupes_sorts_and_persists(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.assertEqual(self.app.set_tags(" A ", [" VIP ", "vip", "华东"]), ["vip", "华东"])
        self.assertEqual(self.app.get_tags("A"), ["vip", "华东"])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        # Internal whitespace is preserved; surrounding whitespace is not.
        self.assertEqual(reopened.set_tags("A", ["  华 东 ", "华 东", "VIP"]), ["vip", "华 东"])

    def test_set_empty_tags_clears(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        self.assertEqual(self.app.set_tags("A", []), [])
        self.assertEqual(ContactFlow(self.root).get_tags("A"), [])

    def test_tag_contact_structure_is_unchanged(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        self.assertEqual(self.app.find()[0], {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})

    def test_set_tags_rejects_bad_arguments_without_writing(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip", "books"])
        before = self.app.path.read_bytes()
        for contact_id in ["", "  ", None, 123, "B"]:
            with self.assertRaises(ValueError):
                self.app.set_tags(contact_id, ["x"])
        for tags in [None, "vip", 123, {"x": 1}, ["ok", 1], ["ok", ""], ["ok", "   "], ["ok", None]]:
            with self.assertRaises(ValueError):
                self.app.set_tags("A", tags)
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual(self.app.get_tags("A"), ["books", "vip"])
        for contact_id in ["", "  ", None, 4.5, "B"]:
            with self.assertRaises(ValueError):
                self.app.get_tags(contact_id)

    def test_tag_methods_without_data_file_create_nothing(self):
        with self.assertRaises(ValueError):
            self.app.set_tags("A", ["x"])
        with self.assertRaises(ValueError):
            self.app.get_tags("A")
        self.assertFalse(self.app.path.exists())

    def test_find_filters_by_tags_all_and_any(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Books")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip"])
        self.assert_returns(["A", "B", "C"], {})
        self.assert_returns(["A", "B", "C"], {"tags": None})
        self.assert_returns(["A", "B", "C"], {"tags": []})
        self.assert_returns(["A"], {"tags": ["vip", "华东"]})
        self.assert_returns(["A"], {"tags": [" VIP ", " 华东 "], "tag_mode": "all"})
        self.assert_returns(["A", "B"], {"tags": ["vip"], "tag_mode": "any"})
        self.assert_returns(["A", "B"], {"tags": ["vip", "华东"], "tag_mode": "any"})
        self.assertEqual(self.app.find(tags=["ghost"]), [])

    def test_find_combines_organization_and_tags(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        self.assertEqual([c["contact_id"] for c in self.app.find(organization="books", tags=["vip"])], ["A"])
        self.assertEqual(self.app.find(organization="music", tags=["vip"]), [])

    def test_find_validates_tags_and_mode(self):
        with self.assertRaises(ValueError):
            self.app.find(tag_mode="nope")
        with self.assertRaises(ValueError):
            self.app.find(tags=[], tag_mode="nope")
        with self.assertRaises(ValueError):
            self.app.find(tags=[], tag_mode=None)
        with self.assertRaises(ValueError):
            self.app.find(tags=["ok", 1])
        with self.assertRaises(ValueError):
            self.app.find(tags="vip")

    def assert_returns(self, ids, kwargs):
        self.assertEqual([c["contact_id"] for c in self.app.find(**kwargs)], ids)

    def test_merge_unions_tags_and_drops_source_tags(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip", "新客"])
        self.app.follow_up("B", "2026-10-01", "Note")
        result = self.app.merge_contacts("B", "A")
        self.assertEqual(result["contact"], {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})
        self.assertEqual(result["moved_followups"], 1)
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东", "新客"])
        with self.assertRaises(ValueError):
            reopened.get_tags("B")
        self.assertEqual([c["contact_id"] for c in reopened.find(tags=["新客"])], ["A"])
        self.assertEqual(reopened.timeline("A")[0]["note"], "Note")

    def test_merge_with_only_source_tags_transfers_them(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("B", ["新客"])
        self.app.merge_contacts("B", "A")
        self.assertEqual(ContactFlow(self.root).get_tags("A"), ["新客"])

    def test_merge_without_tags_stays_clean(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.merge_contacts("B", "A")
        self.assertEqual(ContactFlow(self.root).get_tags("A"), [])

    def test_cli_tags_and_find_filters(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        payload = self.root / "tags.json"
        payload.write_text(json.dumps({"contact_id": "A", "tags": [" VIP ", "vip", "华东"]}), encoding="utf-8")
        set_result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        self.assertEqual(json.loads(set_result.stdout), ["vip", "华东"])
        payload.write_text(json.dumps({"contact_id": "A"}), encoding="utf-8")
        get_result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "get-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(get_result.returncode, 0, get_result.stderr)
        self.assertEqual(json.loads(get_result.stdout), ["vip", "华东"])
        query = self.root / "find.json"
        query.write_text(json.dumps({"organization": "books", "tags": ["vip"]}), encoding="utf-8")
        find_result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "find", str(query)], text=True, capture_output=True)
        self.assertEqual(find_result.returncode, 0, find_result.stderr)
        self.assertEqual([c["contact_id"] for c in json.loads(find_result.stdout)], ["A"])

    def test_cli_tags_array_keeps_prior_successes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        payload = self.root / "tags.json"
        payload.write_text(json.dumps([{"contact_id": "A", "tags": ["vip"]}, {"contact_id": "B", "tags": ["ok", 1]}]), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        json.loads(result.stderr)
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.get_tags("B"), [])

if __name__ == "__main__":
    unittest.main()
