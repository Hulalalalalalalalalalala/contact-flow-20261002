import csv
import io
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

    def seed_funnel(self):
        # Books (casefold group): A with two opportunities (new + won), D with one qualified.
        # books: B with one lost. Music: C with no opportunities.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        self.app.add_opportunity("O3", "D", "Third")
        self.app.set_stage("O3", "qualified")
        self.app.add_opportunity("O4", "B", "Fourth")
        self.app.set_stage("O4", "lost")

    def test_funnel_report_counts_current_stages_and_groups_organizations(self):
        self.seed_funnel()
        report = self.app.funnel_report()
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        self.assertEqual(report["total"],
            {"contacts": 4, "new": 1, "qualified": 1, "won": 1, "lost": 1, "opportunities": 4})
        # Organizations are grouped by casefold value, sorted by that key;
        # the display name is the code-point-smallest original value ("Books" < "books").
        self.assertEqual([row["organization"] for row in report["organizations"]], ["Books", "Music"])
        books = report["organizations"][0]
        self.assertEqual(books,
            {"organization": "Books", "contacts": 3, "new": 1, "qualified": 1, "won": 1, "lost": 1, "opportunities": 4})
        # A contact with no opportunities is included as an organization but adds no stages.
        self.assertEqual(report["organizations"][1],
            {"organization": "Music", "contacts": 1, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})

    def test_funnel_report_csv_matches_organizations(self):
        self.seed_funnel()
        report = self.app.funnel_report()
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["organization", "contacts", "new", "qualified", "won", "lost", "opportunities"])
        decoded = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual([row["organization"] for row in decoded],
                         [row["organization"] for row in report["organizations"]])
        for decoded_row, row in zip(decoded, report["organizations"]):
            self.assertEqual(int(decoded_row["contacts"]), row["contacts"])
            self.assertEqual(int(decoded_row["new"]), row["new"])
            self.assertEqual(int(decoded_row["qualified"]), row["qualified"])
            self.assertEqual(int(decoded_row["won"]), row["won"])
            self.assertEqual(int(decoded_row["lost"]), row["lost"])
            self.assertEqual(int(decoded_row["opportunities"]), row["opportunities"])
        # LF-terminated records, final newline, only organization rows.
        self.assertTrue(report["csv"].endswith("\n"))
        self.assertNotIn("\r", report["csv"])
        self.assertEqual(len(report["csv"].splitlines()), len(report["organizations"]) + 1)

    def test_funnel_report_csv_escapes_and_preserves_internal_newlines(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Book, \"店\"\n二楼")
        report = self.app.funnel_report()
        self.assertIn('"Book, ""店""\n二楼",1,0,0,0,0,0', report["csv"])
        # The embedded newline survives a strict CSV round-trip.
        rows = list(csv.reader(io.StringIO(report["csv"]), strict=True))
        self.assertEqual(rows[1][0], "Book, \"店\"\n二楼")

    def test_funnel_report_filters_organization_tags_and_intersection(self):
        self.seed_funnel()
        by_org = self.app.funnel_report(organization="  BOOKS ")
        self.assertEqual([row["organization"] for row in by_org["organizations"]], ["Books"])
        self.assertEqual(by_org["total"],
            {"contacts": 3, "new": 1, "qualified": 1, "won": 1, "lost": 1, "opportunities": 4})
        tagged = self.app.funnel_report(tags=["VIP"])
        # Only A matches: its won opportunity stays won and is not counted as qualified.
        self.assertEqual(tagged["total"],
            {"contacts": 1, "new": 1, "qualified": 0, "won": 1, "lost": 0, "opportunities": 2})
        self.assertEqual(tagged["organizations"][0]["organization"], "Books")
        # Organization and tag conditions intersect: no contact in Music carries the tag.
        self.assertEqual(self.app.funnel_report(organization="Music", tags=["vip"])["total"],
            {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(self.app.funnel_report(tags=["vip", "华东"])["total"]["contacts"], 1)
        self.assertEqual(self.app.funnel_report(tags=["vip", "missing"], tag_mode="any")["total"]["contacts"], 1)

    def test_funnel_report_empty_and_legacy_data(self):
        # No data file at all.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        report = fresh.funnel_report()
        self.assertEqual(report["total"],
            {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(report["organizations"], [])
        self.assertEqual(report["csv"], "organization,contacts,new,qualified,won,lost,opportunities\n")
        self.assertFalse(fresh_root.exists())
        # Legacy data with contacts but no opportunities.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        report = self.app.funnel_report()
        self.assertEqual(report["total"],
            {"contacts": 1, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0})
        self.assertEqual(report["organizations"][0]["new"], 0)

    def test_funnel_report_regroups_after_merge_without_extra_opportunities(self):
        self.seed_funnel()
        before = self.app.funnel_report()["total"]["opportunities"]
        self.app.merge_contacts("A", "C")  # into Music; tags travel with the contact
        report = self.app.funnel_report()
        self.assertEqual(report["total"]["opportunities"], before)
        by_org = {row["organization"]: row for row in report["organizations"]}
        self.assertEqual(by_org["Music"],
            {"organization": "Music", "contacts": 1, "new": 1, "qualified": 0, "won": 1, "lost": 0, "opportunities": 2})
        self.assertEqual(by_org["Books"],
            {"organization": "Books", "contacts": 2, "new": 0, "qualified": 1, "won": 0, "lost": 1, "opportunities": 2})
        # The merged contact keeps its tags, so the vip filter now resolves to Music.
        tagged = self.app.funnel_report(tags=["vip"])
        self.assertEqual(tagged["organizations"][0]["organization"], "Music")
        self.assertEqual(tagged["total"]["opportunities"], 2)

    def test_funnel_report_validates_arguments_without_writing(self):
        self.seed_funnel()
        before = self.app.path.read_bytes()
        for kwargs in [{"organization": 5}, {"organization": ["x"]},
                       {"tags": ["ok", 1]}, {"tags": "vip"},
                       {"tag_mode": "ALL"}, {"tag_mode": "weird"}]:
            with self.assertRaises(ValueError):
                self.app.funnel_report(**kwargs)
        self.assertEqual(self.app.path.read_bytes(), before)
        # None and plain strings are accepted without side effects.
        self.assertEqual(self.app.funnel_report(organization=None)["total"]["contacts"], 4)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_funnel_report_success_and_failure(self):
        self.seed_funnel()
        # No input file: report over all data.
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "funnel-report"],
                            text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(report["total"]["opportunities"], 4)
        self.assertEqual(report["total"]["contacts"], 4)
        # Object input with filters, array input behaves like repeated calls.
        payload = self.root / "funnel.json"
        payload.write_text(json.dumps({"tags": ["vip"]}), encoding="utf-8")
        filtered = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                   "funnel-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertEqual(json.loads(filtered.stdout)["total"]["contacts"], 1)
        payload.write_text(json.dumps([{}, {"organization": "Music"}]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "funnel-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual(values[0]["total"]["contacts"], 4)
        self.assertEqual(values[1]["organizations"][0]["organization"], "Music")
        # Invalid argument: exit 2, empty stdout, JSON error on stderr, no data change.
        payload.write_text(json.dumps({"organization": 7}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "funnel-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(self.app.funnel_report()["total"]["opportunities"], 4)

    def test_set_reminder_persists_replaces_and_keeps_note_inner_space(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        result = self.app.set_reminder(" A ", " 2026-11-05 ", "  call  back ")
        self.assertEqual(result, {"contact_id": "A", "due_on": "2026-11-05", "note": "call  back"})
        self.assertEqual(set(result), {"contact_id", "due_on", "note"})
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.due_reminders("2026-12-31"),
                         [{"contact_id": "A", "due_on": "2026-11-05", "note": "call  back"}])
        # Setting again replaces the whole reminder; at most one per contact.
        self.app.set_reminder("A", "2027-01-01", "New note")
        self.assertEqual(ContactFlow(self.root).due_reminders("2027-06-30"),
                         [{"contact_id": "A", "due_on": "2027-01-01", "note": "New note"}])

    def test_set_reminder_accepts_past_dates_and_leap_days(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        for day in ["2000-01-01", "2024-02-29", "1999-12-31"]:
            self.assertEqual(self.app.set_reminder("A", day, "n")["due_on"], day)

    def test_clear_reminder_returns_whether_present_and_skips_write_when_absent(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        # Existing contacts without reminders: false and no write.
        self.assertFalse(self.app.clear_reminder(" A "))
        self.assertFalse(self.app.clear_reminder("B"))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Present reminder: true, removed and persisted; clearing again is false.
        self.app.set_reminder("A", "2026-11-05", "note")
        self.assertTrue(self.app.clear_reminder("A"))
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"), [])
        after_clear = self.app.path.read_bytes()
        self.assertFalse(ContactFlow(self.root).clear_reminder("A"))
        self.assertEqual(self.app.path.read_bytes(), after_clear)

    def test_due_reminders_includes_today_and_overdue_sorted_by_day_then_id(self):
        for cid, name in [("b", "Bob"), ("A", "Alice"), ("东", "Dong")]:
            self.app.add_contact(cid, name, name.lower() + "@example.test", "Books")
        self.app.set_reminder("b", "2026-10-02", "same day b")
        self.app.set_reminder("东", "2026-10-01", "overdue dong")
        self.app.set_reminder("A", "2026-10-02", "same day A")
        self.app.set_reminder("b", "2026-10-03", "future replacement")
        due = self.app.due_reminders(" 2026-10-02 ")
        # Overdue first; same day ordered by contact id Unicode code point: A(U+0041) < b(U+0062).
        self.assertEqual([(r["contact_id"], r["due_on"]) for r in due],
                         [("东", "2026-10-01"), ("A", "2026-10-02")])
        self.assertEqual(self.app.due_reminders("2026-09-30"), [])
        self.assertEqual(self.app.due_reminders("2099-01-01")[2]["contact_id"], "b")

    def test_reminder_validation_rejects_without_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-11-05", "keep")
        before = self.app.path.read_bytes()
        bad_dates = ["2026-02-30", "2023-02-29", "2024-2-9", "2024/02-09", "26-02-09",
                     "20260209", "2026-12-31x", "", "  ", None, 5, ["2026-01-01"]]
        for bad in bad_dates:
            with self.assertRaises(ValueError):
                self.app.set_reminder("A", bad, "note")
            with self.assertRaises(ValueError):
                self.app.due_reminders(bad)
        for bad_id in ["", "  ", None, 7]:
            with self.assertRaises(ValueError):
                self.app.set_reminder(bad_id, "2026-11-05", "note")
            with self.assertRaises(ValueError):
                self.app.clear_reminder(bad_id)
        for bad_note in ["", "  ", None, 9]:
            with self.assertRaises(ValueError):
                self.app.set_reminder("A", "2026-11-05", bad_note)
        for cid in ["ZZZ", "a"]:  # unknown; ids are case-sensitive
            with self.assertRaises(ValueError):
                self.app.set_reminder(cid, "2026-11-05", "note")
            with self.assertRaises(ValueError):
                self.app.clear_reminder(cid)
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.due_reminders("2099-01-01"),
                         [{"contact_id": "A", "due_on": "2026-11-05", "note": "keep"}])

    def test_reminders_legacy_empty_and_query_creates_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.due_reminders("2026-10-02"), [])
        self.assertFalse(fresh_root.exists())
        with self.assertRaises(ValueError):
            fresh.set_reminder("A", "2026-10-02", "note")
        with self.assertRaises(ValueError):
            fresh.clear_reminder("A")
        self.assertFalse(fresh_root.exists())

    def test_follow_up_does_not_clear_reminder(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-02", "call")
        self.app.follow_up("A", "2026-10-01", "done")
        self.assertEqual(self.app.due_reminders("2026-10-02")[0]["note"], "call")

    def test_merge_reminder_rules(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")

        def reminders():
            return {r["contact_id"]: r for r in self.app.due_reminders("2099-01-01")}

        # Only the source has one: it moves to the target.
        self.app.set_reminder("B", "2026-11-05", "source note")
        self.app.merge_contacts("B", "A")
        self.assertEqual(reminders(), {"A": {"contact_id": "A", "due_on": "2026-11-05", "note": "source note"}})

        # Only the target has one across another merge: it is kept.
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        self.app.set_reminder("A", "2026-11-10", "target note")
        self.app.merge_contacts("C", "A")
        self.assertEqual(reminders(), {"A": {"contact_id": "A", "due_on": "2026-11-10", "note": "target note"}})

        # Both have reminders, source earlier: source date and note win, id rewritten.
        self.app.add_contact("D", "Dan", "d@example.test", "Music")
        self.app.set_reminder("D", "2026-11-01", "source earlier")
        self.app.merge_contacts("D", "A")
        self.assertEqual(reminders(), {"A": {"contact_id": "A", "due_on": "2026-11-01", "note": "source earlier"}})

        # Both have reminders, target earlier: target kept.
        self.app.set_reminder("A", "2026-12-01", "target earlier")
        self.app.add_contact("E", "Eve", "e@example.test", "Games")
        self.app.set_reminder("E", "2026-12-20", "source later")
        self.app.merge_contacts("E", "A")
        self.assertEqual(reminders(), {"A": {"contact_id": "A", "due_on": "2026-12-01", "note": "target earlier"}})

        # Same due date: target's reminder (and note) is kept.
        self.app.set_reminder("A", "2027-01-01", "target same day")
        self.app.add_contact("F", "Finn", "f@example.test", "Games")
        self.app.set_reminder("F", "2027-01-01", "source same day")
        self.app.follow_up("F", "2027-01-02", "still recorded")
        result = self.app.merge_contacts("F", "A")
        self.assertEqual(reminders(), {"A": {"contact_id": "A", "due_on": "2027-01-01", "note": "target same day"}})
        # Merge return structure and follow-up count are unchanged.
        self.assertEqual(set(result), {"contact", "moved_followups"})
        self.assertEqual(result["moved_followups"], 1)
        self.assertEqual(result["contact"]["contact_id"], "A")

    def test_merge_without_reminders_leaves_no_reminder_key(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        result = self.app.merge_contacts("B", "A")
        self.assertEqual(set(result), {"contact", "moved_followups"})
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"), [])
        self.assertNotIn("reminders", json.loads(self.app.path.read_text(encoding="utf-8")))

    def test_cli_reminders_success_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        payload = self.root / "reminder.json"
        payload.write_text(json.dumps({"contact_id": " A ", "due_on": " 2026-10-02 ", "note": " call "}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), {"contact_id": "A", "due_on": "2026-10-02", "note": "call"})
        payload.write_text(json.dumps({"contact_id": "B", "due_on": "2026-10-01", "note": "n"}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        payload.write_text(json.dumps({"as_of": "2026-10-02"}), encoding="utf-8")
        due = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "due-reminders", str(payload)], text=True, capture_output=True)
        self.assertEqual(due.returncode, 0, due.stderr)
        self.assertEqual([r["contact_id"] for r in json.loads(due.stdout)], ["B", "A"])
        payload.write_text(json.dumps({"contact_id": "A"}), encoding="utf-8")
        cleared = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "clear-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIs(json.loads(cleared.stdout), True)
        # Invalid date: exit 2, empty stdout, JSON error on stderr, no data change.
        payload.write_text(json.dumps({"contact_id": "B", "due_on": "2026-02-30", "note": "x"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01")[0]["contact_id"], "B")
        # Array executes in order; a later failure keeps earlier successes.
        payload.write_text(json.dumps([
            {"contact_id": "A", "due_on": "2026-09-30", "note": "first"},
            {"contact_id": "ZZZ", "due_on": "2026-09-30", "note": "bad"},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "set-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01")[0]["contact_id"], "A")
        # Array of clear calls: present reminders return true in order; both removed.
        payload.write_text(json.dumps([{"contact_id": "A"}, {"contact_id": "B"}]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "clear-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertEqual(json.loads(batch.stdout), [True, True])
        payload.write_text(json.dumps({"as_of": "2099-01-01"}), encoding="utf-8")
        due_after = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "due-reminders", str(payload)], text=True, capture_output=True)
        self.assertEqual(json.loads(due_after.stdout), [])

if __name__ == "__main__":
    unittest.main()
