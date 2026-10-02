import json
import os
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

    def test_cli_demo_and_invalid_action(self):
        result = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "demo"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual([r["on"] for r in value], ["2026-10-01", "2026-10-02"])
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "not-an-action"], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)

    def write_csv(self, name, content, encoding="utf-8"):
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding=encoding)
        return path

    def test_import_returns_file_order_and_persists_like_add_contact(self):
        csv_path = self.write_csv("contacts.csv",
            "email,contact_id,name,organization\r\n"
            "bob@example.test,B,\"Bob, Jr.\",Music\r\n"
            "ALICE@example.test, A ,\"陈\n小明\",\" 春山书店 \"\r\n")
        result = self.app.import_contacts(str(csv_path))
        self.assertEqual([c["contact_id"] for c in result], ["B", "A"])
        self.assertEqual(result[0], {"contact_id": "B", "name": "Bob, Jr.", "email": "bob@example.test", "organization": "Music"})
        self.assertEqual(result[1], {"contact_id": "A", "name": "陈\n小明", "email": "alice@example.test", "organization": "春山书店"})
        # Reopened workbench sees every imported contact with identical content.
        reopened = ContactFlow(self.root)
        by_id = {c["contact_id"]: c for c in reopened.find()}
        self.assertEqual(by_id["A"], result[1])
        self.assertEqual(by_id["B"], result[0])

    def test_import_accepts_bom_and_reordered_header(self):
        csv_path = self.write_csv("contacts.csv",
            "﻿organization,email,contact_id,name\n"
            "Books,a@example.test,A,Alice\n")
        result = self.app.import_contacts(str(csv_path))
        self.assertEqual(result, [{"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"}])

    def test_import_empty_inputs_create_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        for content in ["", "﻿", "\n", "\r\n"]:
            csv_path = self.write_csv("empty-%d.csv" % len(content.encode("utf-8")), content)
            with self.assertRaises(ValueError):
                fresh.import_contacts(str(csv_path))
            self.assertFalse(fresh_root.exists())
        # Header alone, or header followed only by zero-field blank lines, imports nothing.
        for content in ["contact_id,name,email,organization\n", "contact_id,name,email,organization\n\n\r\n\n"]:
            csv_path = self.write_csv("header-%d.csv" % len(content), content)
            self.assertEqual(fresh.import_contacts(str(csv_path)), [])
            self.assertFalse(fresh_root.exists())

    def test_import_rejects_bad_headers_without_creating_store(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        bad_headers = [
            "contact_id,name,email\n",
            "contact_id,name,email,organization,extra\n",
            "contact_id,name,email,email\n",
            "Contact_ID,name,email,organization\n",
            "contact_id,name,email,organisation\n",
            "contact_id,name,email,organization,x\nA,a@example.test,a,Org,extra\n",
        ]
        for content in bad_headers:
            csv_path = self.write_csv("bad-%d.csv" % len(content), content)
            with self.assertRaises(ValueError):
                fresh.import_contacts(str(csv_path))
        self.assertFalse(fresh_root.exists())

    def test_import_rejects_malformed_records_without_changes(self):
        self.app.add_contact("X", "Existing", "x@example.test", "Books")
        before = self.app.path.read_bytes()
        bad_files = [
            # wrong field count
            "contact_id,name,email,organization\nA,Alice,a@example.test\n",
            "contact_id,name,email,organization\nA,Alice,a@example.test,Org,extra\n",
            # empty/blank required fields
            "contact_id,name,email,organization\n,Alice,a@example.test,Org\n",
            "contact_id,name,email,organization\nA,   ,a@example.test,Org\n",
            "contact_id,name,email,organization\nA,Alice,,Org\n",
            "contact_id,name,email,organization\nA,Alice,a@example.test,  \n",
            # invalid email
            "contact_id,name,email,organization\nA,Alice,no-at-sign,Org\n",
            "contact_id,name,email,organization\nA,Alice,a b@example.test,Org\n",
            "contact_id,name,email,organization\nA,Alice,@example.test,Org\n",
            # row of empty fields is not a blank line
            "contact_id,name,email,organization\n,,,\n",
            # unterminated quoted field
            'contact_id,name,email,organization\nA,"Alice,a@example.test,Org\n',
        ]
        for i, content in enumerate(bad_files):
            csv_path = self.write_csv("bad-record-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                self.app.import_contacts(str(csv_path))
            self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["X"])

    def test_import_rejects_invalid_utf8_without_changes(self):
        csv_path = self.write_csv("latin.csv", b"contact_id,name,email,organization\nA,\xff,a@example.test,Org\n")
        with self.assertRaises(ValueError):
            self.app.import_contacts(str(csv_path))
        self.assertFalse(self.app.path.exists())

    def test_import_rejects_duplicates_against_existing_data(self):
        self.app.add_contact("A", "Alice", "alice@example.test", "Books")
        before = self.app.path.read_bytes()
        cases = [
            "contact_id,name,email,organization\nA,Other,a2@example.test,Org\n",
            "contact_id,name,email,organization\nB,Other,ALICE@example.test,Org\n",
            "contact_id,name,email,organization\nB,Other, ALICE@example.test ,Org\n",
        ]
        for i, content in enumerate(cases):
            csv_path = self.write_csv("dup-existing-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                self.app.import_contacts(str(csv_path))
            self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A"])

    def test_import_rejects_duplicates_within_file_even_identical(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        # Even byte-identical rows collide; the later row rejects the whole batch.
        row = "D,Dee,dee@example.test,Dept"
        csv_path = self.write_csv("dup.csv",
            "contact_id,name,email,organization\n" + row + "\n" + row + "\n")
        with self.assertRaises(ValueError):
            fresh.import_contacts(str(csv_path))
        self.assertFalse(fresh_root.exists())
        # A valid first row followed by a later duplicate is also fully rolled back.
        csv_path = self.write_csv("dup2.csv",
            "contact_id,name,email,organization\n"
            "A,Alice,a@example.test,Books\n"
            "B,Bob,b@example.test,Music\n"
            "C,Cara,A@example.test,Games\n")
        with self.assertRaises(ValueError):
            fresh.import_contacts(str(csv_path))
        self.assertFalse(fresh_root.exists())

    def test_import_validates_csv_path(self):
        for bad in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.import_contacts(bad)
        with self.assertRaises(FileNotFoundError):
            self.app.import_contacts(str(self.root / "missing.csv"))
        self.assertFalse(self.app.path.exists())

    def test_import_unreadable_file_raises_permission_error(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        csv_path = self.write_csv("locked.csv", "contact_id,name,email,organization\nA,Alice,a@example.test,Books\n")
        csv_path.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                self.app.import_contacts(str(csv_path))
        finally:
            csv_path.chmod(0o644)
        self.assertFalse(self.app.path.exists())

    def test_import_resolves_relative_path_from_cwd(self):
        self.write_csv("people.csv",
            "contact_id,name,email,organization\nA,Alice,a@example.test,Books\n")
        old_cwd = os.getcwd()
        os.chdir(self.root)
        try:
            result = self.app.import_contacts("people.csv")
        finally:
            os.chdir(old_cwd)
        self.assertEqual(result[0]["contact_id"], "A")

    def test_cli_import_contacts_success_and_failure(self):
        csv_path = self.write_csv("people.csv",
            "contact_id,name,email,organization\n"
            "A,Alice,a@example.test,Books\n"
            "B,Bob,b@example.test,Music\n")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"csv_path": str(csv_path)}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([c["contact_id"] for c in json.loads(ok.stdout)], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["A", "B"])
        # Second import collides with existing contacts: stderr JSON, exit 2, no stdout.
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        # Bad header also fails without creating anything in a fresh root.
        bad_csv = self.write_csv("bad.csv", "contact_id,name,email\nA,Alice,a@example.test\n")
        fresh_root = self.root / "fresh"
        payload.write_text(json.dumps({"csv_path": str(bad_csv)}), encoding="utf-8")
        rejected = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(fresh_root), "import-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(fresh_root.exists())

    def test_funnel_report_totals_groups_and_csv(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Games")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.add_opportunity("O1", "A", "One")
        self.app.add_opportunity("O2", "A", "Two")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        self.app.add_opportunity("O3", "B", "Three")
        self.app.set_stage("O3", "lost")
        self.app.add_opportunity("O4", "C", "Four")
        self.app.set_stage("O4", "qualified")
        report = self.app.funnel_report()
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        self.assertEqual(report["total"], {"contacts": 4, "new": 1, "qualified": 1, "won": 1, "lost": 1, "opportunities": 4})
        # Contacts without opportunities still appear, with every stage at zero.
        self.assertEqual([o["organization"] for o in report["organizations"]], ["Books", "Games", "Music"])
        self.assertEqual(report["organizations"][0], {"organization": "Books", "contacts": 2, "new": 1, "qualified": 0, "won": 1, "lost": 1, "opportunities": 3})
        self.assertEqual(report["organizations"][1], {"organization": "Games", "contacts": 1, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(report["organizations"][2], {"organization": "Music", "contacts": 1, "new": 0, "qualified": 1, "won": 0, "lost": 0, "opportunities": 1})
        # CSV mirrors the organizations rows, LF-separated with a trailing newline.
        self.assertEqual(report["csv"], "organization,contacts,new,qualified,won,lost,opportunities\n"
                                        "Books,2,1,0,1,1,3\n"
                                        "Games,1,0,0,0,0,0\n"
                                        "Music,1,0,1,0,0,1\n")
        self.assertNotIn("\r", report["csv"])

    def test_funnel_report_display_name_min_codepoint_in_filtered_set(self):
        self.app.add_contact("A", "Alice", "a@example.test", "books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "BOOKS")
        # Filtering keeps only "books" and "BOOKS"; "Books" is excluded and cannot win the name.
        report = self.app.funnel_report(tags=["x"])
        self.assertEqual(report["organizations"], [])
        self.app.set_tags("A", ["x"])
        self.app.set_tags("C", ["x"])
        report = self.app.funnel_report(tags=["x"])
        self.assertEqual(len(report["organizations"]), 1)
        self.assertEqual(report["organizations"][0]["organization"], "BOOKS")

    def test_funnel_report_filters_intersect(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.add_opportunity("O1", "A", "One")
        self.app.set_stage("O1", "qualified")
        self.app.set_stage("O1", "won")
        self.app.add_opportunity("O2", "B", "Two")
        report = self.app.funnel_report(organization="books", tags=["vip"])
        self.assertEqual(report["total"], {"contacts": 1, "new": 0, "qualified": 0, "won": 1, "lost": 0, "opportunities": 1})
        self.assertEqual([o["organization"] for o in report["organizations"]], ["Books"])
        self.assertEqual(self.app.funnel_report(organization="books", tags=["华东", "north"])["total"]["contacts"], 0)
        self.assertEqual(self.app.funnel_report(organization="books", tags=["华东", "north"], tag_mode="any")["total"]["contacts"], 1)
        empty = self.app.funnel_report(organization="missing")
        self.assertEqual(empty["total"], {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(empty["organizations"], [])
        self.assertEqual(empty["csv"], "organization,contacts,new,qualified,won,lost,opportunities\n")

    def test_funnel_report_csv_escaping(self):
        self.app.add_contact("A", "Alice", "a@example.test", 'A,B "店"\n二楼')
        report = self.app.funnel_report()
        self.assertEqual(
            report["csv"],
            'organization,contacts,new,qualified,won,lost,opportunities\n'
            '"A,B ""店""\n二楼",1,0,0,0,0,0\n')

    def test_funnel_report_validation_and_read_only(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        for kwargs in [{"organization": 5}, {"organization": []}, {"tags": [1]}, {"tags": "x"}, {"tag_mode": "ALL"}, {"tag_mode": "weird"}]:
            with self.assertRaises(ValueError):
                self.app.funnel_report(**kwargs)
        self.app.funnel_report(organization=None, tags=None, tag_mode="all")
        # Querying a root with no data file neither creates it nor its directory.
        empty = self.root / "empty"
        result = ContactFlow(empty).funnel_report()
        self.assertFalse(empty.exists())
        self.assertEqual(result["total"], {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(result["organizations"], [])
        # Legacy data without an opportunities collection reports zero stages.
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "data.json").write_text(json.dumps({"contacts": {
            "A": {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Org"}}}), encoding="utf-8")
        legacy_report = ContactFlow(legacy).funnel_report()
        self.assertEqual(legacy_report["organizations"], [
            {"organization": "Org", "contacts": 1, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0}])

    def test_funnel_report_regroups_after_merge(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.add_opportunity("O1", "B", "One")
        self.app.set_stage("O1", "qualified")
        self.app.set_stage("O1", "won")
        self.app.add_opportunity("O2", "B", "Two")
        self.app.set_stage("O2", "lost")
        self.app.merge_contacts("B", "A")
        report = self.app.funnel_report()
        self.assertEqual(report["total"], {"contacts": 1, "new": 0, "qualified": 0, "won": 1, "lost": 1, "opportunities": 2})
        self.assertEqual([o["organization"] for o in report["organizations"]], ["Books"])
        self.assertEqual(report["organizations"][0]["opportunities"], 2)
        # Merged tags apply to the regrouped contact.
        self.assertEqual(self.app.funnel_report(tags=["vip"])["total"]["contacts"], 1)

    def test_cli_funnel_report(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_opportunity("O1", "A", "One")
        self.app.set_stage("O1", "qualified")
        self.app.set_stage("O1", "won")
        # No input file reports every contact.
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "funnel-report"], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        self.assertEqual(report["total"]["contacts"], 2)
        self.assertEqual(report["total"]["won"], 1)
        # Object and array inputs behave like the other commands.
        payload = self.root / "funnel.json"
        payload.write_text(json.dumps({"organization": "books"}), encoding="utf-8")
        filtered = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "funnel-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertEqual(json.loads(filtered.stdout)["total"]["contacts"], 1)
        payload.write_text(json.dumps([{}, {"organization": "music"}]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "funnel-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertEqual([r["total"]["contacts"] for r in json.loads(batch.stdout)], [2, 1])
        # Invalid arguments fail with exit 2, empty stdout and JSON on stderr.
        for body in [{"organization": 5}, {"tags": [1]}, {"tag_mode": "ALL"}]:
            payload.write_text(json.dumps(body), encoding="utf-8")
            failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "funnel-report", str(payload)], text=True, capture_output=True)
            self.assertEqual(failed.returncode, 2, body)
            self.assertEqual(failed.stdout, "")
            json.loads(failed.stderr)

if __name__ == "__main__":
    unittest.main()
