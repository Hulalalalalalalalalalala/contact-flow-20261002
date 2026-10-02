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

    def test_tags_default_empty_normalized_persisted_and_cleared(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.assertEqual(self.app.get_tags(" A "), [])
        self.assertEqual(self.app.set_tags("A", [" VIP ", "vip", "华东"]), ["vip", "华东"])
        self.assertEqual(ContactFlow(self.root).get_tags("A"), ["vip", "华东"])
        self.assertEqual(self.app.set_tags("A", []), [])
        self.assertEqual(ContactFlow(self.root).get_tags("A"), [])

    def test_find_filters_by_tags_all_any_and_organization(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip", "north"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tags=["VIP"])], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tags=["vip", "华东"])], ["A"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tags=["vip", "north"], tag_mode="any")], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in self.app.find(organization="books", tags=["north"])], ["B"])
        self.assertEqual(self.app.find(tags=None), self.app.find(tags=[]))
        self.assertEqual(self.app.find(tags=["missing"]), [])
        # contact return structure is unchanged
        self.assertEqual(self.app.find(tags=["vip"])[0], {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})

    def test_tag_validation_rejects_without_partial_or_file_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        for bad_id in ["", "  ", None, 5]:
            with self.assertRaises(ValueError):
                self.app.set_tags(bad_id, ["x"])
            with self.assertRaises(ValueError):
                self.app.get_tags(bad_id)
        with self.assertRaises(ValueError):
            self.app.set_tags("ZZZ", ["x"])
        for bad_tags in [["ok", 1], ["ok", None], ["  "], "vip", ("vip",), {"vip": 1}]:
            with self.assertRaises(ValueError):
                self.app.set_tags("A", bad_tags)
        self.app.set_tags("A", ["vip"])
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.set_tags("A", ["ok", "   "])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.get_tags("A"), ["vip"])
        for kwargs in [{"tags": ["x", 1]}, {"tag_mode": "weird"}, {"tags": [], "tag_mode": "ALL"}]:
            with self.assertRaises(ValueError):
                self.app.find(**kwargs)
        empty = Path(self.temp.name) / "empty"
        fresh = ContactFlow(empty)
        with self.assertRaises(ValueError):
            fresh.set_tags("A", ["x"])
        self.assertFalse(fresh.path.exists())

    def test_merge_unions_normalized_tags_and_removes_source_tags(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip", "north"])
        self.app.follow_up("B", "2026-10-01", "Note")
        result = self.app.merge_contacts("B", "A")
        self.assertEqual(result["contact"], {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["north", "vip", "华东"])
        with self.assertRaises(ValueError):
            reopened.get_tags("B")

    def test_cli_tags_and_tagged_find(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        payload = self.root / "tags.json"
        payload.write_text(json.dumps({"contact_id": "A", "tags": [" VIP ", "华东"]}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), ["vip", "华东"])
        payload.write_text(json.dumps({"contact_id": "A"}), encoding="utf-8")
        got = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "get-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(json.loads(got.stdout), ["vip", "华东"])
        payload.write_text(json.dumps({"tags": ["VIP"], "tag_mode": "all"}), encoding="utf-8")
        found = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "find", str(payload)], text=True, capture_output=True)
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertEqual([c["contact_id"] for c in json.loads(found.stdout)], ["A"])
        payload.write_text(json.dumps({"contact_id": "A", "tags": [1]}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        payload.write_text(json.dumps([{"contact_id": "A", "tags": ["x"]}, {"contact_id": "A", "tags": "bad"}]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(ContactFlow(self.root).get_tags("A"), ["x"])

    def test_import_contacts_basic_order_and_persistence(self):
        source = self.root / "in.csv"
        source.write_text('name,contact_id,organization,email\n Alice , A , Books , Alice@EXAMPLE.test \n"Bob, Jr.",B,"Music, ""Live""",b@example.test\n"Cara\nC",C,Games,c@example.test\n', encoding="utf-8")
        result = self.app.import_contacts(str(source))
        self.assertEqual([c["contact_id"] for c in result], ["A", "B", "C"])
        self.assertEqual(result[0], {"contact_id": "A", "name": "Alice", "email": "alice@example.test", "organization": "Books"})
        self.assertEqual(result[1]["name"], "Bob, Jr.")
        self.assertEqual(result[2]["name"], "Cara\nC")
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["A", "B", "C"])

    def test_import_contacts_utf8_bom_and_empty_sections(self):
        source = self.root / "bom.csv"
        source.write_bytes("\ufeffcontact_id,name,email,organization\nA,Alice,a@example.test,Books\n".encode("utf-8"))
        self.assertEqual([c["contact_id"] for c in self.app.import_contacts(str(source))], ["A"])
        header_only = self.root / "header.csv"
        header_only.write_text("contact_id,name,email,organization\n\n\n", encoding="utf-8")
        empty_root = self.root / "fresh"
        self.assertEqual(ContactFlow(empty_root).import_contacts(str(header_only)), [])
        self.assertFalse((empty_root / "data.json").exists())
        self.assertFalse(empty_root.exists())

    def test_import_contacts_rejects_bad_files_without_writing(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        cases = {
            "empty.csv": "",
            "blank.csv": "\n\n",
            "missing.csv": "contact_id,name,email\nA,Alice,a@example.test\n",
            "extra.csv": "contact_id,name,email,organization,age\nA,Alice,a@example.test,Books,3\n",
            "dup.csv": "contact_id,name,email,email\nA,Alice,a@example.test,b@example.test\n",
            "spaced.csv": "contact_id, name,email,organization\nA,Alice,a@example.test,Books\n",
            "ragged.csv": "contact_id,name,email,organization\nB,Bob,b@example.test\n",
            "badfield.csv": "contact_id,name,email,organization\nB, ,b@example.test,Books\n",
            "bademail.csv": "contact_id,name,email,organization\nB,Bob,nope,Books\n",
            "dupid.csv": "contact_id,name,email,organization\nA,Other,o@example.test,Books\n",
            "dupemail.csv": "contact_id,name,email,organization\nB,Bob, A@example.test ,Books\n",
            "batchdup.csv": "contact_id,name,email,organization\nB,Bob,b@example.test,Books\nC,Cara,B@EXAMPLE.TEST,Games\n",
            "batchsame.csv": "contact_id,name,email,organization\nB,Bob,b@example.test,Books\nB,Bob,b@example.test,Books\n",
        }
        for name, content in cases.items():
            source = self.root / name
            source.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError, msg=name):
                self.app.import_contacts(str(source))
        bad_bytes = self.root / "latin1.csv"
        bad_bytes.write_bytes("contact_id,name,email,organization\nB,Bébé,b@example.test,Books\n".encode("latin-1"))
        with self.assertRaises(ValueError):
            self.app.import_contacts(str(bad_bytes))
        self.assertEqual(before, self.app.path.read_bytes())
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A"])

    def test_import_contacts_rejection_creates_no_storage(self):
        source = self.root / "bad.csv"
        source.write_text("contact_id,name,email,organization\nA,Alice,not-an-email,Books\n", encoding="utf-8")
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.import_contacts(str(source))
        self.assertFalse(fresh.path.exists())
        self.assertFalse((self.root / "fresh").exists())

    def test_import_contacts_path_errors(self):
        with self.assertRaises(FileNotFoundError):
            self.app.import_contacts(str(self.root / "missing.csv"))
        for bad in [None, 5, ["x"], "", "   "]:
            with self.assertRaises(ValueError):
                self.app.import_contacts(bad)
        self.assertFalse(self.app.path.exists())

    def test_cli_import_contacts(self):
        source = self.root / "in.csv"
        source.write_text("contact_id,name,email,organization\nA,Alice,Alice@EXAMPLE.test,Books\nB,Bob,b@example.test,Music\n", encoding="utf-8")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"csv_path": str(source)}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([c["contact_id"] for c in json.loads(ok.stdout)], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A", "B"])
        payload.write_text(json.dumps({"csv_path": str(self.root / "missing.csv")}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        payload.write_text(json.dumps({"csv_path": str(source)}), encoding="utf-8")
        again = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(again.returncode, 2)
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A", "B"])

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([r["on"] for r in value], ["2026-10-01", "2026-10-02"])
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

if __name__ == "__main__":
    unittest.main()
