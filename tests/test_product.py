import csv
import io
import json
import os
from datetime import date
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

    def test_find_tag_expression_semantics(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东", "暂停"])
        self.app.set_tags("B", ["vip", "华南"])
        self.app.set_tags("C", ["vip", "华东"])
        # D has no tags at all.
        expression = '"vip" && ("华东" || "华南") && !"暂停"'
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression=expression)], ["B", "C"])
        # Precedence: ! binds tightest, then &&, then ||.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='"华东" || "华南" && !"vip"')], ["A", "C"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='!"华东" && !"华南"')], ["D"])
        # Consecutive negations and nested parentheses.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='!!"vip"')], ["A", "B", "C"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='!(("华东" || "华南") && !"vip")')],
                         ["A", "B", "C", "D"])
        # Unknown tags count as absent, so their negation is true; a pure
        # exclusion expression selects contacts without any tags.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='!"missing"')], ["A", "B", "C", "D"])
        self.assertEqual(self.app.find(tag_expression='"missing"'), [])
        # Tags normalize like set-tags: trimmed and casefolded.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression=' " VIP " ')], ["A", "B", "C"])
        # JSON escapes decode before normalization; Unicode whitespace between
        # tokens is ignored.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='"\\u0076ip"\u3000&&\t"华东"')], ["A", "C"])
        # Operators and parentheses inside quotes are tag content.
        self.app.set_tags("D", ["a&&b", "x(y)"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='"a&&b" && "x(y)"')], ["D"])
        # The expression intersects with organization and tags/tag_mode.
        self.assertEqual([c["contact_id"] for c in self.app.find(organization="music", tag_expression='"vip"')], ["C"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tags=["华东"], tag_expression='"vip"')], ["A", "C"])
        self.assertEqual([c["contact_id"] for c in self.app.find(tags=["华东"], tag_expression='!"暂停"')], ["C"])
        # Results stay sorted by contact id with each contact at most once.
        self.assertEqual([c["contact_id"] for c in self.app.find(tag_expression='"vip" || "华东"')], ["A", "B", "C"])
        # Omitting the parameter or passing None keeps the legacy behavior.
        self.assertEqual(self.app.find(tag_expression=None), self.app.find())

    def test_find_tag_expression_validation(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        bad = [
            "", "   ", '"unterminated', '"bad\\xescape"', '"a" "b"', '"a" &&',
            '&& "a"', "!", "()", "(", ")", '("a"', '"a")', "vip", '"a" & "b"',
            '"a" | "b"', '"a" ? "b"', '("a"))', '" "', '""', '"a" && ()',
        ]
        for expression in bad:
            with self.assertRaises(ValueError, msg=expression):
                self.app.find(tag_expression=expression)
        for not_string in [5, True, ["vip"], {"tag": "vip"}]:
            with self.assertRaises(ValueError):
                self.app.find(tag_expression=not_string)
        # The whole expression validates even when nothing could match: an
        # unknown organization or an empty store never hides a malformed tail.
        with self.assertRaises(ValueError):
            self.app.find(organization="nobody", tag_expression='"vip" &&')
        empty = ContactFlow(Path(self.temp.name) / "empty")
        with self.assertRaises(ValueError):
            empty.find(tag_expression='"vip" &&')
        self.assertFalse(empty.path.exists())
        # A failed query never creates the directory or rewrites data.
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.find(tag_expression='"a" "b"')
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_find_tag_expression(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        payload = self.root / "find.json"
        payload.write_text(json.dumps({"tag_expression": '"vip" && !"华东"'}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "find", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([c["contact_id"] for c in json.loads(ok.stdout)], ["B"])
        # Each item of an outer array is an independent query.
        payload.write_text(json.dumps([{"tag_expression": '"vip"'}, {"tag_expression": '"华东"'}]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "find", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertEqual([[c["contact_id"] for c in row] for row in json.loads(batch.stdout)], [["A", "B"], ["A"]])
        # A malformed expression reports the error envelope on stderr, keeps
        # stdout empty, exits 2 and never creates a data directory.
        fresh = Path(self.temp.name) / "fresh"
        payload.write_text(json.dumps({"tag_expression": '"vip" &&'}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(fresh), "find", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertFalse((fresh / "data.json").exists())

    def test_duplicate_candidates_pairs_and_ordering(self):
        self.app.add_contact("A", "陈小明", "a@example.test", "Books")
        self.app.add_contact("B", "陈晓明", "b@example.test", "Books")
        self.app.add_contact("C", "陈晓鸣", "c@example.test", "Books")
        self.app.add_contact("D", "陈 晓明", "d@example.test", "books")
        pairs = self.app.duplicate_candidates()
        self.assertEqual([(p["distance"], p["left"]["contact_id"], p["right"]["contact_id"]) for p in pairs],
                         [(0, "B", "D"), (1, "A", "B"), (1, "A", "D"), (1, "B", "C"), (1, "C", "D")])
        # No transitive completion: 陈小明 vs 陈晓鸣 needs two operations.
        self.assertNotIn(("A", "C"), [(p["left"]["contact_id"], p["right"]["contact_id"]) for p in pairs])
        first = pairs[0]
        self.assertEqual(set(first), {"left", "right", "distance"})
        self.assertEqual(first["left"], {"contact_id": "B", "name": "陈晓明", "email": "b@example.test", "organization": "Books"})
        # Originals keep their stored values; only the comparison normalizes.
        self.assertEqual(self.app.find()[3]["name"], "陈 晓明")

    def test_duplicate_candidates_requires_same_organization_and_one_edit(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Alicia", "b@example.test", "Music")
        self.app.add_contact("C", "Alic", "c@example.test", "Books")
        self.app.add_contact("D", "Laice", "d@example.test", "Books")
        self.assertEqual(self.app.duplicate_candidates(), [
            {"left": self.app.find()[0], "right": self.app.find()[2], "distance": 1}])
        # Adjacent transposition (Alice/Laice) is two operations, never a match.
        self.assertNotIn("D", [p["left"]["contact_id"] for p in self.app.duplicate_candidates()])

    def test_duplicate_candidates_filters_like_find(self):
        self.app.add_contact("A", "陈小明", "a@example.test", "Books")
        self.app.add_contact("B", "陈晓明", "b@example.test", "Books")
        self.app.add_contact("C", "陈晓鸣", "c@example.test", "Music")
        self.assertEqual([(p["left"]["contact_id"], p["right"]["contact_id"]) for p in self.app.duplicate_candidates()],
                         [("A", "B")])
        self.assertEqual(self.app.duplicate_candidates(organization="music"), [])
        self.app.set_tags("A", ["vip"])
        self.assertEqual(self.app.duplicate_candidates(tags=["vip"]), [])
        self.app.set_tags("B", ["vip"])
        self.assertEqual([(p["left"]["contact_id"], p["right"]["contact_id"])
                          for p in self.app.duplicate_candidates(tags=["VIP"])], [("A", "B")])

    def test_duplicate_candidates_empty_and_readonly(self):
        self.assertEqual(self.app.duplicate_candidates(), [])
        self.assertFalse(self.app.path.exists())
        self.app.add_contact("A", "陈小明", "a@example.test", "Books")
        self.assertEqual(self.app.duplicate_candidates(), [])
        self.app.add_contact("B", "陈晓明", "b@example.test", "Books")
        before = self.app.path.read_bytes()
        self.assertEqual(len(self.app.duplicate_candidates()), 1)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_duplicate_candidates_validation_and_merge(self):
        for kwargs in [{"organization": 5}, {"organization": ["Books"]}, {"tags": "vip"},
                       {"tags": ["ok", 1]}, {"tag_mode": "weird"}, {"tag_mode": "ALL"}]:
            with self.assertRaises(ValueError):
                self.app.duplicate_candidates(**kwargs)
        # The same validation runs against an empty store.
        self.assertFalse(self.app.path.exists())
        self.app.add_contact("A", "陈小明", "a@example.test", "Books")
        self.app.add_contact("B", "陈晓明", "b@example.test", "Books")
        self.app.add_contact("C", "陈晓鸣", "c@example.test", "Books")
        self.app.merge_contacts("C", "B")
        pairs = ContactFlow(self.root).duplicate_candidates()
        self.assertEqual([(p["left"]["contact_id"], p["right"]["contact_id"]) for p in pairs], [("A", "B")])

    def test_cli_duplicate_candidates(self):
        self.app.add_contact("A", "陈小明", "a@example.test", "Books")
        self.app.add_contact("B", "陈晓明", "b@example.test", "Books")
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"organization": "books"}), encoding="utf-8")
        found = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "duplicate-candidates", str(payload)], text=True, capture_output=True)
        self.assertEqual(found.returncode, 0, found.stderr)
        result = json.loads(found.stdout)
        self.assertEqual([(p["distance"], p["left"]["contact_id"], p["right"]["contact_id"]) for p in result],
                         [(1, "A", "B")])
        payload.write_text(json.dumps({"tag_mode": "weird"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "duplicate-candidates", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        empty = self.root / "empty"
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "duplicate-candidates"], text=True, capture_output=True)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty.exists())

    def test_search_contacts_normalization_exact_and_distances(self):
        self.app.add_contact("A", "Alice Chen", "a@example.test", "Books")
        result = self.app.search_contacts("lice")
        self.assertEqual(set(result), {"total", "matches"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(set(result["matches"][0]), {"contact", "distance"})
        self.assertEqual(result["matches"][0]["distance"], 0)
        # casefold and full whitespace stripping on both sides
        self.assertEqual(self.app.search_contacts(" LICE ")["matches"][0]["distance"], 0)
        # NFKC full-width letters and an ideographic space normalize away
        self.app.add_contact("B", "Ａlice 　Chen", "b@example.test", "Books")
        result = self.app.search_contacts("alicechen")
        self.assertEqual([m["contact"]["contact_id"] for m in result["matches"]], ["A", "B"])
        self.assertEqual(result["matches"][1]["contact"]["name"], "Ａlice 　Chen")
        # insertion against the best fragment is one code-point edit
        self.app.add_contact("C", "Jo", "c@example.test", "Books")
        self.assertEqual(self.app.search_contacts("joe", max_distance=1)["matches"][0]["distance"], 1)
        # exact-only threshold
        self.assertEqual(self.app.search_contacts("joe", max_distance=0)["total"], 0)
        self.assertEqual(self.app.search_contacts("jo", max_distance=0)["total"], 1)
        # a two-edit fragment (zqf vs the "def" slice: two substitutions) is only
        # reachable at max_distance 2; the other names share no z/q/f code point.
        self.app.add_contact("D", "abcdef", "d@example.test", "Books")
        self.assertEqual(self.app.search_contacts("zqf"), {"total": 0, "matches": []})
        result = self.app.search_contacts("zqf", max_distance=2)
        self.assertEqual([(m["contact"]["contact_id"], m["distance"]) for m in result["matches"]],
                         [("D", 2)])

    def test_search_contacts_ordering_pagination_and_defaults(self):
        self.app.add_contact("m3", "xabex", "m3@example.test", "Books")
        self.app.add_contact("a2", "abd", "a2@example.test", "Books")
        self.app.add_contact("z1", "abc", "z1@example.test", "Books")
        result = self.app.search_contacts("abe")
        self.assertEqual([(m["contact"]["contact_id"], m["distance"]) for m in result["matches"]],
                         [("m3", 0), ("a2", 1), ("z1", 1)])
        self.assertEqual(result["total"], 3)
        page = self.app.search_contacts("abe", limit=2)
        self.assertEqual(page["total"], 3)
        self.assertEqual([m["contact"]["contact_id"] for m in page["matches"]], ["m3", "a2"])
        page = self.app.search_contacts("abe", limit=2, offset=2)
        self.assertEqual(page["total"], 3)
        self.assertEqual([m["contact"]["contact_id"] for m in page["matches"]], ["z1"])
        page = self.app.search_contacts("abe", offset=3)
        self.assertEqual(page, {"total": 3, "matches": []})
        for index in range(25):
            self.app.add_contact("p%02d" % index, "person %d" % index,
                                 "p%02d@example.test" % index, "Music")
        result = self.app.search_contacts("person")
        self.assertEqual(result["total"], 25)
        self.assertEqual(len(result["matches"]), 20)
        self.assertEqual(result["matches"][0]["contact"]["contact_id"], "p00")
        self.assertEqual(len(self.app.search_contacts("person", limit=20, offset=20)["matches"]), 5)

    def test_search_contacts_filters_intersect_with_name(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Alicia", "b@example.test", "Music")
        self.app.add_contact("C", "Alice", "c@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        self.app.set_tags("C", ["north"])
        self.assertEqual([m["contact"]["contact_id"] for m in
                          self.app.search_contacts("ali", organization="books")["matches"]],
                         ["A", "C"])
        self.assertEqual([m["contact"]["contact_id"] for m in
                          self.app.search_contacts("ali", tags=["vip"])["matches"]], ["A"])
        self.assertEqual([m["contact"]["contact_id"] for m in
                          self.app.search_contacts("ali", tags=["vip", "north"], tag_mode="any")["matches"]],
                         ["A", "C"])
        self.assertEqual(self.app.search_contacts("ali", tags=["missing"]),
                         {"total": 0, "matches": []})
        # name condition intersects the filters
        self.assertEqual(self.app.search_contacts("bob", organization="books"),
                         {"total": 0, "matches": []})

    def test_search_contacts_validation_empty_store_and_typeerror(self):
        self.assertFalse(self.app.path.exists())
        for bad_query in [5, None, True, [], "", "   ", "　", "\t\n"]:
            with self.assertRaises(ValueError):
                self.app.search_contacts(bad_query)
        for bad_distance in [3, -1, "1", 1.0, True, False, None]:
            with self.assertRaises(ValueError):
                self.app.search_contacts("ali", max_distance=bad_distance)
        for bad_limit in [0, -1, "20", 1.0, True, False, None]:
            with self.assertRaises(ValueError):
                self.app.search_contacts("ali", limit=bad_limit)
        for bad_offset in [-1, "0", 1.0, True, False, None]:
            with self.assertRaises(ValueError):
                self.app.search_contacts("ali", offset=bad_offset)
        for kwargs in [{"organization": 5}, {"organization": ["Books"]}, {"tags": "vip"},
                       {"tags": ["ok", 1]}, {"tag_mode": "weird"}, {"tag_mode": "ALL"}]:
            with self.assertRaises(ValueError):
                self.app.search_contacts("ali", **kwargs)
        with self.assertRaises(TypeError):
            self.app.search_contacts()
        # Valid parameters on an empty store validate fine and create nothing.
        self.assertEqual(self.app.search_contacts("ali"), {"total": 0, "matches": []})
        self.assertEqual(self.app.search_contacts("ali", max_distance=2, limit=1, offset=0),
                         {"total": 0, "matches": []})
        self.assertFalse(self.app.path.exists())

    def test_search_contacts_readonly_and_reflects_current_state(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Alicia", "b@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.search_contacts("alic")["total"], 2)
        self.assertEqual(self.app.path.read_bytes(), before)
        # Updating the name changes future results.
        ContactFlow(self.root).update_contact("A", {"name": "Brenda"})
        reopened = ContactFlow(self.root)
        self.assertEqual([m["contact"]["contact_id"] for m in reopened.search_contacts("alice")["matches"]],
                         ["B"])
        self.assertEqual([m["contact"]["contact_id"]
                          for m in reopened.search_contacts("ali", tags=["vip"])["matches"]], [])
        # Tag updates widen the filter again.
        ContactFlow(self.root).set_tags("B", ["vip"])
        self.assertEqual([m["contact"]["contact_id"]
                          for m in ContactFlow(self.root).search_contacts("ali", tags=["VIP"])["matches"]],
                         ["B"])
        # Imported contacts become searchable; merged sources disappear.
        csv_path = self.root / "people.csv"
        csv_path.write_text("contact_id,name,email,organization\nC,Aline,c@example.test,Books\n",
                            encoding="utf-8")
        ContactFlow(self.root).import_contacts(str(csv_path))
        self.assertEqual([m["contact"]["contact_id"]
                          for m in ContactFlow(self.root).search_contacts("aline")["matches"]], ["C"])
        ContactFlow(self.root).merge_contacts("C", "B")
        self.assertEqual(ContactFlow(self.root).search_contacts("aline"),
                         {"total": 0, "matches": []})

    def test_cli_search_contacts(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Alicia", "b@example.test", "Music")
        payload = self.root / "query.json"
        payload.write_text(json.dumps({"query": "alice", "max_distance": 0, "limit": 5, "offset": 0}),
                           encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "search-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
                         {"total": 1, "matches": [
                             {"contact": {"contact_id": "A", "name": "Alice",
                                          "email": "a@example.test", "organization": "Books"},
                              "distance": 0}]})
        payload.write_text(json.dumps({"query": 5}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "search-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        # Missing the required query is a TypeError, still wrapped as exit 2.
        payload.write_text(json.dumps({"max_distance": 1}), encoding="utf-8")
        missing = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "search-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # Arrays run one search per object.
        payload.write_text(json.dumps([{"query": "ali"}, {"query": "zzz"}]), encoding="utf-8")
        array = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "search-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(array.returncode, 0, array.stderr)
        results = json.loads(array.stdout)
        self.assertEqual([row["total"] for row in results], [2, 0])
        empty = self.root / "empty"
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "search-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout),
                         [{"total": 0, "matches": []}, {"total": 0, "matches": []}])
        self.assertFalse(empty.exists())

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

    def test_preview_clean_file_reports_rows_without_importing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        csv_path = self.write_csv("people.csv",
            "email,contact_id,name,organization\r\n"
            "bob@example.test,B,\"Bob, Jr.\",Music\r\n"
            "ALICE@example.test, A ,\"陈\n小明\",\" 春山书店 \"\r\n")
        result = fresh.preview_contact_import(str(csv_path))
        self.assertEqual(result, {
            "can_import": True,
            "records": [
                {"row": 1, "contact": {"contact_id": "B", "name": "Bob, Jr.", "email": "bob@example.test", "organization": "Music"}},
                {"row": 2, "contact": {"contact_id": "A", "name": "陈\n小明", "email": "alice@example.test", "organization": "春山书店"}},
            ],
            "errors": [],
        })
        # The preview registered nothing and never created the data directory.
        self.assertFalse(fresh_root.exists())
        # A later real import still validates independently and succeeds.
        imported = fresh.import_contacts(str(csv_path))
        self.assertEqual([c["contact_id"] for c in imported], ["B", "A"])

    def test_preview_reports_every_problem_and_keeps_clean_rows(self):
        self.app.add_contact("X", "Existing", "x@example.test", "Books")
        self.app.add_contact("Y", "Other", "y@example.test", "Games")
        before = self.app.path.read_bytes()
        csv_path = self.write_csv("mixed.csv",
            "contact_id,name,email,organization\n"
            "A,Alice,a@example.test,Org\n"          # row 1: clean
            "X,BadId,new@example.test,Org\n"        # row 2: existing + duplicated id
            ",NoId,no-id@example.test,Org\n"        # row 3: invalid id
            "B, ,b@example.test,Org\n"              # row 4: invalid name
            "C,Cara,not-an-email,Org\n"             # row 5: invalid email
            "D,Dee,Y@example.test,Org\n"            # row 6: existing email
            "E,Eve,dup@example.test,Org\n"          # row 7: duplicated email
            "F,Fay,DUP@example.test,Org\n"          # row 8: duplicated email
            "E,Eve2,e2@example.test,Org\n"          # row 9: duplicated id (rows 7+9)
            "X,Xagain,x@example.test,Org\n")        # row 10: duplicated + existing id, existing email
        result = self.app.preview_contact_import(str(csv_path))
        self.assertFalse(result["can_import"])
        self.assertEqual(result["records"], [
            {"row": 1, "contact": {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Org"}},
        ])
        self.assertEqual(result["errors"], [
            {"row": 2, "field": "contact_id", "code": "duplicate"},
            {"row": 2, "field": "contact_id", "code": "existing"},
            {"row": 3, "field": "contact_id", "code": "invalid"},
            {"row": 4, "field": "name", "code": "invalid"},
            {"row": 5, "field": "email", "code": "invalid"},
            {"row": 6, "field": "email", "code": "existing"},
            {"row": 7, "field": "contact_id", "code": "duplicate"},
            {"row": 7, "field": "email", "code": "duplicate"},
            {"row": 8, "field": "email", "code": "duplicate"},
            {"row": 9, "field": "contact_id", "code": "duplicate"},
            {"row": 10, "field": "contact_id", "code": "duplicate"},
            {"row": 10, "field": "contact_id", "code": "existing"},
            {"row": 10, "field": "email", "code": "existing"},
        ])
        # Nothing was imported or rewritten, and a repeated preview recomputes.
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.preview_contact_import(str(csv_path)), result)
        self.assertEqual([c["contact_id"] for c in ContactFlow(self.root).find()], ["X", "Y"])

    def test_preview_checks_legal_id_and_email_even_when_row_has_invalid_fields(self):
        self.app.add_contact("X", "Existing", "x@example.test", "Books")
        csv_path = self.write_csv("partial.csv",
            "contact_id,name,email,organization\n"
            "X, ,x@example.test, \n")
        result = self.app.preview_contact_import(str(csv_path))
        self.assertEqual(result["records"], [])
        self.assertEqual(result["errors"], [
            {"row": 1, "field": "contact_id", "code": "existing"},
            {"row": 1, "field": "email", "code": "existing"},
            {"row": 1, "field": "name", "code": "invalid"},
            {"row": 1, "field": "organization", "code": "invalid"},
        ])

    def test_preview_flags_all_rows_of_a_batch_duplicate_and_both_conflict_kinds(self):
        self.app.add_contact("A", "Existing", "a@example.test", "Books")
        csv_path = self.write_csv("dup.csv",
            "contact_id,name,email,organization\n"
            "A,One,a@example.test,Org\n"
            "B,Two,b@example.test,Org\n"
            "A,Three,a@example.test,Org\n"
            "B,Four,b2@example.test,Org\n")
        result = self.app.preview_contact_import(str(csv_path))
        self.assertEqual(result["records"], [])
        self.assertEqual(result["errors"], [
            {"row": 1, "field": "contact_id", "code": "duplicate"},
            {"row": 1, "field": "contact_id", "code": "existing"},
            {"row": 1, "field": "email", "code": "duplicate"},
            {"row": 1, "field": "email", "code": "existing"},
            {"row": 2, "field": "contact_id", "code": "duplicate"},
            {"row": 3, "field": "contact_id", "code": "duplicate"},
            {"row": 3, "field": "contact_id", "code": "existing"},
            {"row": 3, "field": "email", "code": "duplicate"},
            {"row": 3, "field": "email", "code": "existing"},
            {"row": 4, "field": "contact_id", "code": "duplicate"},
        ])

    def test_preview_row_numbering_skips_blank_lines_and_quoted_newlines(self):
        csv_path = self.write_csv("rows.csv",
            "contact_id,name,email,organization\n"
            "\n"
            "A,\"Al\nice\",a@example.test,Org\n"
            "\r\n"
            ",Bad,b@example.test,Org\n"
            "\n")
        result = self.app.preview_contact_import(str(csv_path))
        self.assertEqual([r["row"] for r in result["records"]], [1])
        self.assertEqual(result["records"][0]["contact"]["name"], "Al\nice")
        self.assertEqual(result["errors"], [{"row": 2, "field": "contact_id", "code": "invalid"}])

    def test_preview_header_only_is_importable_and_creates_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        for content in ["contact_id,name,email,organization\n", "contact_id,name,email,organization\n\n\r\n\n"]:
            csv_path = self.write_csv("header-%d.csv" % len(content), content)
            self.assertEqual(fresh.preview_contact_import(str(csv_path)),
                             {"can_import": True, "records": [], "errors": []})
            self.assertFalse(fresh_root.exists())

    def test_preview_treats_missing_contacts_collection_as_empty_store(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.app.path.write_text(json.dumps({"followups": []}), encoding="utf-8")
        before = self.app.path.read_bytes()
        csv_path = self.write_csv("people.csv",
            "contact_id,name,email,organization\nA,Alice,a@example.test,Org\n")
        result = self.app.preview_contact_import(str(csv_path))
        self.assertTrue(result["can_import"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_preview_rejects_file_level_problems_without_partial_report(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        bad_files = [
            "", "\n",                                            # empty file
            "contact_id,name,email\n",                           # bad header
            "contact_id,name,email,email\n",                     # duplicated header
            "contact_id,name,email,organization\nA,Alice,a@example.test\n",   # short record
            "contact_id,name,email,organization\nA,Alice,a@example.test,Org,x\n",  # long record
            'contact_id,name,email,organization\nA,"Alice,a@example.test,Org\n',   # unterminated quote
        ]
        for i, content in enumerate(bad_files):
            csv_path = self.write_csv("bad-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                fresh.preview_contact_import(str(csv_path))
        csv_path = self.write_csv("latin.csv", b"contact_id,name,email,organization\nA,\xff,a@example.test,Org\n")
        with self.assertRaises(ValueError):
            fresh.preview_contact_import(str(csv_path))
        self.assertFalse(fresh_root.exists())

    def test_preview_validates_csv_path_and_missing_argument(self):
        for bad in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.preview_contact_import(bad)
        with self.assertRaises(FileNotFoundError):
            self.app.preview_contact_import(str(self.root / "missing.csv"))
        with self.assertRaises(TypeError):
            self.app.preview_contact_import()
        self.assertFalse(self.app.path.exists())

    def test_preview_unreadable_file_raises_permission_error(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        csv_path = self.write_csv("locked.csv", "contact_id,name,email,organization\nA,Alice,a@example.test,Books\n")
        csv_path.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                self.app.preview_contact_import(str(csv_path))
        finally:
            csv_path.chmod(0o644)
        self.assertFalse(self.app.path.exists())

    def test_preview_recomputes_against_current_data(self):
        csv_path = self.write_csv("people.csv",
            "contact_id,name,email,organization\nA,Alice,a@example.test,Org\n")
        first = self.app.preview_contact_import(str(csv_path))
        self.assertTrue(first["can_import"])
        self.app.add_contact("A", "Alice", "a@example.test", "Org")
        second = self.app.preview_contact_import(str(csv_path))
        self.assertFalse(second["can_import"])
        self.assertEqual(second["records"], [])
        self.assertEqual(second["errors"], [
            {"row": 1, "field": "contact_id", "code": "existing"},
            {"row": 1, "field": "email", "code": "existing"},
        ])

    def test_cli_preview_contact_import_success_and_failure(self):
        csv_path = self.write_csv("people.csv",
            "contact_id,name,email,organization\n"
            "A,Alice,a@example.test,Books\n"
            ",Bad,b@example.test,Music\n")
        payload = self.root / "preview.json"
        payload.write_text(json.dumps({"csv_path": str(csv_path)}), encoding="utf-8")
        # Row-level problems are still a successful report: JSON on stdout, exit 0.
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "preview-contact-import", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertFalse(report["can_import"])
        self.assertEqual([r["row"] for r in report["records"]], [1])
        self.assertEqual(report["errors"], [{"row": 2, "field": "contact_id", "code": "invalid"}])
        self.assertFalse(self.app.path.exists())
        # File-level problems use the standard error JSON and exit 2.
        bad_csv = self.write_csv("bad.csv", "contact_id,name,email\nA,Alice,a@example.test\n")
        payload.write_text(json.dumps({"csv_path": str(bad_csv)}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "preview-contact-import", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        # An outer JSON array calls each item independently and reports them all.
        payload.write_text(json.dumps([{"csv_path": str(csv_path)}, {"csv_path": str(csv_path)}]), encoding="utf-8")
        mixed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root), "preview-contact-import", str(payload)], text=True, capture_output=True)
        self.assertEqual(mixed.returncode, 0, mixed.stderr)
        reports = json.loads(mixed.stdout)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])
        self.assertFalse(self.app.path.exists())

    def test_import_followups_returns_file_order_and_persists(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Games")
        self.app.follow_up("A", "2026-10-02", "old same day")
        csv_path = self.write_csv("followups.csv",
            "note,contact_id,on\r\n"
            "\"line1\nline2, end\", A , 2026-10-02 \r\n"
            "leap day, 陈 ,2024-02-29\r\n"
            "future,B,2099-01-01\r\n"
            "leap day, 陈 ,2024-02-29\r\n")  # in-batch duplicate kept verbatim
        result = self.app.import_followups(str(csv_path))
        self.assertEqual(result, [
            {"contact_id": "A", "on": "2026-10-02", "note": "line1\nline2, end"},
            {"contact_id": "陈", "on": "2024-02-29", "note": "leap day"},
            {"contact_id": "B", "on": "2099-01-01", "note": "future"},
            {"contact_id": "陈", "on": "2024-02-29", "note": "leap day"},
        ])
        for entry in result:
            self.assertEqual(set(entry), {"contact_id", "on", "note"})
        # Same day, same contact: the pre-existing entry sorts before the imported batch.
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")],
                         [("2026-10-02", "old same day"), ("2026-10-02", "line1\nline2, end")])
        self.assertEqual([r["note"] for r in reopened.timeline("陈")], ["leap day", "leap day"])
        report = reopened.followup_report("2024-01-01", "2100-01-01")
        self.assertEqual([(r["contact_id"], r["on"], r["note"]) for r in report["records"]],
                         [("陈", "2024-02-29", "leap day"), ("陈", "2024-02-29", "leap day"),
                          ("A", "2026-10-02", "old same day"),
                          ("A", "2026-10-02", "line1\nline2, end"),
                          ("B", "2099-01-01", "future")])

    def test_import_followups_bom_header_only_and_empty(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        csv_path = self.write_csv("followups.csv",
            "﻿on,note,contact_id\n 2024-02-29 , ok ,A\n")
        self.assertEqual(self.app.import_followups(str(csv_path)),
                         [{"contact_id": "A", "on": "2024-02-29", "note": "ok"}])
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        for content in ["", "﻿", "\n", "\r\n"]:
            path = self.write_csv("empty-%d.csv" % len(content.encode("utf-8")), content)
            with self.assertRaises(ValueError):
                fresh.import_followups(str(path))
        self.assertFalse(fresh_root.exists())
        for content in ["contact_id,on,note\n", "contact_id,on,note\n\n\r\n\n"]:
            path = self.write_csv("header-%d.csv" % len(content), content)
            self.assertEqual(fresh.import_followups(str(path)), [])
        self.assertFalse(fresh_root.exists())

    def test_import_followups_rejects_bad_headers(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        bad_headers = [
            "contact_id,note\n",
            "contact_id,on,note,extra\n",
            "contact_id,on,on\n",
            "Contact_ID,on,note\n",
            "contact_id,On,note\n",
            "contact_id,on,note,x\nA,2026-10-01,x,extra\n",
        ]
        for content in bad_headers:
            path = self.write_csv("bad-%d.csv" % len(content), content)
            with self.assertRaises(ValueError):
                fresh.import_followups(str(path))
        self.assertFalse(fresh_root.exists())

    def test_import_followups_rejects_bad_records_atomically(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        bad_files = [
            "contact_id,on,note\nA,2026-10-01\n",                # too few fields
            "contact_id,on,note\nA,2026-10-01,x,extra\n",       # too many fields
            "contact_id,on,note\n,2026-10-01,x\n",              # blank id
            "contact_id,on,note\n   ,2026-10-01,x\n",
            "contact_id,on,note\nA,2026-10-01,  \n",            # blank note
            "contact_id,on,note\nA,,x\n",                       # blank date
            "contact_id,on,note\nA,  ,x\n",
            "contact_id,on,note\nA,2026-02-30,x\n",             # impossible date
            "contact_id,on,note\nA,2026-1-1,x\n",
            "contact_id,on,note\nA,20261001,x\n",
            "contact_id,on,note\n,,x\n",                        # row of empties is not a blank line
            'contact_id,on,note\nA,"2026-10-01,x\n',            # unterminated quoted field
            "contact_id,on,note\nZZZ,2026-10-01,x\n",           # unknown contact
            "contact_id,on,note\nA,2026-10-01,ok\nZ,2026-10-01,x\n",  # later row unknown
        ]
        for i, content in enumerate(bad_files):
            path = self.write_csv("bad-record-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                self.app.import_followups(str(path))
            self.assertEqual(self.app.path.read_bytes(), before)
        # Ids are case-sensitive: "a" does not match contact "A".
        path = self.write_csv("case.csv", "contact_id,on,note\na,2026-10-01,x\n")
        with self.assertRaises(ValueError):
            self.app.import_followups(str(path))
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_import_followups_rejects_invalid_utf8(self):
        path = self.write_csv("latin.csv", b"contact_id,on,note\nA,2026-10-01,\xff\n")
        with self.assertRaises(ValueError):
            self.app.import_followups(str(path))
        self.assertFalse(self.app.path.exists())

    def test_import_followups_keeps_duplicates_and_reimport_appends(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.follow_up("A", "2026-10-01", "same")
        path = self.write_csv("followups.csv", "contact_id,on,note\n A ,2026-10-01, same \n")
        first = self.app.import_followups(str(path))
        second = self.app.import_followups(str(path))
        self.assertEqual(first, second)
        # Existing copy, then batch one, then re-imported batch one, all kept.
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("A")],
                         ["same", "same", "same"])

    def test_import_followups_validates_path_and_permissions(self):
        for bad in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.import_followups(bad)
        with self.assertRaises(FileNotFoundError):
            self.app.import_followups(str(self.root / "missing.csv"))
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        path = self.write_csv("locked.csv", "contact_id,on,note\nA,2026-10-01,x\n")
        path.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                self.app.import_followups(str(path))
        finally:
            path.chmod(0o644)

    def test_import_followups_relative_path_and_legacy_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.write_csv("people.csv", "contact_id,on,note\nA,2026-10-01,x\n")
        old_cwd = os.getcwd()
        os.chdir(self.root)
        try:
            self.assertEqual(self.app.import_followups("people.csv")[0]["contact_id"], "A")
        finally:
            os.chdir(old_cwd)
        # Legacy data without a followups collection imports as if it were empty.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        path = self.write_csv("legacy.csv", "contact_id,on,note\nL,2026-10-01,note\n")
        legacy = ContactFlow(legacy_root)
        self.assertEqual(legacy.import_followups(str(path)),
                         [{"contact_id": "L", "on": "2026-10-01", "note": "note"}])
        self.assertEqual(ContactFlow(legacy_root).timeline("L")[0]["note"], "note")

    def test_import_followups_leaves_other_data_unchanged(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_reminder("A", "2099-01-01", "call")
        funnel_before = self.app.funnel_report()
        path = self.write_csv("followups.csv",
            "contact_id,on,note\nA,2026-10-03,x\nA,2026-10-04,y\n")
        self.app.import_followups(str(path))
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.find_opportunities(contact_id="A")[0]["title"], "Deal")
        self.assertEqual(reopened.due_reminders("2099-12-31"),
                         [{"contact_id": "A", "due_on": "2099-01-01", "note": "call"}])
        self.assertEqual(reopened.funnel_report(), funnel_before)
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["A", "B"])

    def test_cli_import_followups_success_failure_and_array(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("b", "Bob", "b@example.test", "Music")
        csv_path = self.write_csv("followups.csv",
            "contact_id,on,note\n A , 2026-10-01 , ok \nb,2024-02-29,leap\n")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"csv_path": str(csv_path)}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "import-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
                         [{"contact_id": "A", "on": "2026-10-01", "note": "ok"},
                          {"contact_id": "b", "on": "2024-02-29", "note": "leap"}])
        # Unknown contact: stderr JSON, exit 2, empty stdout, no write.
        before = self.app.path.read_bytes()
        bad_csv = self.write_csv("bad.csv", "contact_id,on,note\nZZZ,2026-10-01,x\n")
        payload.write_text(json.dumps({"csv_path": str(bad_csv)}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "import-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Array input: each import is independent and a later failure keeps earlier writes.
        other = self.write_csv("other.csv", "contact_id,on,note\nb,2026-10-05,y\n")
        payload.write_text(json.dumps([{"csv_path": str(other)}, {"csv_path": str(bad_csv)}]),
                           encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "import-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        json.loads(partial.stderr)
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("b")],
                         ["leap", "y"])

    def test_import_opportunities_returns_file_order_and_persists(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("b", "Bob", "b@example.test", "Music")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Games")
        self.app.add_opportunity("OLD", "A", "Existing")
        csv_path = self.write_csv("opportunities.csv",
            "﻿stage,title,contact_id,opportunity_id\r\n"
            "won,\"line1\nline2, end\", A , O1 \r\n"
            "lost,second,b,O2\n"
            "qualified,third,陈,O3\n"
            "\r\n\r\n")  # zero-field blank lines ignored
        result = self.app.import_opportunities(str(csv_path))
        self.assertEqual(result, [
            {"opportunity_id": "O1", "contact_id": "A", "title": "line1\nline2, end", "stage": "won"},
            {"opportunity_id": "O2", "contact_id": "b", "title": "second", "stage": "lost"},
            {"opportunity_id": "O3", "contact_id": "陈", "title": "third", "stage": "qualified"},
        ])
        for entry in result:
            self.assertEqual(set(entry), {"opportunity_id", "contact_id", "title", "stage"})
        # Historical stages are preserved as filled; multiple opportunities per contact allowed.
        reopened = ContactFlow(self.root)
        self.assertEqual([o["opportunity_id"] for o in reopened.find_opportunities()],
                         ["O1", "O2", "O3", "OLD"])
        self.assertEqual([(o["opportunity_id"], o["stage"])
                          for o in reopened.find_opportunities(contact_id="A")],
                         [("O1", "won"), ("OLD", "new")])
        report = reopened.funnel_report()
        self.assertEqual(report["total"],
                         {"contacts": 3, "new": 1, "qualified": 1, "won": 1, "lost": 1,
                          "opportunities": 4})

    def test_import_opportunities_header_only_and_empty(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        for content in ["", "﻿", "\n", "\r\n"]:
            path = self.write_csv("empty-%d.csv" % len(content.encode("utf-8")), content)
            with self.assertRaises(ValueError):
                fresh.import_opportunities(str(path))
        self.assertFalse(fresh_root.exists())
        for content in [
            "opportunity_id,contact_id,title,stage\n",
            "stage,opportunity_id,contact_id,title\n\n\r\n\n",
        ]:
            path = self.write_csv("header-%d.csv" % len(content), content)
            self.assertEqual(fresh.import_opportunities(str(path)), [])
        self.assertFalse(fresh_root.exists())

    def test_import_opportunities_rejects_bad_headers(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        bad_headers = [
            "opportunity_id,contact_id,title\n",
            "opportunity_id,contact_id,title,stage,extra\n",
            "opportunity_id,contact_id,title,title\n",
            "Opportunity_ID,contact_id,title,stage\n",
            "opportunity_id,contact_id,Title,stage\n",
            "opportunity_id,contact_id,title,stage,x\nO1,A,x,new,extra\n",
        ]
        for content in bad_headers:
            path = self.write_csv("bad-%d.csv" % len(content), content)
            with self.assertRaises(ValueError):
                fresh.import_opportunities(str(path))
        self.assertFalse(fresh_root.exists())

    def test_import_opportunities_rejects_bad_records_atomically(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_opportunity("EXISTING", "A", "Old")
        before = self.app.path.read_bytes()
        bad_files = [
            "opportunity_id,contact_id,title,stage\nO1,A,x,new,extra\n",   # too many fields
            "opportunity_id,contact_id,title,stage\nO1,A,x\n",             # too few fields
            "opportunity_id,contact_id,title,stage\n,A,x,new\n",           # blank opportunity id
            "opportunity_id,contact_id,title,stage\n  ,A,x,new\n",
            "opportunity_id,contact_id,title,stage\nO1,,x,new\n",          # blank contact id
            "opportunity_id,contact_id,title,stage\nO1, ,x,new\n",
            "opportunity_id,contact_id,title,stage\nO1,A,,new\n",          # blank title
            "opportunity_id,contact_id,title,stage\nO1,A,  ,new\n",
            "opportunity_id,contact_id,title,stage\nO1,A,x,\n",            # blank stage
            "opportunity_id,contact_id,title,stage\nO1,A,x,NEW\n",         # case not accepted
            "opportunity_id,contact_id,title,stage\nO1,A,x,pending\n",     # illegal stage
            "opportunity_id,contact_id,title,stage\n,,,\n",  # empty row is not a zero-field blank
            'opportunity_id,contact_id,title,stage\nO1,A,"x,new\n',        # unterminated quote
            "opportunity_id,contact_id,title,stage\nO9,ZZZ,x,new\n",        # unknown contact
            "opportunity_id,contact_id,title,stage\nO1,ZZZ,x,new\nO2,A,y,won\n",
            "opportunity_id,contact_id,title,stage\nEXISTING,A,x,new\n",   # existing opportunity
            "opportunity_id,contact_id,title,stage\nDUP,A,x,won\nDUP,A,x,won\n",  # in-batch dup
            "opportunity_id,contact_id,title,stage\nO1,a,x,new\n",        # case-sensitive contact
        ]
        for i, content in enumerate(bad_files):
            path = self.write_csv("bad-record-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                self.app.import_opportunities(str(path))
            self.assertEqual(self.app.path.read_bytes(), before)
        # Opportunity ids and contact ids share no namespace: a contact whose id
        # equals an existing opportunity id still works, and vice versa.
        path = self.write_csv("shared.csv",
            "opportunity_id,contact_id,title,stage\nA,A,shared id space,new\n")
        self.assertEqual(self.app.import_opportunities(str(path))[0]["opportunity_id"], "A")

    def test_import_opportunities_rejects_invalid_utf8(self):
        path = self.write_csv("latin.csv",
            b"opportunity_id,contact_id,title,stage\nO1,A,x,new\n\xff\n")
        with self.assertRaises(ValueError):
            self.app.import_opportunities(str(path))
        self.assertFalse(self.app.path.exists())

    def test_import_opportunities_validates_path_and_permissions(self):
        with self.assertRaises(TypeError):
            self.app.import_opportunities()
        for bad in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.import_opportunities(bad)
        with self.assertRaises(FileNotFoundError):
            self.app.import_opportunities(str(self.root / "missing.csv"))
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        path = self.write_csv("locked.csv",
            "opportunity_id,contact_id,title,stage\nO1,A,x,new\n")
        path.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                self.app.import_opportunities(str(path))
        finally:
            path.chmod(0o644)

    def test_import_opportunities_relative_path_and_legacy_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.write_csv("opps.csv", "opportunity_id,contact_id,title,stage\nO1,A,x,won\n")
        old_cwd = os.getcwd()
        os.chdir(self.root)
        try:
            self.assertEqual(self.app.import_opportunities("opps.csv")[0]["stage"], "won")
        finally:
            os.chdir(old_cwd)
        # Legacy data without an opportunities collection imports as if it were empty.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        path = self.write_csv("legacy.csv",
            "opportunity_id,contact_id,title,stage\nOL,L,deal,lost\n")
        legacy = ContactFlow(legacy_root)
        self.assertEqual(legacy.import_opportunities(str(path)),
                         [{"opportunity_id": "OL", "contact_id": "L", "title": "deal",
                           "stage": "lost"}])
        self.assertEqual(ContactFlow(legacy_root).find_opportunities()[0]["stage"], "lost")

    def test_import_opportunities_leaves_other_data_unchanged(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.follow_up("A", "2026-10-01", "called")
        self.app.set_reminder("A", "2099-01-01", "call")
        path = self.write_csv("opportunities.csv",
            "opportunity_id,contact_id,title,stage\nO1,A,x,won\nO2,B,y,qualified\n")
        self.app.import_opportunities(str(path))
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["called"])
        self.assertEqual(reopened.due_reminders("2099-12-31"),
                         [{"contact_id": "A", "due_on": "2099-01-01", "note": "call"}])
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["A", "B"])

    def test_cli_import_opportunities_success_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        csv_path = self.write_csv("opportunities.csv",
            "contact_id,opportunity_id,title,stage\n A , O1 , ok , won \n")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"csv_path": str(csv_path)}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "import-opportunities", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
                         [{"opportunity_id": "O1", "contact_id": "A", "title": "ok",
                           "stage": "won"}])
        # Reimporting the same file rejects the whole batch (duplicate id), exit 2, no write.
        before = self.app.path.read_bytes()
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "import-opportunities", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_import_contact_tags_appends_union_in_first_seen_order(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("b", "Bob", "b@example.test", "Music")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Games")
        self.app.set_tags("A", ["vip"])
        before = self.app.path.read_bytes()
        csv_path = self.write_csv("tags.csv",
            "tag,contact_id\r\n"
            " VIP , A \r\n"                       # trimmed, casefolded: already present
            '"VIP",A\r\n'                         # identical record duplicates freely
            "New Tag,A\r\n"                       # inner whitespace is kept
            "华东, 陈 \r\n"
            "vip,b\r\n"                           # b first appears here, after A
            "lead, A \r\n"
            "陈,陈\r\n")                          # tag equal to the contact id is fine
        result = self.app.import_contact_tags(str(csv_path))
        self.assertEqual(result, [
            {"contact_id": "A", "tags": ["lead", "new tag", "vip"]},
            {"contact_id": "陈", "tags": ["华东", "陈"]},
            {"contact_id": "b", "tags": ["vip"]},
        ])
        for row in result:
            self.assertEqual(set(row), {"contact_id", "tags"})
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["lead", "new tag", "vip"])
        self.assertEqual(reopened.get_tags("b"), ["vip"])
        self.assertEqual(reopened.get_tags("陈"), ["华东", "陈"])
        # Existing tags were preserved, not replaced; tag filters are case-insensitive.
        self.assertEqual([c["contact_id"] for c in reopened.find(tags=["VIP"])], ["A", "b"])
        self.assertEqual([c["contact_id"] for c in reopened.find(tags=["LEAD"])], ["A"])
        self.assertNotEqual(self.app.path.read_bytes(), before)

    def test_import_contact_tags_bom_reordered_header_and_tag_expressions(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        csv_path = self.write_csv("tags.csv",
            "﻿contact_id,tag\n A ,  Team A \n")
        self.assertEqual(self.app.import_contact_tags(str(csv_path)),
                         [{"contact_id": "A", "tags": ["team a"]}])
        # Inner whitespace survives casefold and is queryable through expressions.
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find(tag_expression='"TEAM A"')], ["A"])
        self.assertEqual([c["contact_id"] for c in reopened.find(tag_expression='!"other"')], ["A"])

    def test_import_contact_tags_header_only_creates_nothing(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        for content in ["", "﻿", "\n", "\r\n"]:
            path = self.write_csv("empty-%d.csv" % len(content.encode("utf-8")), content)
            with self.assertRaises(ValueError):
                fresh.import_contact_tags(str(path))
        self.assertFalse(fresh_root.exists())
        for content in ["contact_id,tag\n", "contact_id,tag\n\n\r\n\n"]:
            path = self.write_csv("header-%d.csv" % len(content), content)
            self.assertEqual(fresh.import_contact_tags(str(path)), [])
        # A legal header with no records never creates the data directory.
        self.assertFalse(fresh_root.exists())

    def test_import_contact_tags_rejects_bad_headers(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        bad_headers = [
            "contact_id\n",
            "contact_id,tag,extra\n",
            "contact_id,contact_id\n",
            "tag,tag\n",
            "Contact_ID,tag\n",
            "contact_id,Tag\n",
            "contact_id,tag,x\nA,y,extra\n",
        ]
        for content in bad_headers:
            path = self.write_csv("bad-%d.csv" % len(content), content)
            with self.assertRaises(ValueError):
                fresh.import_contact_tags(str(path))
        self.assertFalse(fresh_root.exists())

    def test_import_contact_tags_rejects_bad_records_atomically(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip"])
        before = self.app.path.read_bytes()
        bad_files = [
            "contact_id,tag\nA\n",                # too few fields
            "contact_id,tag\nA,x,extra\n",        # too many fields
            "contact_id,tag\n,x\n",               # blank id
            "contact_id,tag\n   ,x\n",
            "contact_id,tag\nA,\n",               # blank tag cannot clear data
            "contact_id,tag\nA,   \n",
            "contact_id,tag\n,\n",                # row of empties is not a blank line
            'contact_id,tag\nA,"x\n',             # unterminated quoted field
            "contact_id,tag\nZZZ,x\n",            # unknown contact
            "contact_id,tag\nA,ok\nZ,x\n",        # later row unknown
            "contact_id,tag\na,ok\n",             # ids are case-sensitive
        ]
        for i, content in enumerate(bad_files):
            path = self.write_csv("bad-record-%d.csv" % i, content)
            with self.assertRaises(ValueError):
                self.app.import_contact_tags(str(path))
            self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(ContactFlow(self.root).get_tags("A"), ["vip"])

    def test_import_contact_tags_rejects_invalid_utf8(self):
        path = self.write_csv("latin.csv", b"contact_id,tag\nA,\xff\n")
        with self.assertRaises(ValueError):
            self.app.import_contact_tags(str(path))
        self.assertFalse(self.app.path.exists())

    def test_import_contact_tags_all_existing_does_not_rewrite_and_reimports_equal(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "new tag"])
        before = self.app.path.read_bytes()
        csv_path = self.write_csv("tags.csv",
            "contact_id,tag\n A , VIP \nB,x\n A , new tag \n")
        # B gains "x"; afterwards reimporting the identical file changes nothing.
        first = self.app.import_contact_tags(str(csv_path))
        self.assertEqual(first, [
            {"contact_id": "A", "tags": ["new tag", "vip"]},
            {"contact_id": "B", "tags": ["x"]},
        ])
        after_first = self.app.path.read_bytes()
        self.assertNotEqual(after_first, before)
        second = self.app.import_contact_tags(str(csv_path))
        self.assertEqual(second, first)
        self.assertEqual(self.app.path.read_bytes(), after_first)
        # A wholly redundant file is a no-op even on a fresh contact set.
        redundant = self.write_csv("redundant.csv", "contact_id,tag\nA,new tag\nA,VIP\n")
        self.assertEqual(self.app.import_contact_tags(str(redundant)),
                         [{"contact_id": "A", "tags": ["new tag", "vip"]}])
        self.assertEqual(self.app.path.read_bytes(), after_first)

    def test_import_contact_tags_validates_path_and_permissions(self):
        for bad in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.import_contact_tags(bad)
        with self.assertRaises(FileNotFoundError):
            self.app.import_contact_tags(str(self.root / "missing.csv"))
        self.assertFalse(self.app.path.exists())
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses file permissions")
        path = self.write_csv("locked.csv", "contact_id,tag\nA,x\n")
        path.chmod(0o000)
        try:
            with self.assertRaises(PermissionError):
                self.app.import_contact_tags(str(path))
        finally:
            path.chmod(0o644)
        self.assertFalse(self.app.path.exists())

    def test_import_contact_tags_relative_path_legacy_and_other_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.write_csv("people.csv", "contact_id,tag\nA,ok\n")
        old_cwd = os.getcwd()
        os.chdir(self.root)
        try:
            self.assertEqual(self.app.import_contact_tags("people.csv")[0]["tags"], ["ok"])
        finally:
            os.chdir(old_cwd)
        # Legacy data without a tags collection treats tags as an empty set.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        path = self.write_csv("legacy.csv", "contact_id,tag\n L , Hi \n")
        legacy = ContactFlow(legacy_root)
        self.assertEqual(legacy.import_contact_tags(str(path)),
                         [{"contact_id": "L", "tags": ["hi"]}])
        self.assertEqual(ContactFlow(legacy_root).get_tags("L"), ["hi"])
        # Unrelated records stay byte-identical in value; reports keep totals.
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.follow_up("A", "2026-10-01", "note")
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_opportunity_amount("O1", "12.50")
        self.app.set_reminder("A", "2099-01-01", "call")
        funnel_before = self.app.funnel_report()
        money_before = self.app.opportunity_amount_report()
        more = self.write_csv("more.csv",
            "contact_id,tag\nA,lead\nB,lead\n")
        self.app.import_contact_tags(str(more))
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["lead", "ok"])
        self.assertEqual(reopened.timeline("A")[0]["note"], "note")
        self.assertEqual(reopened.find_opportunities(contact_id="A")[0]["title"], "Deal")
        self.assertEqual(reopened.due_reminders("2099-12-31"),
                         [{"contact_id": "A", "due_on": "2099-01-01", "note": "call"}])
        self.assertEqual(reopened.funnel_report(), funnel_before)
        self.assertEqual(reopened.opportunity_amount_report(), money_before)
        # The new tag drives find, expressions and every tag-filtering report.
        self.assertEqual([c["contact_id"] for c in reopened.find(tags=["lead"])], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in reopened.find(tag_expression='"lead" && !"ok"')],
                         ["B"])
        self.assertEqual(reopened.funnel_report(tags=["lead"])["total"]["contacts"], 2)
        self.assertEqual(reopened.funnel_report(tags=["ok"])["total"]["contacts"], 1)
        self.assertEqual(reopened.followup_report("2024-01-01", "2100-01-01",
                                                  tags=["lead"])["records"][0]["contact_id"], "A")

    def test_cli_import_contact_tags_success_failure_and_array(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("b", "Bob", "b@example.test", "Music")
        csv_path = self.write_csv("tags.csv",
            "contact_id,tag\n A , VIP \nb,lead\nA,华东\n")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"csv_path": str(csv_path)}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "import-contact-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A", "tags": ["vip", "华东"]},
            {"contact_id": "b", "tags": ["lead"]},
        ])
        # Unknown contact: stderr JSON with error, exit 2, empty stdout, no write.
        before = self.app.path.read_bytes()
        bad_csv = self.write_csv("bad.csv", "contact_id,tag\nZZZ,x\n")
        payload.write_text(json.dumps({"csv_path": str(bad_csv)}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "import-contact-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing argument is a TypeError surfaced through the same envelope.
        payload.write_text(json.dumps({}), encoding="utf-8")
        missing = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "import-contact-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # Array input: each import is independent and a later failure keeps earlier writes.
        other = self.write_csv("other.csv", "contact_id,tag\nb,extra\n")
        payload.write_text(json.dumps([{"csv_path": str(other)}, {"csv_path": str(bad_csv)}]),
                           encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "import-contact-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        json.loads(partial.stderr)
        self.assertEqual(ContactFlow(self.root).get_tags("b"), ["extra", "lead"])

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

    def seed_snapshot(self):
        # Books (casefold group): A with O1 (no history) and O2 (qualified on
        # 2024-01-10, won on 2024-02-10); D with O3 (qualified on 2024-01-15).
        # books: B with O4 (lost on 2024-01-20). Music: C with no opportunities.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified", on="2024-01-10")
        self.app.set_stage("O2", "won", on="2024-02-10")
        self.app.add_opportunity("O3", "D", "Third")
        self.app.set_stage("O3", "qualified", on="2024-01-15")
        self.app.add_opportunity("O4", "B", "Fourth")
        self.app.set_stage("O4", "lost", on="2024-01-20")

    def test_funnel_snapshot_report_counts_stages_as_of_date(self):
        self.seed_snapshot()
        report = self.app.funnel_snapshot_report("2024-01-31")
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        # O2 was qualified on the cutoff, O1 has no history at all.
        self.assertEqual(report["total"],
            {"contacts": 4, "new": 0, "qualified": 2, "won": 0, "lost": 1,
             "unknown": 1, "opportunities": 4})
        self.assertEqual([row["organization"] for row in report["organizations"]],
                         ["Books", "Music"])
        self.assertEqual(report["organizations"][0],
            {"organization": "Books", "contacts": 3, "new": 0, "qualified": 2,
             "won": 0, "lost": 1, "unknown": 1, "opportunities": 4})
        self.assertEqual(report["organizations"][1],
            {"organization": "Music", "contacts": 1, "new": 0, "qualified": 0,
             "won": 0, "lost": 0, "unknown": 0, "opportunities": 0})
        # After the win date O2 counts as won; opportunities always equal the
        # four stages plus unknown.
        later = self.app.funnel_snapshot_report("2024-02-10")
        self.assertEqual(later["total"],
            {"contacts": 4, "new": 0, "qualified": 1, "won": 1, "lost": 1,
             "unknown": 1, "opportunities": 4})
        # Before any dated record everything is unknown.
        early = self.app.funnel_snapshot_report("2024-01-01")
        self.assertEqual(early["total"],
            {"contacts": 4, "new": 0, "qualified": 0, "won": 0, "lost": 0,
             "unknown": 4, "opportunities": 4})

    def test_funnel_snapshot_report_ignores_current_stage_and_future_or_null_records(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        # Imported directly at won with no history: counts as unknown, never won.
        payload = self.root / "opps.csv"
        payload.write_text("opportunity_id,contact_id,title,stage\nO1,A,Deal,won\n",
                           encoding="utf-8")
        self.app.import_opportunities(str(payload))
        report = self.app.funnel_snapshot_report("2024-06-01")
        self.assertEqual(report["total"],
            {"contacts": 1, "new": 0, "qualified": 0, "won": 0, "lost": 0,
             "unknown": 1, "opportunities": 1})
        # Only future-dated or null-dated records: still unknown.
        self.app.add_opportunity("O2", "A", "Future")
        self.app.set_stage("O2", "qualified", on="2099-01-01")
        self.app.add_opportunity("O3", "A", "Undated")
        self.app.set_stage("O3", "qualified")
        report = self.app.funnel_snapshot_report("2024-06-01")
        self.assertEqual(report["total"]["unknown"], 3)
        self.assertEqual(report["total"]["qualified"], 0)
        # A far-future cutoff picks up the dated record but never the null one.
        future = self.app.funnel_snapshot_report("2099-12-31")
        self.assertEqual(future["total"]["qualified"], 1)
        self.assertEqual(future["total"]["unknown"], 2)

    def test_funnel_snapshot_report_same_day_and_reopen_resolution(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_stage("O1", "qualified", on="2024-03-01")
        self.app.set_stage("O1", "won", on="2024-03-10")
        self.app.reopen_opportunities([{"opportunity_id": "O1", "expected_stage": "won",
                                        "on": "2024-03-20"}])
        # Between the dated win and the reopen the deal counts as won.
        self.assertEqual(self.app.funnel_snapshot_report("2024-03-15")["total"]["won"], 1)
        # On the reopen day it counts as qualified.
        reopened = self.app.funnel_snapshot_report("2024-03-20")
        self.assertEqual(reopened["total"]["qualified"], 1)
        self.assertEqual(reopened["total"]["won"], 0)
        # Closed again on the reopen day: the last saved same-day change wins.
        self.app.set_stage("O1", "lost", on="2024-03-20")
        total = self.app.funnel_snapshot_report("2024-03-20")["total"]
        self.assertEqual(total["lost"], 1)
        self.assertEqual(total["qualified"], 0)
        # Each opportunity counts exactly once.
        self.assertEqual(total["opportunities"], 1)
        self.assertEqual(total["new"] + total["qualified"] + total["won"]
                         + total["lost"] + total["unknown"], 1)
        # Reopening the same root recomputes from the latest data identically.
        self.assertEqual(ContactFlow(self.root).funnel_snapshot_report("2024-03-20")["total"],
                         total)

    def test_funnel_snapshot_report_csv_layout_and_escaping(self):
        self.seed_snapshot()
        report = self.app.funnel_snapshot_report("2024-02-10")
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["organization", "contacts", "new", "qualified",
                                   "won", "lost", "unknown", "opportunities"])
        decoded = [dict(zip(rows[0], row)) for row in rows[1:]]
        for decoded_row, row in zip(decoded, report["organizations"]):
            for field in rows[0][1:]:
                self.assertEqual(int(decoded_row[field]), row[field])
        self.assertTrue(report["csv"].endswith("\n"))
        self.assertNotIn("\r", report["csv"])
        self.assertEqual(len(report["csv"].splitlines()), len(report["organizations"]) + 1)
        # Standard escaping survives a strict round-trip.
        self.app.add_contact("E", "Eve", "e@example.test", "Book, \"店\"\n二楼")
        escaped = self.app.funnel_snapshot_report("2024-02-10")
        self.assertIn('"Book, ""店""\n二楼",1,0,0,0,0,0,0', escaped["csv"])
        strict = list(csv.reader(io.StringIO(escaped["csv"]), strict=True))
        self.assertEqual(strict[1][0], "Book, \"店\"\n二楼")

    def test_funnel_snapshot_report_filters_and_recomputes_after_updates(self):
        self.seed_snapshot()
        by_org = self.app.funnel_snapshot_report("2024-02-10", organization=" BOOKS ")
        self.assertEqual([row["organization"] for row in by_org["organizations"]], ["Books"])
        self.assertEqual(by_org["total"]["contacts"], 3)
        tagged = self.app.funnel_snapshot_report("2024-02-10", tags=["VIP"])
        self.assertEqual(tagged["total"],
            {"contacts": 1, "new": 0, "qualified": 0, "won": 1, "lost": 0,
             "unknown": 1, "opportunities": 2})
        self.assertEqual(self.app.funnel_snapshot_report(
            "2024-02-10", tags=["vip", "missing"], tag_mode="any")["total"]["contacts"], 1)
        # Correcting a history date moves the deal between snapshot buckets.
        self.app.correct_stage_dates([{"opportunity_id": "O2", "index": 1, "on": "2024-01-25"}])
        moved = self.app.funnel_snapshot_report("2024-01-31")
        self.assertEqual(moved["total"]["won"], 1)
        self.assertEqual(moved["total"]["qualified"], 1)
        # Updating a profile regroups; transferring re-buckets the opportunity.
        self.app.update_contact("D", {"organization": "Music"})
        regrouped = self.app.funnel_snapshot_report("2024-02-10")
        by_name = {row["organization"]: row for row in regrouped["organizations"]}
        self.assertEqual(by_name["Music"]["qualified"], 1)
        self.assertEqual(by_name["Music"]["contacts"], 2)
        self.app.transfer_opportunities([{"opportunity_id": "O3", "source_contact_id": "D",
                                          "target_contact_id": "C"}])
        transferred = self.app.funnel_snapshot_report("2024-02-10")
        by_name = {row["organization"]: row for row in transferred["organizations"]}
        self.assertEqual(by_name["Music"]["qualified"], 1)
        # Merging keeps the opportunity count and moves the deals with the contact.
        before = self.app.funnel_snapshot_report("2024-02-10")["total"]["opportunities"]
        self.app.merge_contacts("D", "C")
        merged = self.app.funnel_snapshot_report("2024-02-10")
        self.assertEqual(merged["total"]["opportunities"], before)

    def test_funnel_snapshot_report_empty_and_validation(self):
        # Empty store: counts all zero, no organizations, header-only CSV, and
        # the query never creates the directory.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        report = fresh.funnel_snapshot_report("2024-06-01")
        self.assertEqual(report["total"],
            {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0,
             "unknown": 0, "opportunities": 0})
        self.assertEqual(report["organizations"], [])
        self.assertEqual(report["csv"],
                         "organization,contacts,new,qualified,won,lost,unknown,opportunities\n")
        self.assertFalse(fresh_root.exists())
        # Dates and filters validate even against the empty store.
        for kwargs in [{"as_of": "2024-13-01"}, {"as_of": "2023-02-29"},
                       {"as_of": " 2024-01-01x"}, {"as_of": 20240101}, {"as_of": None},
                       {"as_of": "2024-01-01", "organization": 5},
                       {"as_of": "2024-01-01", "tags": "vip"},
                       {"as_of": "2024-01-01", "tag_mode": "ALL"}]:
            with self.assertRaises(ValueError):
                fresh.funnel_snapshot_report(**kwargs)
        self.assertFalse(fresh_root.exists())
        # A missing as_of is a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            fresh.funnel_snapshot_report()
        # Legal leap days, past and future dates and surrounding whitespace pass.
        self.assertEqual(fresh.funnel_snapshot_report(" 2024-02-29 ")["total"]["contacts"], 0)
        self.assertEqual(fresh.funnel_snapshot_report("1999-12-31")["total"]["opportunities"], 0)
        self.assertFalse(fresh_root.exists())
        # No match: zero counts, empty organizations, header-only CSV, no writes.
        self.seed_snapshot()
        before = self.app.path.read_bytes()
        none = self.app.funnel_snapshot_report("2024-02-10", organization="Missing")
        self.assertEqual(none["total"]["contacts"], 0)
        self.assertEqual(none["organizations"], [])
        self.assertEqual(none["csv"],
                         "organization,contacts,new,qualified,won,lost,unknown,opportunities\n")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_funnel_snapshot_report_success_and_failure(self):
        self.seed_snapshot()
        payload = self.root / "snapshot.json"
        payload.write_text(json.dumps({"as_of": "2024-02-10"}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "funnel-snapshot-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(report["total"],
            {"contacts": 4, "new": 0, "qualified": 1, "won": 1, "lost": 1,
             "unknown": 1, "opportunities": 4})
        # Array input: each item is an independent call.
        payload.write_text(json.dumps([{"as_of": "2024-01-01"},
                                       {"as_of": "2024-02-10", "organization": "Music"}]),
                           encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "funnel-snapshot-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual(values[0]["total"]["unknown"], 4)
        self.assertEqual(values[1]["organizations"][0]["organization"], "Music")
        # Missing as_of or an invalid date: exit 2, empty stdout, JSON error.
        payload.write_text(json.dumps({"as_of": "not-a-date"}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "funnel-snapshot-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        payload.write_text(json.dumps({}), encoding="utf-8")
        missing = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "funnel-snapshot-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2)
        json.loads(missing.stderr)

    def test_set_reminder_replaces_and_persists_trimmed_values(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        result = self.app.set_reminder(" A ", "2024-02-29", "  call  him  ")
        self.assertEqual(result, {"contact_id": "A", "due_on": "2024-02-29", "note": "call  him"})
        # Past dates and legal leap days are allowed; surrounding whitespace is trimmed.
        again = self.app.set_reminder("A", " 2026-11-05 ", "later")
        self.assertEqual(again, {"contact_id": "A", "due_on": "2026-11-05", "note": "later"})
        # At most one reminder per contact: setting again replaces the whole entry.
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(list(stored["reminders"]), ["A"])
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"),
                         [{"contact_id": "A", "due_on": "2026-11-05", "note": "later"}])

    def test_clear_reminder_reports_presence_and_rewrites_only_when_set(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "note")
        self.assertIs(self.app.clear_reminder("A"), True)
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"), [])
        self.assertNotIn("reminders", json.loads(self.app.path.read_text(encoding="utf-8")))
        # No reminder present: False without touching the file.
        before = self.app.path.read_bytes()
        self.assertIs(self.app.clear_reminder("A"), False)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_due_reminders_orders_by_date_then_contact_id_without_system_clock(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("陈", "Chen", "c@example.test", "Games")
        self.app.set_reminder("A", "2026-11-05", "future")
        self.app.set_reminder("B", "2026-10-01", "overdue")
        self.app.set_reminder("陈", "2026-10-02", "same day as target")
        self.app.set_reminder("陈", "2026-10-02", "replaced")  # replace, still one
        # as_of includes the due day itself and overdue items; explicit, never today.
        due = self.app.due_reminders(" 2026-10-02 ")
        self.assertEqual([(r["contact_id"], r["due_on"], r["note"]) for r in due],
                         [("B", "2026-10-01", "overdue"), ("陈", "2026-10-02", "replaced")])
        self.assertTrue(all(set(r) == {"contact_id", "due_on", "note"} for r in due))
        # Same day orders by contact id code point: "B" < "陈".
        same_day = ContactFlow(self.root).due_reminders("2026-11-05")
        self.assertEqual([r["contact_id"] for r in same_day], ["B", "陈", "A"])
        self.assertEqual(self.app.due_reminders("2026-09-30"), [])

    def test_reminder_validation_rejects_without_changing_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        for cid in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.set_reminder(cid, "2026-10-02", "x")
            with self.assertRaises(ValueError):
                self.app.clear_reminder(cid)
        for note in [None, 5, "", "   "]:
            with self.assertRaises(ValueError):
                self.app.set_reminder("A", "2026-10-02", note)
        for day in [None, 5, "", "   ", "2026-02-30", "2026-13-01", "2026-1-1", "20261002"]:
            with self.assertRaises(ValueError):
                self.app.set_reminder("A", day, "x")
            with self.assertRaises(ValueError):
                self.app.due_reminders(day)
        # Unknown contact rejects set and clear, never writing.
        with self.assertRaises(ValueError):
            self.app.set_reminder("ZZZ", "2026-10-02", "x")
        with self.assertRaises(ValueError):
            self.app.clear_reminder("ZZZ")
        # A store with no data file: unknown-contact set/clear and queries create nothing.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.set_reminder("ZZZ", "2026-10-02", "x")
        with self.assertRaises(ValueError):
            fresh.clear_reminder("ZZZ")
        self.assertEqual(fresh.due_reminders("2026-10-02"), [])
        self.assertFalse((self.root / "fresh").exists())
        # Once a reminder exists, a later rejected call leaves the file untouched.
        self.app.set_reminder("A", "2026-10-02", "keep")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.set_reminder("A", "2026-02-30", "bad")
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01")[0]["note"], "keep")

    def test_follow_up_does_not_clear_reminder(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "nudge")
        self.app.follow_up("A", "2026-10-03", "chatted")
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"),
                         [{"contact_id": "A", "due_on": "2026-10-05", "note": "nudge"}])

    def seed_merge_reminders(self):
        self.app.add_contact("S", "Src", "s@example.test", "X")
        self.app.add_contact("T", "Tgt", "t@example.test", "Y")

    def test_merge_reminder_selection_rules(self):
        # Only the source has a reminder: it survives under the target id.
        self.seed_merge_reminders()
        self.app.set_reminder("S", "2026-10-01", "src")
        result = self.app.merge_contacts("S", "T")
        self.assertEqual(set(result), {"contact", "moved_followups"})
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"),
                         [{"contact_id": "T", "due_on": "2026-10-01", "note": "src"}])

        # Only the target has one: it is retained.
        root2 = self.root / "two"
        app2 = ContactFlow(root2)
        app2.add_contact("S", "Src", "s@example.test", "X")
        app2.add_contact("T", "Tgt", "t@example.test", "Y")
        app2.set_reminder("T", "2026-10-01", "tgt")
        app2.merge_contacts("S", "T")
        self.assertEqual(ContactFlow(root2).due_reminders("2099-01-01"),
                         [{"contact_id": "T", "due_on": "2026-10-01", "note": "tgt"}])

        # Both: the earlier due date wins, carrying its note and the target id.
        root3 = self.root / "three"
        app3 = ContactFlow(root3)
        app3.add_contact("S", "Src", "s@example.test", "X")
        app3.add_contact("T", "Tgt", "t@example.test", "Y")
        app3.set_reminder("T", "2026-10-05", "tgt")
        app3.set_reminder("S", "2026-10-01", "src")
        app3.merge_contacts("S", "T")
        self.assertEqual(ContactFlow(root3).due_reminders("2099-01-01"),
                         [{"contact_id": "T", "due_on": "2026-10-01", "note": "src"}])

        # Both due the same day: the target's original reminder is kept.
        root4 = self.root / "four"
        app4 = ContactFlow(root4)
        app4.add_contact("S", "Src", "s@example.test", "X")
        app4.add_contact("T", "Tgt", "t@example.test", "Y")
        app4.set_reminder("T", "2026-10-01", "tgt")
        app4.set_reminder("S", "2026-10-01", "src")
        app4.merge_contacts("S", "T")
        self.assertEqual(ContactFlow(root4).due_reminders("2099-01-01"),
                         [{"contact_id": "T", "due_on": "2026-10-01", "note": "tgt"}])

    def test_reminders_on_legacy_data_read_as_empty(self):
        # A data file written by an older version has no "reminders" key.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertNotIn("reminders", raw)
        self.assertEqual(self.app.due_reminders("2099-01-01"), [])
        self.assertIs(self.app.clear_reminder("A"), False)

    def test_cli_reminder_commands_success_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        payload = self.root / "reminder.json"

        def cli(action, obj):
            payload.write_text(json.dumps(obj), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                   action, str(payload)], text=True, capture_output=True)

        ok = cli("set-reminder", {"contact_id": " A ", "due_on": " 2026-10-05 ", "note": " call "})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
                         {"contact_id": "A", "due_on": "2026-10-05", "note": "call"})
        cli("set-reminder", {"contact_id": "B", "due_on": "2026-10-01", "note": "b"})
        due = cli("due-reminders", {"as_of": "2026-10-05"})
        self.assertEqual(due.returncode, 0, due.stderr)
        self.assertEqual([r["contact_id"] for r in json.loads(due.stdout)], ["B", "A"])
        cleared = cli("clear-reminder", {"contact_id": "B"})
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIs(json.loads(cleared.stdout), True)
        again = cli("clear-reminder", {"contact_id": "B"})
        self.assertIs(json.loads(again.stdout), False)

        # Invalid date: exit 2, empty stdout, JSON error on stderr, no change.
        before = self.app.path.read_bytes()
        failed = cli("set-reminder", {"contact_id": "A", "due_on": "2026-02-30", "note": "x"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)
        bad_as_of = cli("due-reminders", {"as_of": "nope"})
        self.assertEqual(bad_as_of.returncode, 2)
        self.assertEqual(bad_as_of.stdout, "")

        # Array executes in order; an early success is kept when a later call fails.
        payload.write_text(json.dumps([
            {"contact_id": "A", "due_on": "2030-01-01", "note": "far"},
            {"contact_id": "ZZZ", "due_on": "2030-01-01", "note": "x"},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "set-reminder", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        json.loads(partial.stderr)
        self.assertEqual(ContactFlow(self.root).due_reminders("2030-12-31"),
                         [{"contact_id": "A", "due_on": "2030-01-01", "note": "far"}])

    def test_update_contact_changes_fields_and_persists_like_add(self):
        self.app.add_contact("A", "Alice", "alice@example.test", "Books")
        result = self.app.update_contact(" A ", {"name": "  Alice 王 ", "email": " ALICE@Example.TEST "})
        self.assertEqual(result,
            {"contact_id": "A", "name": "Alice 王", "email": "alice@example.test", "organization": "Books"})
        result = self.app.update_contact("A", {"organization": "\t春山 书店\n", "name": "Alice"})
        self.assertEqual(result,
            {"contact_id": "A", "name": "Alice", "email": "alice@example.test", "organization": "春山 书店"})
        # Internal whitespace in name/organization is preserved; reopening shows the update.
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.find(" 春山 书店 ")[0]["name"], "Alice")
        self.assertEqual(reopened.find("books"), [])

    def test_update_contact_id_is_case_sensitive_and_trimmed(self):
        self.app.add_contact("a", "Alice", "alice@example.test", "Books")
        self.assertEqual(self.app.update_contact(" a ", {"name": "Ali"})["contact_id"], "a")
        with self.assertRaises(ValueError):
            self.app.update_contact("A", {"name": "Other"})
        self.assertEqual(ContactFlow(self.root).find()[0]["name"], "Ali")

    def test_update_contact_rejects_bad_id_or_unknown_without_writing(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        for bad_id in ["", "   ", None, 5]:
            with self.assertRaises(ValueError):
                self.app.update_contact(bad_id, {"name": "X"})
        with self.assertRaises(ValueError):
            self.app.update_contact("ZZZ", {"name": "X"})
        self.assertEqual(self.app.path.read_bytes(), before)
        # Unknown contact against a missing data file creates neither directory nor file.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.update_contact("ZZZ", {"name": "X"})
        self.assertFalse((self.root / "fresh").exists())

    def test_update_contact_validates_changes_without_partial_write(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        bad_changes = [
            None, [], [{"name": "X"}], "name", 5, True,
            {},
            {"contact_id": "B"}, {"id": "A"}, {"name": "X", "surname": "Y"},
            {"name": None}, {"email": None}, {"organization": None},
            {"name": 5}, {"email": ["a@b.test"]}, {"organization": {}},
            {"name": ""}, {"name": "   "}, {"email": "  "}, {"organization": "\n\t"},
        ]
        for changes in bad_changes:
            with self.assertRaises(ValueError):
                self.app.update_contact("A", changes)
            self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(ContactFlow(self.root).find()[0],
            {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})

    def test_update_contact_validates_email_format(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        for bad in ["no-at-sign", "a@b@example.test", "a b@example.test", "a@b test",
                    "@example.test", "a@", "a@ ", " @example.test"]:
            with self.assertRaises(ValueError):
                self.app.update_contact("A", {"email": bad})
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_update_contact_email_duplicate_and_identity_rules(self):
        self.app.add_contact("A", "Alice", "alice@example.test", "Books")
        self.app.add_contact("B", "Bob", "bob@example.test", "Music")
        before = self.app.path.read_bytes()
        # Another contact's normalized email (case/whitespace-insensitive) is rejected.
        for bad in ["BOB@example.test", " Bob@Example.test "]:
            with self.assertRaises(ValueError):
                self.app.update_contact("A", {"email": bad})
        self.assertEqual(self.app.path.read_bytes(), before)
        # The contact's own current email, in any case, is a valid no-op.
        self.assertEqual(self.app.update_contact("A", {"email": " ALICE@EXAMPLE.TEST "})["email"],
            "alice@example.test")
        # After changing the email, the old one can be registered again and the new one is deduped.
        self.app.update_contact("A", {"email": "alice2@example.test"})
        self.app.add_contact("C", "Cara", "alice@example.test", "Games")
        with self.assertRaises(ValueError):
            self.app.add_contact("D", "Dan", "Alice2@example.test", "Toys")
        # Import is bound by the same existing-email dedup rule.
        csv_path = self.root / "people.csv"
        csv_path.write_text(
            "contact_id,name,email,organization\nE,Eve,ALICE2@example.test,Toys\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.app.import_contacts(str(csv_path))

    def test_update_contact_is_atomic_across_fields(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        # A valid name together with a duplicate email: the whole call fails, name stays.
        with self.assertRaises(ValueError):
            self.app.update_contact("A", {"name": "Ali", "email": "b@example.test"})
        self.assertEqual(self.app.path.read_bytes(), before)
        contact = ContactFlow(self.root).find()[0]
        self.assertEqual((contact["name"], contact["email"]), ("Alice", "a@example.test"))
        # A valid organization together with an unsupported key is also fully rejected.
        with self.assertRaises(ValueError):
            self.app.update_contact("A", {"organization": "Toys", "nope": "x"})
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_update_contact_noop_returns_contact_without_rewrite(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        result = self.app.update_contact(" A ", {"name": "  Alice  ", "email": " A@EXAMPLE.test ",
                                                "organization": " Books "})
        self.assertEqual(result,
            {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_update_contact_preserves_related_records_and_legacy_shape(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.follow_up("A", "2026-10-01", "first note")
        self.app.follow_up("A", "2026-10-03", "second note")
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        self.app.set_reminder("A", "2026-11-05", "call back")
        self.app.update_contact("A", {"name": "Alice Wang", "email": "ali@example.test",
                                      "organization": "Music"})
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["first note", "second note"])
        opportunities = {o["opportunity_id"]: o for o in reopened.find_opportunities(contact_id="A")}
        self.assertEqual(opportunities["O1"]["stage"], "new")
        self.assertEqual(opportunities["O2"]["stage"], "won")
        self.assertEqual(reopened.due_reminders("2099-01-01"),
            [{"contact_id": "A", "due_on": "2026-11-05", "note": "call back"}])
        # Legacy document with contacts but no tags/opportunities/reminders/followups updates fine.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        legacy.update_contact("L", {"organization": "New"})
        self.assertEqual(ContactFlow(legacy_root).find("new")[0]["contact_id"], "L")
        self.assertEqual(ContactFlow(legacy_root).find_opportunities(contact_id="L"), [])

    def test_update_organization_moves_contact_and_opportunities_in_find_and_funnel(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        before = self.app.funnel_report()
        self.assertEqual(before["total"],
            {"contacts": 2, "new": 1, "qualified": 0, "won": 1, "lost": 0, "opportunities": 2})
        self.app.update_contact("A", {"organization": "music"})
        # find organization filtering follows the new organization (casefold grouped).
        self.assertEqual([c["contact_id"] for c in self.app.find(organization="Music")], ["A", "B"])
        self.assertEqual(self.app.find(organization="Books"), [])
        report = self.app.funnel_report()
        # Unfiltered totals never move; only the organization grouping changes.
        self.assertEqual(report["total"], before["total"])
        self.assertEqual([row["organization"] for row in report["organizations"]], ["Music"])
        music = report["organizations"][0]
        self.assertEqual(music,
            {"organization": "Music", "contacts": 2, "new": 1, "qualified": 0, "won": 1,
             "lost": 0, "opportunities": 2})
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[1], ["Music", "2", "1", "0", "1", "0", "2"])
        # Filtering for the new organization counts both contacts and A's opportunities.
        filtered = self.app.funnel_report(organization="MUSIC")
        self.assertEqual(filtered["total"], report["total"])
        self.app.update_contact("A", {"organization": "Books"})
        self.assertEqual(self.app.funnel_report()["total"], before["total"])

    def test_cli_update_contact_object_and_array(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.add_opportunity("O1", "A", "Deal")
        payload = self.root / "update.json"
        payload.write_text(json.dumps({"contact_id": " A ", "changes": {"name": " Ali ", "email": " ALI@x.test "}}),
                           encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "update-contact", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
            {"contact_id": "A", "name": "Ali", "email": "ali@x.test", "organization": "Books"})
        # Array executes item by item; successes before a failure are kept.
        payload.write_text(json.dumps([
            {"contact_id": "B", "changes": {"organization": "Books"}},
            {"contact_id": "ZZZ", "changes": {"name": "Ghost"}},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "update-contact", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        json.loads(partial.stderr)
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find(organization="books")], ["A", "B"])
        # Unrelated data is untouched by the partial failure.
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.find_opportunities(contact_id="A")[0]["title"], "Deal")
        # A malformed item (empty changes) also fails with the standard JSON error envelope.
        payload.write_text(json.dumps({"contact_id": "A", "changes": {}}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "update-contact", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))

    def test_update_contacts_batch_returns_full_contacts_in_order(self):
        self.app.add_contact("A", "Alice", "alice@example.test", "Books")
        self.app.add_contact("B", "Bob", "bob@example.test", "Music")
        result = self.app.update_contacts([
            {"contact_id": " B ", "changes": {"name": " Bob Wang ", "organization": "\tGames\n"}},
            {"contact_id": "A", "changes": {"name": " Alice 王 "}},
        ])
        self.assertEqual(result, [
            {"contact_id": "B", "name": "Bob Wang", "email": "bob@example.test", "organization": "Games"},
            {"contact_id": "A", "name": "Alice 王", "email": "alice@example.test", "organization": "Books"},
        ])
        # Input order, not storage or id order; reopening the same root shows the new values.
        reopened = ContactFlow(self.root)
        by_id = {c["contact_id"]: c for c in reopened.find()}
        self.assertEqual(by_id["A"]["name"], "Alice 王")
        self.assertEqual((by_id["B"]["name"], by_id["B"]["organization"]), ("Bob Wang", "Games"))

    def test_update_contacts_swaps_and_cycles_emails(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        swapped = self.app.update_contacts([
            {"contact_id": "A", "changes": {"email": " B@Example.test "}},
            {"contact_id": "B", "changes": {"email": " a@EXAMPLE.test "}},
        ])
        self.assertEqual([c["email"] for c in swapped], ["b@example.test", "a@example.test"])
        reopened = ContactFlow(self.root)
        by_id = {c["contact_id"]: c for c in reopened.find()}
        self.assertEqual((by_id["A"]["email"], by_id["B"]["email"]),
                         ("b@example.test", "a@example.test"))
        # A three-way cycle, with a non-participant keeping an unrelated address.
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Toys")
        cycled = self.app.update_contacts([
            {"contact_id": "A", "changes": {"email": "c@example.test"}},
            {"contact_id": "B", "changes": {"email": "b@example.test"}},
            {"contact_id": "C", "changes": {"email": " A@example.test "}},
        ])
        self.assertEqual([c["email"] for c in cycled],
                         ["c@example.test", "b@example.test", "a@example.test"])
        final = {c["contact_id"]: c["email"] for c in ContactFlow(self.root).find()}
        self.assertEqual(final, {"A": "c@example.test", "B": "b@example.test",
                                 "C": "a@example.test", "D": "d@example.test"})

    def test_update_contacts_rejects_taking_nonparticipant_email(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        # A alone takes B's address while B does not participate: whole batch fails.
        with self.assertRaises(ValueError):
            self.app.update_contacts([{"contact_id": "A", "changes": {"email": "b@example.test"}}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Two participants ending on the same address also fail, even via a swap-like shape.
        with self.assertRaises(ValueError):
            self.app.update_contacts([
                {"contact_id": "A", "changes": {"email": "x@example.test"}},
                {"contact_id": "B", "changes": {"email": "X@example.test"}},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual({c["contact_id"]: c["email"] for c in ContactFlow(self.root).find()},
                         {"A": "a@example.test", "B": "b@example.test"})

    def test_update_contacts_requires_list(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        for bad in [None, {}, {"updates": []}, "x", 5, True, ({"contact_id": "A", "changes": {"name": "X"}},)]:
            with self.assertRaises(ValueError):
                self.app.update_contacts(bad)
            self.assertEqual(self.app.path.read_bytes(), before)
        # Missing required parameter is a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            self.app.update_contacts()

    def test_update_contacts_empty_list_writes_nothing(self):
        # Against a missing data file: empty result, and neither directory nor file is created.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.update_contacts([]), [])
        self.assertFalse(fresh_root.exists())
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.update_contacts([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_update_contacts_validates_item_shape_without_partial_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [{"contact_id": "A"}],
            [{"changes": {"name": "X"}}],
            [{"contact_id": "A", "changes": {"name": "X"}, "extra": 1}],
            [{"contact_id": "A", "changes": {}}],
            [{"contact_id": "A", "changes": None}],
            [{"contact_id": "A", "changes": {"contact_id": "B"}}],
            [{"contact_id": "A", "changes": {"name": "X", "surname": "Y"}}],
            [{"contact_id": "A", "changes": {"name": None}}],
            [{"contact_id": "A", "changes": {"email": None}}],
            [{"contact_id": "A", "changes": {"name": 5}}],
            [{"contact_id": "A", "changes": {"name": "   "}}],
            [{"contact_id": "A", "changes": {"email": "no-at-sign"}}],
            [{"contact_id": "A", "changes": {"email": "a@b@example.test"}}],
            [{"contact_id": "A", "changes": {"email": "a b@example.test"}}],
            [{"contact_id": "A", "changes": {"email": "@example.test"}}],
            # A valid first item followed by an invalid second item rolls both back.
            [{"contact_id": "A", "changes": {"name": "Ali"}},
             {"contact_id": "B", "changes": {"email": "bad"}}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.update_contacts(batch)
            self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual({c["contact_id"]: c for c in ContactFlow(self.root).find()},
                         {"A": {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                                "organization": "Books"},
                          "B": {"contact_id": "B", "name": "Bob", "email": "b@example.test",
                                "organization": "Music"}})

    def test_update_contacts_rejects_bad_unknown_or_duplicate_ids(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        for bad_id in ["", "   ", None, 5]:
            with self.assertRaises(ValueError):
                self.app.update_contacts([{"contact_id": bad_id, "changes": {"name": "X"}}])
            self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.update_contacts([{"contact_id": "ZZZ", "changes": {"name": "X"}}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Duplicate normalized ids reject the batch, even when the two items are identical.
        with self.assertRaises(ValueError):
            self.app.update_contacts([
                {"contact_id": " A ", "changes": {"name": "Ali"}},
                {"contact_id": "A", "changes": {"name": "Ali"}},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Ids are case-sensitive: "a" is unknown when only "A" exists.
        with self.assertRaises(ValueError):
            self.app.update_contacts([{"contact_id": "a", "changes": {"name": "Ali"}}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Unknown contact against a missing data file creates neither directory nor file.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.update_contacts([{"contact_id": "ZZZ", "changes": {"name": "X"}}])
        self.assertFalse((self.root / "fresh").exists())

    def test_update_contacts_is_atomic_when_later_item_fails(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        # The first item moves A onto B's address while B keeps it: the final state has a
        # duplicate, so the whole batch (including A's name change) must be rolled back.
        with self.assertRaises(ValueError):
            self.app.update_contacts([
                {"contact_id": "A", "changes": {"name": "Ali", "email": "b@example.test"}},
                {"contact_id": "B", "changes": {"name": "Bobby"}},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        by_id = {c["contact_id"]: c for c in ContactFlow(self.root).find()}
        self.assertEqual((by_id["A"]["name"], by_id["A"]["email"]), ("Alice", "a@example.test"))
        self.assertEqual((by_id["B"]["name"], by_id["B"]["email"]), ("Bob", "b@example.test"))

    def test_update_contacts_noop_returns_contacts_without_rewrite(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        result = self.app.update_contacts([
            {"contact_id": " A ", "changes": {"name": "  Alice ", "email": " A@EXAMPLE.test ",
                                              "organization": " Books "}},
            {"contact_id": "B", "changes": {"name": "Bob"}},
        ])
        self.assertEqual(result, [
            {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"},
            {"contact_id": "B", "name": "Bob", "email": "b@example.test", "organization": "Music"},
        ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_update_contacts_preserves_related_records_and_legacy_shape(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.follow_up("A", "2026-10-01", "first note")
        self.app.follow_up("A", "2026-10-03", "second note")
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        self.app.set_reminder("A", "2026-11-05", "call back")
        self.app.update_contacts([
            {"contact_id": " A ", "changes": {"name": "Alice Wang", "email": "ali@example.test",
                                              "organization": "Music"}},
            {"contact_id": "B", "changes": {"organization": "Games"}},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["first note", "second note"])
        opportunities = {o["opportunity_id"]: o for o in reopened.find_opportunities(contact_id="A")}
        self.assertEqual(opportunities["O1"]["stage"], "new")
        self.assertEqual(opportunities["O2"]["stage"], "won")
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "A", "due_on": "2026-11-05", "note": "call back"}])
        # Legacy document missing the optional collections still updates.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        result = legacy.update_contacts([{"contact_id": "L", "changes": {"organization": "New"}}])
        self.assertEqual(result[0]["organization"], "New")
        self.assertEqual(ContactFlow(legacy_root).find("new")[0]["contact_id"], "L")
        self.assertEqual(ContactFlow(legacy_root).find_opportunities(contact_id="L"), [])

    def test_update_contacts_organization_moves_groups_without_changing_totals(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified")
        self.app.set_stage("O2", "won")
        before = self.app.funnel_report()
        self.app.update_contacts([{"contact_id": "A", "changes": {"organization": "music"}}])
        self.assertEqual([c["contact_id"] for c in self.app.find(organization="Music")], ["A", "B"])
        self.assertEqual(self.app.find(organization="Books"), [])
        report = self.app.funnel_report()
        self.assertEqual(report["total"], before["total"])
        self.assertEqual([row["organization"] for row in report["organizations"]], ["Music"])
        self.assertEqual(report["organizations"][0],
            {"organization": "Music", "contacts": 2, "new": 1, "qualified": 0, "won": 1,
             "lost": 0, "opportunities": 2})

    def test_cli_update_contacts_object_empty_array_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.add_opportunity("O1", "A", "Deal")
        payload = self.root / "batch.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "update-contacts", str(payload)], text=True, capture_output=True)

        ok = cli({"updates": [
            {"contact_id": " A ", "changes": {"email": " B@Example.test "}},
            {"contact_id": "B", "changes": {"email": " a@example.test ", "name": " Bobby "}},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A", "name": "Alice", "email": "b@example.test", "organization": "Books"},
            {"contact_id": "B", "name": "Bobby", "email": "a@example.test", "organization": "Music"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.find_opportunities(contact_id="A")[0]["title"], "Deal")
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"updates": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"updates": [{"contact_id": "A", "changes": {"email": "not-an-email"}}]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # updates must be a list.
        not_list = cli({"updates": {"contact_id": "A", "changes": {"name": "X"}}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")

    def test_cli_update_contacts_outer_array_keeps_earlier_batches(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        payload = self.root / "batches.json"
        # An outer array runs whole batches independently; a later failed batch keeps
        # the earlier successful batch (unlike the atomicity inside one updates list).
        payload.write_text(json.dumps([
            {"updates": [{"contact_id": "A", "changes": {"organization": "Music"}}]},
            {"updates": [{"contact_id": "ZZZ", "changes": {"name": "Ghost"}}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "update-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        by_id = {c["contact_id"]: c for c in ContactFlow(self.root).find()}
        self.assertEqual(by_id["A"]["organization"], "Music")
        self.assertEqual(by_id["B"]["organization"], "Music")
        self.assertEqual(by_id["C"]["organization"], "Games")

    def test_rename_contacts_returns_full_contacts_in_input_order(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        result = self.app.rename_contacts([
            {"old_id": " A ", "new_id": " A-2 "},
            {"old_id": "B", "new_id": "B"},
        ])
        self.assertEqual(result, [
            {"contact_id": "A-2", "name": "Alice", "email": "a@example.test", "organization": "Books"},
            {"contact_id": "B", "name": "Bob", "email": "b@example.test", "organization": "Music"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual([c["contact_id"] for c in reopened.find()], ["A-2", "B"])
        self.assertEqual(reopened.find()[0], result[0])

    def test_rename_contacts_swaps_and_cycles_ids(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        swapped = self.app.rename_contacts([
            {"old_id": "A", "new_id": "B"},
            {"old_id": "B", "new_id": "A"},
        ])
        self.assertEqual([c["contact_id"] for c in swapped], ["B", "A"])
        by_id = {c["contact_id"]: c for c in ContactFlow(self.root).find()}
        self.assertEqual(by_id["A"]["name"], "Bob")
        self.assertEqual(by_id["B"]["name"], "Alice")
        self.assertEqual(by_id["C"]["name"], "Cara")
        # Cycle A->B, B->C, C->A against the post-swap identities; the input
        # order of the items does not change the final state.
        self.app.rename_contacts([
            {"old_id": "C", "new_id": "A"},
            {"old_id": "A", "new_id": "B"},
            {"old_id": "B", "new_id": "C"},
        ])
        by_id = {c["contact_id"]: c for c in ContactFlow(self.root).find()}
        self.assertEqual(by_id["A"]["name"], "Cara")
        self.assertEqual(by_id["B"]["name"], "Bob")
        self.assertEqual(by_id["C"]["name"], "Alice")

    def test_rename_contacts_ids_are_case_sensitive(self):
        self.app.add_contact("a", "Alice", "a@example.test", "Books")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.rename_contacts([{"old_id": "A", "new_id": "B"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        result = self.app.rename_contacts([{"old_id": " a ", "new_id": "A"}])
        self.assertEqual(result[0]["contact_id"], "A")
        self.assertEqual(ContactFlow(self.root).find()[0]["contact_id"], "A")

    def test_rename_contacts_moves_related_records_and_preserves_reports(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.follow_up("A", "2026-10-01", "first")
        self.app.follow_up("A", "2026-10-01", "first")  # duplicate kept verbatim
        self.app.follow_up("B", "2026-10-02", "other")
        self.app.add_opportunity("O1", "A", "Deal one")
        self.app.add_opportunity("O2", "A", "Deal two")
        self.app.set_stage("O2", "qualified", on="2026-09-01")
        self.app.set_stage("O2", "won", on="2026-09-15")
        self.app.set_opportunity_amount("O2", "100.5")
        self.app.set_reminder("A", "2026-11-05", "call back", repeat_monthly=True)
        before_funnel = self.app.funnel_report()
        before_amounts = self.app.opportunity_amount_report()
        self.app.rename_contacts([{"old_id": "A", "new_id": "A-9"}])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A-9"), ["vip", "华东"])
        with self.assertRaises(ValueError):
            reopened.get_tags("A")
        # Followup dates, notes, duplicates and save order are preserved.
        timeline = reopened.timeline("A-9")
        self.assertEqual([(r["on"], r["note"]) for r in timeline],
                         [("2026-10-01", "first"), ("2026-10-01", "first")])
        opportunities = {o["opportunity_id"]: o
                         for o in reopened.find_opportunities(contact_id="A-9")}
        self.assertEqual(opportunities["O1"],
            {"opportunity_id": "O1", "contact_id": "A-9", "title": "Deal one", "stage": "new"})
        # An unset amount is not back-filled by the rename.
        self.assertNotIn("amount", opportunities["O1"])
        self.assertEqual(opportunities["O2"]["stage"], "won")
        self.assertEqual(opportunities["O2"]["amount"], "100.50")
        self.assertEqual(reopened.stage_history("O2"), [
            {"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
            {"from_stage": "qualified", "to_stage": "won", "on": "2026-09-15"},
        ])
        # Reminder due date, note and repetition fields survive the rename.
        self.assertEqual(reopened.due_reminders("2099-01-01"), [
            {"contact_id": "A-9", "due_on": "2026-11-05", "note": "call back",
             "repeat_monthly": True, "anchor_day": 5}])
        # Reports keep their filters, ordering, CSV format and totals.
        self.assertEqual(reopened.funnel_report(), before_funnel)
        self.assertEqual(reopened.opportunity_amount_report(), before_amounts)
        report = reopened.followup_report("2026-01-01", "2026-12-31")
        self.assertEqual([r["contact_id"] for r in report["records"]], ["A-9", "A-9", "B"])
        self.assertEqual(report["records"][0]["name"], "Alice")

    def test_rename_contacts_frees_old_ids_for_registration(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.follow_up("A", "2026-10-01", "note")
        self.app.rename_contacts([{"old_id": "A", "new_id": "A-2"}])
        # The old id is no longer reused, so it reads as an unknown contact...
        with self.assertRaises(ValueError):
            self.app.timeline("A")
        with self.assertRaises(ValueError):
            self.app.follow_up("A", "2026-10-02", "ghost")
        # ...and can be registered again.
        self.app.add_contact("A", "Annie", "annie@example.test", "Games")
        self.assertEqual(self.app.get_tags("A"), [])
        self.assertEqual(self.app.timeline("A"), [])
        self.assertEqual([r["contact_id"] for r in self.app.timeline("A-2")], ["A-2"])

    def test_rename_contacts_requires_list_and_empty_list_writes_nothing(self):
        for bad in [None, {}, "A", 5, True, {"old_id": "A", "new_id": "B"}]:
            with self.assertRaises(ValueError):
                self.app.rename_contacts(bad)
        with self.assertRaises(TypeError):
            self.app.rename_contacts()
        # An empty batch on a fresh root creates neither directory nor file.
        fresh = ContactFlow(self.root / "fresh")
        self.assertEqual(fresh.rename_contacts([]), [])
        self.assertFalse((self.root / "fresh").exists())
        self.assertEqual(self.app.rename_contacts([]), [])
        self.assertFalse(self.app.path.exists())

    def test_rename_contacts_validates_items_without_partial_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        bad_batches = [
            [None], [["old_id", "new_id"]], ["rename"], [5], [True],
            [{}],
            [{"old_id": "A"}],
            [{"new_id": "C"}],
            [{"old_id": "A", "new_id": "C", "extra": 1}],
            [{"old_id": "", "new_id": "C"}],
            [{"old_id": "   ", "new_id": "C"}],
            [{"old_id": None, "new_id": "C"}],
            [{"old_id": 5, "new_id": "C"}],
            [{"old_id": "A", "new_id": ""}],
            [{"old_id": "A", "new_id": "   "}],
            [{"old_id": "A", "new_id": None}],
            [{"old_id": "A", "new_id": 5}],
            [{"old_id": "ZZZ", "new_id": "C"}],
            # Normalized old ids repeat within the batch, even after trimming.
            [{"old_id": "A", "new_id": "C"}, {"old_id": " A ", "new_id": "D"}],
            # Normalized new ids repeat within the batch.
            [{"old_id": "A", "new_id": "C"}, {"old_id": "B", "new_id": " C "}],
            # The new id is taken by a contact outside the batch.
            [{"old_id": "A", "new_id": "B"}],
            [{"old_id": "A", "new_id": " B "}],
            # A later unknown contact rejects the whole batch atomically.
            [{"old_id": "A", "new_id": "C"}, {"old_id": "ZZZ", "new_id": "D"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.rename_contacts(batch)
            self.assertEqual(self.app.path.read_bytes(), before)
        # Unknown contact against a missing data file creates neither directory nor file.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.rename_contacts([{"old_id": "ZZZ", "new_id": "C"}])
        self.assertFalse((self.root / "fresh").exists())

    def test_rename_contacts_identity_batch_returns_without_rewrite(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        before = self.app.path.read_bytes()
        result = self.app.rename_contacts([
            {"old_id": " A ", "new_id": "A"},
            {"old_id": "B", "new_id": " B "},
        ])
        self.assertEqual(result, [
            {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"},
            {"contact_id": "B", "name": "Bob", "email": "b@example.test", "organization": "Music"},
        ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_rename_contacts_preserves_unrelated_data_and_legacy_shape(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("B", ["vip"])
        self.app.follow_up("B", "2026-10-02", "b note")
        self.app.add_opportunity("O1", "B", "B deal")
        self.app.set_reminder("B", "2026-11-01", "b reminder")
        self.app.rename_contacts([{"old_id": "A", "new_id": "A-2"}])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("B"), ["vip"])
        self.assertEqual(reopened.timeline("B")[0]["note"], "b note")
        self.assertEqual(reopened.find_opportunities(contact_id="B")[0]["contact_id"], "B")
        self.assertEqual(reopened.due_reminders("2099-01-01"),
            [{"contact_id": "B", "due_on": "2026-11-01", "note": "b reminder"}])
        # Legacy document with contacts but no optional collections renames fine.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        result = legacy.rename_contacts([{"old_id": "L", "new_id": "L-2"}])
        self.assertEqual(result, [{"contact_id": "L-2", "name": "Lee",
                                   "email": "l@example.test", "organization": "Old"}])
        self.assertEqual(ContactFlow(legacy_root).find()[0]["contact_id"], "L-2")

    def test_rename_contacts_keeps_other_entry_points_unchanged(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        # update_contact still refuses to change contact_id.
        with self.assertRaises(ValueError):
            self.app.update_contact("A", {"contact_id": "B"})
        with self.assertRaises(ValueError):
            self.app.update_contacts([{"contact_id": "A", "changes": {"contact_id": "B"}}])
        # Registration dedup applies to the new id after a rename.
        self.app.rename_contacts([{"old_id": "A", "new_id": "A-2"}])
        with self.assertRaises(ValueError):
            self.app.add_contact("A-2", "Other", "other@example.test", "Toys")
        with self.assertRaises(ValueError):
            self.app.add_contact("B", "Other", "A@EXAMPLE.test", "Toys")

    def test_cli_rename_contacts_object_empty_array_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        payload = self.root / "renames.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "rename-contacts", str(payload)], text=True, capture_output=True)

        ok = cli({"renames": [{"old_id": " A ", "new_id": " A-2 "},
                              {"old_id": "B", "new_id": "A"}]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A-2", "name": "Alice", "email": "a@example.test", "organization": "Books"},
            {"contact_id": "A", "name": "Bob", "email": "b@example.test", "organization": "Music"},
        ])
        self.assertEqual(ContactFlow(self.root).get_tags("A-2"), ["vip"])
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"renames": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"renames": [{"old_id": "A-2", "new_id": "A"}]})  # A is now Bob's id
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # renames must be a list.
        not_list = cli({"renames": {"old_id": "A-2", "new_id": "C"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")

    def test_cli_rename_contacts_outer_array_keeps_earlier_batches(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        payload = self.root / "batches.json"
        # An outer array runs whole batches independently; a later failed batch keeps
        # the earlier successful batch (unlike the atomicity inside one renames list).
        payload.write_text(json.dumps([
            {"renames": [{"old_id": "A", "new_id": "A-2"}]},
            {"renames": [{"old_id": "ZZZ", "new_id": "C"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "rename-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        by_id = {c["contact_id"]: c for c in ContactFlow(self.root).find()}
        self.assertEqual(sorted(by_id), ["A-2", "B"])
        self.assertEqual(by_id["A-2"]["name"], "Alice")

    def test_rename_tags_returns_changed_contacts_sorted_and_normalized(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("b", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        self.app.set_tags("A", [" VIP ", "华东"])
        self.app.set_tags("b", ["vip", "north"])
        self.app.set_tags("C", ["other"])
        # Names trim and casefold like set-tags; inner whitespace is kept.
        result = self.app.rename_tags([{"old_tag": " VIP ", "new_tag": " Lead  Tag "}])
        # Only contacts whose set changed are reported, ids ascending by code
        # point ("C" < "b" is irrelevant here: C is untouched), tags sorted.
        self.assertEqual(result, [
            {"contact_id": "A", "tags": ["lead  tag", "华东"]},
            {"contact_id": "b", "tags": ["lead  tag", "north"]},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["lead  tag", "华东"])
        self.assertEqual(reopened.get_tags("b"), ["lead  tag", "north"])
        self.assertEqual(reopened.get_tags("C"), ["other"])

    def test_rename_tags_applies_simultaneously_chain_merge_swap_cycle(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        self.app.set_tags("A", ["a"])
        self.app.set_tags("B", ["a", "b"])
        self.app.set_tags("C", ["c"])
        # a->b and b->c resolve against the pre-call sets: A gets b, B gets b
        # and c (the new b is not renamed again), C keeps c and drops out of
        # the result because its set is unchanged.
        result = self.app.rename_tags([{"old_tag": "a", "new_tag": "b"},
                                       {"old_tag": "b", "new_tag": "c"}])
        self.assertEqual(result, [
            {"contact_id": "A", "tags": ["b"]},
            {"contact_id": "B", "tags": ["b", "c"]},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["b"])
        self.assertEqual(reopened.get_tags("B"), ["b", "c"])
        self.assertEqual(reopened.get_tags("C"), ["c"])
        # Several sources may merge into one existing or new target name.
        merged = self.app.rename_tags([{"old_tag": "b", "new_tag": "c"},
                                       {"old_tag": "c", "new_tag": "c"}])
        self.assertEqual(merged, [
            {"contact_id": "A", "tags": ["c"]},
            {"contact_id": "B", "tags": ["c"]},
        ])
        self.assertEqual(ContactFlow(self.root).get_tags("C"), ["c"])
        # Swap and cycle among three tags.
        self.app.set_tags("A", ["x"])
        self.app.set_tags("B", ["y"])
        self.app.set_tags("C", ["z"])
        self.app.rename_tags([{"old_tag": "x", "new_tag": "y"},
                              {"old_tag": "y", "new_tag": "z"},
                              {"old_tag": "z", "new_tag": "x"}])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["y"])
        self.assertEqual(reopened.get_tags("B"), ["z"])
        self.assertEqual(reopened.get_tags("C"), ["x"])

    def test_rename_tags_requires_list_and_empty_list_writes_nothing(self):
        for bad in [None, {}, "vip", 5, True, {"old_tag": "a", "new_tag": "b"}]:
            with self.assertRaises(ValueError):
                self.app.rename_tags(bad)
        with self.assertRaises(TypeError):
            self.app.rename_tags()
        # An empty batch on a fresh root creates neither directory nor file.
        fresh = ContactFlow(self.root / "fresh")
        self.assertEqual(fresh.rename_tags([]), [])
        self.assertFalse((self.root / "fresh").exists())
        self.assertEqual(self.app.rename_tags([]), [])
        self.assertFalse(self.app.path.exists())

    def test_rename_tags_validates_items_without_partial_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        before = self.app.path.read_bytes()
        bad_batches = [
            [None], [["old_tag", "new_tag"]], ["rename"], [5], [True],
            [{}],
            [{"old_tag": "vip"}],
            [{"new_tag": "lead"}],
            [{"old_tag": "vip", "new_tag": "lead", "extra": 1}],
            [{"old_tag": "", "new_tag": "lead"}],
            [{"old_tag": "   ", "new_tag": "lead"}],
            [{"old_tag": None, "new_tag": "lead"}],
            [{"old_tag": 5, "new_tag": "lead"}],
            [{"old_tag": ["vip"], "new_tag": "lead"}],
            [{"old_tag": "vip", "new_tag": ""}],
            [{"old_tag": "vip", "new_tag": "   "}],
            [{"old_tag": "vip", "new_tag": None}],
            [{"old_tag": "vip", "new_tag": 5}],
            # The source tag is not used by any existing contact.
            [{"old_tag": "ghost", "new_tag": "lead"}],
            [{"old_tag": "vip", "new_tag": "lead"}, {"old_tag": "ghost", "new_tag": "x"}],
            # Normalized source names repeat within the batch, even identical items.
            [{"old_tag": "vip", "new_tag": "lead"}, {"old_tag": " VIP ", "new_tag": "other"}],
            [{"old_tag": "vip", "new_tag": "lead"}, {"old_tag": "vip", "new_tag": "lead"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.rename_tags(batch)
            self.assertEqual(self.app.path.read_bytes(), before)
        # Unknown source tag against a missing data file creates nothing.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.rename_tags([{"old_tag": "vip", "new_tag": "lead"}])
        self.assertFalse((self.root / "fresh").exists())

    def test_rename_tags_noop_batch_returns_empty_without_rewrite(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        before = self.app.path.read_bytes()
        # Identity rename: every final set equals its pre-call set.
        self.assertEqual(self.app.rename_tags([{"old_tag": " VIP ", "new_tag": "vip"}]), [])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Renaming to a name the contact already has can still be a no-op for
        # some contacts; only genuinely changed sets are reported and written.
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("B", ["lead"])
        before = self.app.path.read_bytes()
        result = self.app.rename_tags([{"old_tag": "vip", "new_tag": "lead"}])
        self.assertEqual(result, [{"contact_id": "A", "tags": ["lead", "华东"]}])
        self.assertNotEqual(self.app.path.read_bytes(), before)
        self.assertEqual(ContactFlow(self.root).get_tags("B"), ["lead"])

    def test_rename_tags_legacy_data_without_tags_collection(self):
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        legacy_root.joinpath("data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        before = legacy.path.read_bytes()
        # A missing tags collection reads as the empty set, so every source is unknown.
        with self.assertRaises(ValueError):
            legacy.rename_tags([{"old_tag": "vip", "new_tag": "lead"}])
        self.assertEqual(legacy.path.read_bytes(), before)
        self.assertEqual(legacy.rename_tags([]), [])
        self.assertEqual(legacy.path.read_bytes(), before)

    def test_rename_tags_preserves_everything_else_and_recomputes_queries(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.follow_up("A", "2026-10-01", "first")
        self.app.add_opportunity("O1", "A", "Deal one")
        self.app.set_stage("O1", "qualified", on="2026-09-01")
        self.app.set_stage("O1", "won", on="2026-09-15")
        self.app.set_opportunity_amount("O1", "100.5")
        self.app.set_reminder("A", "2026-11-05", "call back", repeat_monthly=True)
        before_funnel = self.app.funnel_report()
        before_amounts = self.app.opportunity_amount_report()
        result = self.app.rename_tags([{"old_tag": "vip", "new_tag": "lead"}])
        self.assertEqual(result, [
            {"contact_id": "A", "tags": ["lead", "华东"]},
            {"contact_id": "B", "tags": ["lead"]},
        ])
        reopened = ContactFlow(self.root)
        # The new name drives get-tags, find conditions, expressions and reports.
        self.assertEqual(reopened.get_tags("A"), ["lead", "华东"])
        self.assertEqual([c["contact_id"] for c in reopened.find(tags=["lead"])], ["A", "B"])
        self.assertEqual([c["contact_id"] for c in reopened.find(tag_expression='"lead" && !"华东"')], ["B"])
        self.assertEqual(reopened.funnel_report(tags=["lead"])["total"]["contacts"], 2)
        self.assertEqual(reopened.followup_report("2026-01-01", "2026-12-31",
                                                  tags=["lead"])["records"][0]["contact_id"], "A")
        # The old name keeps its literal meaning: no alias, no rewritten expression.
        self.assertEqual(reopened.find(tags=["vip"]), [])
        self.assertEqual(reopened.find(tag_expression='"vip"'), [])
        self.assertEqual(reopened.funnel_report(tags=["vip"])["total"]["contacts"], 0)
        # Unfiltered reports, profiles, followups, opportunities, history,
        # amounts and reminders keep their values.
        self.assertEqual(reopened.funnel_report(), before_funnel)
        self.assertEqual(reopened.opportunity_amount_report(), before_amounts)
        self.assertEqual(reopened.find()[0],
            {"contact_id": "A", "name": "Alice", "email": "a@example.test", "organization": "Books"})
        self.assertEqual(reopened.timeline("A"), [{"contact_id": "A", "on": "2026-10-01", "note": "first"}])
        self.assertEqual(reopened.find_opportunities(contact_id="A"), [
            {"opportunity_id": "O1", "contact_id": "A", "title": "Deal one",
             "stage": "won", "amount": "100.50"}])
        self.assertEqual(reopened.stage_history("O1"), [
            {"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
            {"from_stage": "qualified", "to_stage": "won", "on": "2026-09-15"},
        ])
        self.assertEqual(reopened.due_reminders("2099-01-01"), [
            {"contact_id": "A", "due_on": "2026-11-05", "note": "call back",
             "repeat_monthly": True, "anchor_day": 5}])

    def test_cli_rename_tags_success_failure_and_outer_array(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.set_tags("A", ["vip"])
        self.app.set_tags("B", ["vip", "north"])
        payload = self.root / "renames.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "rename-tags", str(payload)], text=True, capture_output=True)

        ok = cli({"renames": [{"old_tag": " VIP ", "new_tag": " Lead "}]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A", "tags": ["lead"]},
            {"contact_id": "B", "tags": ["lead", "north"]},
        ])
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"renames": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"renames": [{"old_tag": "ghost", "new_tag": "x"}]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # renames must be a list.
        not_list = cli({"renames": {"old_tag": "lead", "new_tag": "x"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # An outer array runs whole batches independently; a later failed batch
        # keeps the earlier successful batch.
        payload.write_text(json.dumps([
            {"renames": [{"old_tag": "lead", "new_tag": "warm"}]},
            {"renames": [{"old_tag": "ghost", "new_tag": "x"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "rename-tags", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        self.assertEqual(ContactFlow(self.root).get_tags("A"), ["warm"])

    def seed_followups(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.follow_up("B", "2026-10-02", "b second day")
        self.app.follow_up("A", "2026-10-02", "a later save")
        self.app.follow_up("A", "2026-10-01", "a first day")
        self.app.follow_up("A", "2026-10-02", "a earlier save")
        self.app.follow_up("A", "2026-10-02", "a earlier save")  # duplicate kept verbatim
        self.app.follow_up("C", "2026-10-03", "c out of range day")
        # D has no followups; opportunities and reminders do not affect the report.
        self.app.add_opportunity("O1", "D", "Deal")
        self.app.set_reminder("D", "2026-10-01", "nudge")

    def test_followup_report_records_content_and_order(self):
        self.seed_followups()
        report = self.app.followup_report("2026-10-01", "2026-10-02")
        self.assertEqual(set(report), {"records", "csv"})
        self.assertEqual([(r["contact_id"], r["on"], r["note"]) for r in report["records"]],
                         [("A", "2026-10-01", "a first day"),
                          ("A", "2026-10-02", "a later save"),
                          ("A", "2026-10-02", "a earlier save"),
                          ("A", "2026-10-02", "a earlier save"),
                          ("B", "2026-10-02", "b second day")])
        for record in report["records"]:
            self.assertEqual(set(record), {"contact_id", "name", "email", "organization", "on", "note"})
        self.assertEqual(report["records"][0],
            {"contact_id": "A", "name": "Alice", "email": "a@example.test",
             "organization": "Books", "on": "2026-10-01", "note": "a first day"})
        # Inclusive bounds: a single-day range returns that day's entries.
        single = self.app.followup_report("2026-10-02", "2026-10-02")
        self.assertEqual([r["on"] for r in single["records"]], ["2026-10-02"] * 4)
        # D has no followups and produces nothing; C's entry is outside the range.
        self.assertNotIn("D", [r["contact_id"] for r in report["records"]])
        self.assertNotIn("C", [r["contact_id"] for r in report["records"]])

    def test_followup_report_csv_matches_records(self):
        self.seed_followups()
        report = self.app.followup_report("2026-10-01", "2026-10-03")
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["contact_id", "name", "email", "organization", "on", "note"])
        decoded = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual(decoded, report["records"])
        self.assertTrue(report["csv"].endswith("\n"))
        self.assertNotIn("\r", report["csv"])
        self.assertEqual(len(report["csv"].splitlines()), len(report["records"]) + 1)

    def test_followup_report_csv_escapes_and_preserves_internal_newlines(self):
        self.app.add_contact("A", "Al, \"店\"", "a@example.test", "Books")
        self.app.follow_up("A", "2026-10-01", "line one\nline two")
        report = self.app.followup_report("2026-10-01", "2026-10-01")
        self.assertIn('"Al, ""店""",a@example.test,Books,2026-10-01,"line one\nline two"', report["csv"])
        rows = list(csv.reader(io.StringIO(report["csv"]), strict=True))
        self.assertEqual(rows[1][1], "Al, \"店\"")
        self.assertEqual(rows[1][5], "line one\nline two")

    def test_followup_report_filters_organization_tags_and_intersection(self):
        self.seed_followups()
        by_org = self.app.followup_report("2026-10-01", "2026-10-03", organization=" BOOKS ")
        self.assertEqual([r["contact_id"] for r in by_org["records"]], ["A", "A", "A", "A", "B"])
        tagged = self.app.followup_report("2026-10-01", "2026-10-03", tags=["VIP"])
        self.assertEqual([r["contact_id"] for r in tagged["records"]], ["A", "A", "A", "A", "B"])
        both = self.app.followup_report("2026-10-01", "2026-10-03", tags=["vip", "华东"])
        self.assertEqual([r["contact_id"] for r in both["records"]], ["A", "A", "A", "A"])
        any_tag = self.app.followup_report("2026-10-01", "2026-10-03",
                                           tags=["vip", "missing"], tag_mode="any")
        self.assertEqual([r["contact_id"] for r in any_tag["records"]], ["A", "A", "A", "A", "B"])
        # Organization and tag conditions intersect.
        self.assertEqual(self.app.followup_report("2026-10-01", "2026-10-03",
                                                  organization="Music", tags=["vip"])["records"], [])
        # None and empty list leave tags unrestricted.
        self.assertEqual(self.app.followup_report("2026-10-01", "2026-10-03", tags=None),
                         self.app.followup_report("2026-10-01", "2026-10-03", tags=[]))

    def test_followup_report_empty_and_legacy_data(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        report = fresh.followup_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["records"], [])
        self.assertEqual(report["csv"], "contact_id,name,email,organization,on,note\n")
        self.assertFalse(fresh_root.exists())
        # Contacts but no followups yet: empty records, header-only CSV.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        report = self.app.followup_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["records"], [])
        self.assertEqual(report["csv"], "contact_id,name,email,organization,on,note\n")

    def test_followup_report_uses_current_profile_and_tags_after_update_and_merge(self):
        self.seed_followups()
        self.app.update_contact("A", {"name": "Alice Wang", "organization": "Music"})
        report = self.app.followup_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["records"][0]["name"], "Alice Wang")
        self.assertEqual(report["records"][0]["organization"], "Music")
        # Organization filter follows the current value.
        self.assertEqual(self.app.followup_report("2026-10-01", "2026-10-02",
                                                  organization="books")["records"][0]["contact_id"], "B")
        # Merge: B's followups move to A and display A's current profile and tags.
        self.app.merge_contacts("B", "A")
        merged = self.app.followup_report("2026-10-01", "2026-10-02", tags=["vip"])
        self.assertEqual([r["contact_id"] for r in merged["records"]], ["A"] * 5)
        # B's entry was saved first, so stable ordering places it before A's same-day notes.
        self.assertEqual([r["note"] for r in merged["records"]],
                         ["a first day", "b second day", "a later save", "a earlier save", "a earlier save"])
        self.assertEqual(merged["records"][1]["name"], "Alice Wang")

    def test_followup_report_validates_arguments_without_writing(self):
        self.seed_followups()
        before = self.app.path.read_bytes()
        bad_dates = [None, 5, "", "   ", "2026-1-1", "20261002", "2026-13-01", "2026-02-30"]
        for bad in bad_dates:
            with self.assertRaises(ValueError):
                self.app.followup_report(bad, "2026-10-02")
            with self.assertRaises(ValueError):
                self.app.followup_report("2026-10-01", bad)
        with self.assertRaises(ValueError):
            self.app.followup_report("2026-10-03", "2026-10-02")
        for kwargs in [{"organization": 5}, {"organization": ["x"]},
                       {"tags": ["ok", 1]}, {"tags": "vip"},
                       {"tag_mode": "ALL"}, {"tag_mode": "weird"}]:
            with self.assertRaises(ValueError):
                self.app.followup_report("2026-10-01", "2026-10-02", **kwargs)
        self.assertEqual(self.app.path.read_bytes(), before)
        # Legal leap day is accepted.
        self.assertEqual(self.app.followup_report("2024-02-29", "2024-02-29")["records"], [])
        # Validation also applies when there is no data at all, creating nothing.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.followup_report("2026-02-30", "2026-10-02")
        with self.assertRaises(ValueError):
            fresh.followup_report("2026-10-02", "2026-10-01")
        with self.assertRaises(ValueError):
            fresh.followup_report("2026-10-01", "2026-10-02", tag_mode="weird")
        self.assertFalse((self.root / "fresh").exists())

    def test_cli_followup_report_success_and_failure(self):
        self.seed_followups()
        payload = self.root / "report.json"
        payload.write_text(json.dumps({"start_on": "2026-10-01", "end_on": "2026-10-02"}),
                           encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "followup-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(set(report), {"records", "csv"})
        self.assertEqual(len(report["records"]), 5)
        # Filters pass through; array input behaves like repeated calls.
        payload.write_text(json.dumps([
            {"start_on": "2026-10-01", "end_on": "2026-10-02", "organization": "books"},
            {"start_on": "2026-10-02", "end_on": "2026-10-02", "tags": ["vip", "华东"]},
        ]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "followup-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([r["contact_id"] for r in values[0]["records"]], ["A", "A", "A", "A", "B"])
        self.assertEqual([r["contact_id"] for r in values[1]["records"]], ["A", "A", "A"])
        # Invalid argument: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        payload.write_text(json.dumps({"start_on": "2026-10-02", "end_on": "2026-10-01"}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "followup-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)

    def seed_stage_changes(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.add_opportunity("O1", "A", "Alpha deal")
        self.app.add_opportunity("O2", "B", "Beta deal")
        self.app.add_opportunity("O3", "C", "Gamma deal")
        self.app.add_opportunity("O4", "A", "Delta deal")
        # O1: two changes on the same day keep save order; null-on record excluded.
        self.app.set_stage("O1", "qualified", "2026-10-02")
        self.app.set_stage("O1", "won", "2026-10-02")
        # O2: one in-range change, one out-of-range change.
        self.app.set_stage("O2", "qualified", "2026-10-01")
        self.app.set_stage("O2", "won", "2026-10-05")
        # O3: only a null-date change, so it never appears.
        self.app.set_stage("O3", "qualified")
        # O4: no history at all; nothing is back-filled.

    def test_stage_change_report_content_and_order(self):
        self.seed_stage_changes()
        report = self.app.stage_change_report("2026-10-01", "2026-10-03")
        self.assertEqual(set(report), {"records", "csv"})
        self.assertEqual([(r["opportunity_id"], r["from_stage"], r["to_stage"], r["on"])
                          for r in report["records"]],
                         [("O2", "new", "qualified", "2026-10-01"),
                          ("O1", "new", "qualified", "2026-10-02"),
                          ("O1", "qualified", "won", "2026-10-02")])
        for record in report["records"]:
            self.assertEqual(set(record), {"opportunity_id", "contact_id", "title",
                                           "organization", "from_stage", "to_stage", "on"})
        self.assertEqual(report["records"][1],
                         {"opportunity_id": "O1", "contact_id": "A", "title": "Alpha deal",
                          "organization": "Books", "from_stage": "new",
                          "to_stage": "qualified", "on": "2026-10-02"})
        # Inclusive bounds: a single-day range returns that day's entries.
        single = self.app.stage_change_report("2026-10-02", "2026-10-02")
        self.assertEqual([r["opportunity_id"] for r in single["records"]], ["O1", "O1"])
        # O3's null-date record and O4's missing history produce nothing.
        self.assertNotIn("O3", [r["opportunity_id"] for r in report["records"]])
        self.assertNotIn("O4", [r["opportunity_id"] for r in report["records"]])

    def test_stage_change_report_csv_matches_records(self):
        self.seed_stage_changes()
        report = self.app.stage_change_report("2026-10-01", "2026-10-05")
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["opportunity_id", "contact_id", "title", "organization",
                                   "from_stage", "to_stage", "on"])
        decoded = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual(decoded, report["records"])
        self.assertTrue(report["csv"].endswith("\n"))
        self.assertNotIn("\r", report["csv"])
        self.assertEqual(len(report["csv"].splitlines()), len(report["records"]) + 1)

    def test_stage_change_report_csv_escapes_and_preserves_internal_newlines(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books, \"店\"")
        self.app.add_opportunity("O1", "A", "line one\nline two")
        self.app.set_stage("O1", "qualified", "2026-10-01")
        report = self.app.stage_change_report("2026-10-01", "2026-10-01")
        self.assertIn('"line one\nline two","Books, ""店""",new,qualified,2026-10-01',
                      report["csv"])
        rows = list(csv.reader(io.StringIO(report["csv"]), strict=True))
        self.assertEqual(rows[1][2], "line one\nline two")
        self.assertEqual(rows[1][3], "Books, \"店\"")

    def test_stage_change_report_filters_organization_tags_and_intersection(self):
        self.seed_stage_changes()
        by_org = self.app.stage_change_report("2026-10-01", "2026-10-03", organization=" BOOKS ")
        self.assertEqual([r["opportunity_id"] for r in by_org["records"]], ["O2", "O1", "O1"])
        tagged = self.app.stage_change_report("2026-10-01", "2026-10-03", tags=["VIP"])
        self.assertEqual([r["opportunity_id"] for r in tagged["records"]], ["O2", "O1", "O1"])
        both = self.app.stage_change_report("2026-10-01", "2026-10-03", tags=["vip", "华东"])
        self.assertEqual([r["opportunity_id"] for r in both["records"]], ["O1", "O1"])
        any_tag = self.app.stage_change_report("2026-10-01", "2026-10-03",
                                               tags=["vip", "missing"], tag_mode="any")
        self.assertEqual([r["opportunity_id"] for r in any_tag["records"]], ["O2", "O1", "O1"])
        # Organization and tag conditions intersect.
        self.assertEqual(self.app.stage_change_report("2026-10-01", "2026-10-03",
                                                      organization="Music", tags=["vip"])["records"], [])
        # None and empty list leave tags unrestricted.
        self.assertEqual(self.app.stage_change_report("2026-10-01", "2026-10-03", tags=None),
                         self.app.stage_change_report("2026-10-01", "2026-10-03", tags=[]))

    def test_stage_change_report_empty_and_legacy_data(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        report = fresh.stage_change_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["records"], [])
        self.assertEqual(report["csv"], "opportunity_id,contact_id,title,organization,from_stage,to_stage,on\n")
        self.assertFalse(fresh_root.exists())
        # Contacts and opportunities but no stage history: empty records, header-only CSV.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_opportunity("O1", "A", "Deal")
        report = self.app.stage_change_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["records"], [])
        self.assertEqual(report["csv"], "opportunity_id,contact_id,title,organization,from_stage,to_stage,on\n")

    def test_stage_change_report_uses_current_ownership_profile_and_tags(self):
        self.seed_stage_changes()
        self.app.update_contact("A", {"name": "Alice Wang", "organization": "Music"})
        report = self.app.stage_change_report("2026-10-01", "2026-10-03")
        self.assertEqual(report["records"][1]["organization"], "Music")
        # Organization filter follows the current value.
        self.assertEqual(self.app.stage_change_report("2026-10-01", "2026-10-03",
                                                      organization="books")["records"][0]["opportunity_id"], "O2")
        # Transfer: O1 now belongs to B and displays B's current organization.
        self.app.transfer_opportunities([{"opportunity_id": "O1", "source_contact_id": "A",
                                          "target_contact_id": "B"}])
        moved = self.app.stage_change_report("2026-10-01", "2026-10-03")
        self.assertEqual([r["contact_id"] for r in moved["records"]], ["B", "B", "B"])
        self.assertEqual(moved["records"][1]["organization"], "books")
        # History dates and stages are unchanged and not duplicated.
        self.assertEqual([(r["from_stage"], r["to_stage"], r["on"]) for r in moved["records"]],
                         [("new", "qualified", "2026-10-01"),
                          ("new", "qualified", "2026-10-02"),
                          ("qualified", "won", "2026-10-02")])
        # Merge: B's opportunities move to A and filter by A's current profile and tags.
        self.app.merge_contacts("B", "A")
        merged = self.app.stage_change_report("2026-10-01", "2026-10-03", tags=["vip"])
        self.assertEqual([r["contact_id"] for r in merged["records"]], ["A", "A", "A"])
        self.assertEqual(merged["records"][0]["organization"], "Music")

    def test_stage_change_report_validates_arguments_without_writing(self):
        self.seed_stage_changes()
        before = self.app.path.read_bytes()
        bad_dates = [None, 5, "", "   ", "2026-1-1", "20261002", "2026-13-01", "2026-02-30"]
        for bad in bad_dates:
            with self.assertRaises(ValueError):
                self.app.stage_change_report(bad, "2026-10-02")
            with self.assertRaises(ValueError):
                self.app.stage_change_report("2026-10-01", bad)
        with self.assertRaises(ValueError):
            self.app.stage_change_report("2026-10-03", "2026-10-02")
        for kwargs in [{"organization": 5}, {"organization": ["x"]},
                       {"tags": ["ok", 1]}, {"tags": "vip"},
                       {"tag_mode": "ALL"}, {"tag_mode": "weird"}]:
            with self.assertRaises(ValueError):
                self.app.stage_change_report("2026-10-01", "2026-10-02", **kwargs)
        with self.assertRaises(TypeError):
            self.app.stage_change_report()
        with self.assertRaises(TypeError):
            self.app.stage_change_report("2026-10-01")
        self.assertEqual(self.app.path.read_bytes(), before)
        # Legal leap day is accepted.
        self.assertEqual(self.app.stage_change_report("2024-02-29", "2024-02-29")["records"], [])
        # Validation also applies when there is no data at all, creating nothing.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.stage_change_report("2026-02-30", "2026-10-02")
        with self.assertRaises(ValueError):
            fresh.stage_change_report("2026-10-02", "2026-10-01")
        with self.assertRaises(ValueError):
            fresh.stage_change_report("2026-10-01", "2026-10-02", tag_mode="weird")
        self.assertFalse((self.root / "fresh").exists())

    def test_cli_stage_change_report_success_and_failure(self):
        self.seed_stage_changes()
        payload = self.root / "report.json"
        payload.write_text(json.dumps({"start_on": "2026-10-01", "end_on": "2026-10-02"}),
                           encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "stage-change-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(set(report), {"records", "csv"})
        self.assertEqual(len(report["records"]), 3)
        # Filters pass through; array input behaves like repeated calls.
        payload.write_text(json.dumps([
            {"start_on": "2026-10-01", "end_on": "2026-10-02", "organization": "books"},
            {"start_on": "2026-10-02", "end_on": "2026-10-02", "tags": ["vip", "华东"]},
        ]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "stage-change-report", str(payload)], text=True,
                               capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([r["opportunity_id"] for r in values[0]["records"]], ["O2", "O1", "O1"])
        self.assertEqual([r["opportunity_id"] for r in values[1]["records"]], ["O1", "O1"])
        # Invalid argument: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        payload.write_text(json.dumps({"start_on": "2026-10-02", "end_on": "2026-10-01"}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "stage-change-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)

    def seed_win_cycles(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip"])
        # O1: measured, 30 days across the month boundary.
        self.app.add_opportunity("O1", "A", "Alpha")
        self.app.set_stage("O1", "qualified", "2026-09-01")
        self.app.set_stage("O1", "won", "2026-10-01")
        # O2: measured, 1 day.
        self.app.add_opportunity("O2", "B", "Beta")
        self.app.set_stage("O2", "qualified", "2026-10-01")
        self.app.set_stage("O2", "won", "2026-10-02")
        # O3: the qualified entry has a null date, so the win is unmeasured.
        self.app.add_opportunity("O3", "A", "Gamma")
        self.app.set_stage("O3", "qualified")
        self.app.set_stage("O3", "won", "2026-10-03")
        # O4: null-date win is excluded entirely.
        self.app.add_opportunity("O4", "C", "Delta")
        self.app.set_stage("O4", "qualified", "2026-09-15")
        self.app.set_stage("O4", "won")
        # O5: same-day qualified/won, zero days, just outside the main range.
        self.app.add_opportunity("O5", "A", "Epsilon")
        self.app.set_stage("O5", "qualified", "2026-10-04")
        self.app.set_stage("O5", "won", "2026-10-04")
        # O7: win before the range is excluded.
        self.app.add_opportunity("O7", "D", "Eta")
        self.app.set_stage("O7", "qualified", "2026-09-20")
        self.app.set_stage("O7", "won", "2026-09-25")
        # O8: start more than two years earlier across years and a leap day.
        self.app.add_opportunity("O8", "C", "Theta")
        self.app.set_stage("O8", "qualified", "2024-02-29")
        self.app.set_stage("O8", "won", "2026-10-01")

    def test_win_cycle_report_counts_and_calendar_durations(self):
        self.seed_win_cycles()
        leap_span = (date(2026, 10, 1) - date(2024, 2, 29)).days
        # Amounts and reminders never affect durations.
        self.app.set_opportunity_amount("O1", "1200.50")
        self.app.set_reminder("A", "2099-01-01", "nudge")
        report = self.app.win_cycle_report("2026-10-01", "2026-10-03")
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        self.assertEqual(report["total"],
                         {"won": 4, "measured": 3, "unmeasured": 1,
                          "days": 31 + leap_span, "average": "%.2f" % ((31 + leap_span) / 3)})
        self.assertEqual(set(report["total"]),
                         {"won", "measured", "unmeasured", "days", "average"})
        # Books/books fold into one casefold group; display name is the smallest
        # original value; Music sorts after it.
        self.assertEqual([row["organization"] for row in report["organizations"]],
                         ["Books", "Music"])
        books, music = report["organizations"]
        self.assertEqual(books, {"organization": "Books", "won": 3, "measured": 2,
                                 "unmeasured": 1, "days": 31, "average": "15.50"})
        self.assertEqual(music, {"organization": "Music", "won": 1, "measured": 1,
                                 "unmeasured": 0, "days": leap_span,
                                 "average": "%d.00" % leap_span})
        # Inclusive single-day bounds.
        single = self.app.win_cycle_report("2026-10-02", "2026-10-02")
        self.assertEqual(single["total"],
                         {"won": 1, "measured": 1, "unmeasured": 0, "days": 1,
                          "average": "1.00"})
        self.assertEqual([row["organization"] for row in single["organizations"]], ["books"])
        # Same-day win measures zero days; the 09-25 win stays out of range.
        zero_day = self.app.win_cycle_report("2026-10-04", "2026-10-04")
        self.assertEqual(zero_day["total"],
                         {"won": 1, "measured": 1, "unmeasured": 0, "days": 0,
                          "average": "0.00"})
        outside = self.app.win_cycle_report("2026-09-26", "2026-09-30")
        self.assertEqual(outside["total"]["won"], 0)

    def test_win_cycle_report_csv_matches_organizations(self):
        self.seed_win_cycles()
        report = self.app.win_cycle_report("2026-10-01", "2026-10-03")
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0],
                         ["organization", "won", "measured", "unmeasured", "days", "average"])
        decoded = []
        for row in rows[1:]:
            decoded.append({"organization": row[0], "won": int(row[1]), "measured": int(row[2]),
                            "unmeasured": int(row[3]), "days": int(row[4]),
                            "average": row[5] if row[5] else None})
        self.assertEqual(decoded, report["organizations"])
        self.assertTrue(report["csv"].endswith("\n"))
        self.assertNotIn("\r", report["csv"])
        self.assertEqual(len(report["csv"].splitlines()), len(report["organizations"]) + 1)
        # A range containing only the unmeasured win renders null average as an
        # empty field in both the row and the totals.
        only_unmeasured = self.app.win_cycle_report("2026-10-03", "2026-10-03")
        self.assertIsNone(only_unmeasured["total"]["average"])
        self.assertEqual(
            only_unmeasured["csv"],
            "organization,won,measured,unmeasured,days,average\nBooks,1,0,1,0,\n")

    def test_win_cycle_report_csv_escapes_organization(self):
        self.app.add_contact("A", "Alice", "a@example.test", 'Books, "店"')
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_stage("O1", "qualified", "2026-10-01")
        self.app.set_stage("O1", "won", "2026-10-02")
        report = self.app.win_cycle_report("2026-10-01", "2026-10-02")
        rows = list(csv.reader(io.StringIO(report["csv"]), strict=True))
        self.assertEqual(rows[1][0], 'Books, "店"')
        self.assertEqual(rows[1][1:], ["1", "1", "0", "1", "1.00"])

    def test_win_cycle_report_filters_organization_tags_and_intersection(self):
        self.seed_win_cycles()
        by_org = self.app.win_cycle_report("2026-10-01", "2026-10-03", organization=" books ")
        self.assertEqual(by_org["total"],
                         {"won": 3, "measured": 2, "unmeasured": 1, "days": 31,
                          "average": "15.50"})
        tagged = self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=["VIP"])
        # A owns O1 and O3; B also carries vip and owns O2.
        self.assertEqual(tagged["total"],
                         {"won": 3, "measured": 2, "unmeasured": 1, "days": 31,
                          "average": "15.50"})
        both = self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=["vip", "华东"])
        self.assertEqual(both["total"],
                         {"won": 2, "measured": 1, "unmeasured": 1, "days": 30,
                          "average": "30.00"})
        any_tag = self.app.win_cycle_report("2026-10-01", "2026-10-03",
                                            tags=["vip", "missing"], tag_mode="any")
        self.assertEqual(any_tag["total"]["won"], 3)
        # Organization and tag conditions intersect: Music has no vip contacts.
        self.assertEqual(self.app.win_cycle_report(
            "2026-10-01", "2026-10-03", organization="Music", tags=["vip"])["total"]["won"], 0)
        # None and empty list leave tags unrestricted.
        self.assertEqual(self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=None),
                         self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=[]))

    def test_win_cycle_report_empty_and_legacy_data(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        report = fresh.win_cycle_report("2026-10-01", "2026-10-02")
        self.assertEqual(report["total"],
                         {"won": 0, "measured": 0, "unmeasured": 0, "days": 0, "average": None})
        self.assertEqual(report["organizations"], [])
        self.assertEqual(report["csv"],
                         "organization,won,measured,unmeasured,days,average\n")
        self.assertFalse(fresh_root.exists())
        # A legacy deal already sitting at won without history is not a win event.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps({
            "contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                               "organization": "Old"}},
            "opportunities": {"O9": {"opportunity_id": "O9", "contact_id": "L",
                                     "title": "Legacy", "stage": "won"}}}),
            encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        report = legacy.win_cycle_report("2026-10-01", "2026-10-03")
        self.assertEqual(report["total"]["won"], 0)
        self.assertIsNone(report["total"]["average"])
        self.assertEqual(report["organizations"], [])
        self.assertEqual(report["csv"],
                         "organization,won,measured,unmeasured,days,average\n")

    def test_win_cycle_report_counts_each_opportunity_once(self):
        # Hand-crafted histories cannot arise through the legal transition API.
        legacy_root = self.root / "crafted"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps({
            "contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                               "organization": "Old"}},
            "opportunities": {"O1": {"opportunity_id": "O1", "contact_id": "L",
                                     "title": "Twice", "stage": "won"},
                              "O2": {"opportunity_id": "O2", "contact_id": "L",
                                     "title": "Null after", "stage": "won"}},
            "stage_history": {
                "O1": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-10-01"},
                       {"from_stage": "won", "to_stage": "qualified", "on": "2026-10-02"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-10-03"}],
                "O2": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-10-01"},
                       {"from_stage": "won", "to_stage": "won", "on": None}]}}),
            encoding="utf-8")
        app = ContactFlow(legacy_root)
        report = app.win_cycle_report("2026-10-01", "2026-10-03")
        # O1 takes the last saved winning event and the qualified entry just before
        # it (1 day); O2 keeps its dated win even though a null win follows (30).
        self.assertEqual(report["total"],
                         {"won": 2, "measured": 2, "unmeasured": 0, "days": 31,
                          "average": "15.50"})

    def test_win_cycle_report_average_rounds_half_up(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        # Eight measured wins totalling one day: 1/8 = 0.125 -> "0.13" (half-up,
        # never Python float banker's rounding).
        self.app.add_opportunity("O1", "A", "one")
        self.app.set_stage("O1", "qualified", "2026-10-01")
        self.app.set_stage("O1", "won", "2026-10-02")
        for index in range(7):
            opportunity_id = "O%d" % (index + 2)
            self.app.add_opportunity(opportunity_id, "A", "same")
            self.app.set_stage(opportunity_id, "qualified", "2026-10-02")
            self.app.set_stage(opportunity_id, "won", "2026-10-02")
        report = self.app.win_cycle_report("2026-10-01", "2026-10-03")
        self.assertEqual(report["total"],
                         {"won": 8, "measured": 8, "unmeasured": 0, "days": 1,
                          "average": "0.13"})

    def test_win_cycle_report_uses_current_ownership_profile_and_tags(self):
        self.seed_win_cycles()
        leap_span = (date(2026, 10, 1) - date(2024, 2, 29)).days
        # Profile updates regroup the wins immediately.
        self.app.update_contact("A", {"organization": "Music"})
        report = self.app.win_cycle_report("2026-10-01", "2026-10-03")
        self.assertEqual([row["organization"] for row in report["organizations"]],
                         ["books", "Music"])
        music = next(row for row in report["organizations"]
                     if row["organization"] == "Music")
        self.assertEqual(music["days"], 30 + leap_span)
        self.assertEqual(music["unmeasured"], 1)
        # Transfer O1 to C: it now follows C's profile and loses A's vip tag.
        self.app.transfer_opportunities([{"opportunity_id": "O1", "source_contact_id": "A",
                                          "target_contact_id": "C"}])
        tagged = self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=["vip"])
        self.assertEqual(tagged["total"],
                         {"won": 2, "measured": 1, "unmeasured": 1, "days": 1,
                          "average": "1.00"})
        # History dates and stages are untouched.
        self.assertEqual([(entry["from_stage"], entry["to_stage"], entry["on"])
                          for entry in self.app.stage_history("O1")],
                         [("new", "qualified", "2026-09-01"),
                          ("qualified", "won", "2026-10-01")])
        # Merge B into A: O2 follows A's merged tags and current organization.
        self.app.merge_contacts("B", "A")
        merged = self.app.win_cycle_report("2026-10-01", "2026-10-03", tags=["华东"])
        self.assertEqual(merged["total"],
                         {"won": 2, "measured": 1, "unmeasured": 1, "days": 1,
                          "average": "1.00"})
        full = self.app.win_cycle_report("2026-10-01", "2026-10-03")
        # Reopening the root recomputes from the same current ownership.
        self.assertEqual(ContactFlow(self.root).win_cycle_report("2026-10-01", "2026-10-03"),
                         full)

    def test_win_cycle_report_validates_arguments_without_writing(self):
        self.seed_win_cycles()
        before = self.app.path.read_bytes()
        bad_dates = [None, 5, "", "   ", "2026-1-1", "20261002", "2026-13-01", "2026-02-30"]
        for bad in bad_dates:
            with self.assertRaises(ValueError):
                self.app.win_cycle_report(bad, "2026-10-02")
            with self.assertRaises(ValueError):
                self.app.win_cycle_report("2026-10-01", bad)
        with self.assertRaises(ValueError):
            self.app.win_cycle_report("2026-10-03", "2026-10-02")
        for kwargs in [{"organization": 5}, {"organization": ["x"]},
                       {"tags": ["ok", 1]}, {"tags": "vip"},
                       {"tag_mode": "ALL"}, {"tag_mode": "weird"}]:
            with self.assertRaises(ValueError):
                self.app.win_cycle_report("2026-10-01", "2026-10-02", **kwargs)
        with self.assertRaises(TypeError):
            self.app.win_cycle_report()
        with self.assertRaises(TypeError):
            self.app.win_cycle_report("2026-10-01")
        self.assertEqual(self.app.path.read_bytes(), before)
        # A legal leap day is accepted.
        self.assertEqual(self.app.win_cycle_report("2024-02-29", "2024-02-29")["total"]["won"], 0)
        # Validation also applies when there is no data at all, creating nothing.
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.win_cycle_report("2026-02-30", "2026-10-02")
        with self.assertRaises(ValueError):
            fresh.win_cycle_report("2026-10-02", "2026-10-01")
        with self.assertRaises(ValueError):
            fresh.win_cycle_report("2026-10-01", "2026-10-02", tag_mode="weird")
        self.assertFalse((self.root / "fresh").exists())

    def test_cli_win_cycle_report_success_and_failure(self):
        self.seed_win_cycles()
        payload = self.root / "report.json"
        payload.write_text(json.dumps({"start_on": "2026-10-01", "end_on": "2026-10-03"}),
                           encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "win-cycle-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual(set(report), {"total", "organizations", "csv"})
        self.assertEqual(report["total"]["won"], 4)
        # Filters pass through; array input behaves like repeated calls.
        payload.write_text(json.dumps([
            {"start_on": "2026-10-01", "end_on": "2026-10-02", "organization": "books"},
            {"start_on": "2026-10-03", "end_on": "2026-10-03", "tags": ["vip", "华东"]},
        ]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "win-cycle-report", str(payload)], text=True,
                               capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual(values[0]["total"]["won"], 2)
        self.assertEqual(values[1]["total"]["won"], 1)
        self.assertEqual(values[1]["total"]["unmeasured"], 1)
        # Invalid argument: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        payload.write_text(json.dumps({"start_on": "2026-10-02", "end_on": "2026-10-01"}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "win-cycle-report", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        json.loads(failed.stderr)
        self.assertEqual(self.app.path.read_bytes(), before)

    def seed_inactive(self):
        # A: last on/before 2026-10-05 is the last saved entry on 2026-10-02 (idle 3);
        # B: last 2026-09-01 (idle 34); C: recent 2026-10-04 (idle 1);
        # D: only a future record; E: never followed up; 陈: leap-day 2024-02-29.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.add_contact("E", "Eve", "e@example.test", "Books")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Books")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.follow_up("A", "2026-09-01", "old")
        self.app.follow_up("A", "2026-10-02", "earlier save")
        self.app.follow_up("A", "2026-10-02", "later save")
        self.app.follow_up("A", "2099-01-01", "future")
        self.app.follow_up("B", "2026-09-01", "b note")
        self.app.follow_up("C", "2026-10-04", "recent")
        self.app.follow_up("D", "2099-01-01", "future only")
        self.app.follow_up("陈", "2024-02-29", "leap")
        # Opportunities and reminders never affect inclusion.
        self.app.add_opportunity("O1", "E", "Deal")
        self.app.set_reminder("E", "2026-10-01", "nudge")

    def test_inactive_contacts_content_threshold_and_ordering(self):
        self.seed_inactive()
        result = self.app.inactive_contacts("2026-10-05", 3)
        # No qualifying records first (D, E by id), then idle descending, id ascending.
        self.assertEqual([(r["contact"]["contact_id"], r["idle_days"]) for r in result],
                         [("D", None), ("E", None), ("陈", (date(2026, 10, 5) - date(2024, 2, 29)).days),
                          ("B", 34), ("A", 3)])
        for row in result:
            self.assertEqual(set(row), {"contact", "last_followup", "idle_days"})
        # Null rows carry the current contact and no record.
        self.assertEqual(result[0]["contact"],
                         {"contact_id": "D", "name": "Dan", "email": "d@example.test", "organization": "Books"})
        self.assertIsNone(result[0]["last_followup"])
        self.assertIsNone(result[0]["idle_days"])
        # Latest same-day entry is the last one in save order; future records are ignored.
        a_row = next(r for r in result if r["contact"]["contact_id"] == "A")
        self.assertEqual(a_row["last_followup"],
                         {"contact_id": "A", "on": "2026-10-02", "note": "later save"})
        b_row = next(r for r in result if r["contact"]["contact_id"] == "B")
        self.assertEqual(b_row["last_followup"],
                         {"contact_id": "B", "on": "2026-09-01", "note": "b note"})
        # The threshold is inclusive: exactly 3 days still matches; 4 excludes A.
        self.assertIn("A", [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 3)])
        self.assertNotIn("A", [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 4)])
        # At an earlier cutoff the never/only-future rows still lead; C (idle 0) is excluded.
        earlier = [r["contact"]["contact_id"]
                   for r in ContactFlow(self.root).inactive_contacts("2026-10-04", 1)]
        self.assertEqual(earlier[0:2], ["D", "E"])
        self.assertNotIn("C", earlier)

    def test_inactive_contacts_filters_organization_tags_and_intersection(self):
        self.seed_inactive()
        self.assertEqual(
            [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 3, organization="books")],
            ["D", "E", "陈", "B", "A"])
        self.assertEqual(
            [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 3, organization=" music ")],
            [])
        self.assertEqual(
            [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 1, tags=["vip"])],
            ["B", "A"])
        self.assertEqual(
            [r["contact"]["contact_id"] for r in self.app.inactive_contacts("2026-10-05", 1, tags=["vip", "华东"])],
            ["A"])
        self.assertEqual(
            [r["contact"]["contact_id"]
             for r in self.app.inactive_contacts("2026-10-05", 1, tags=["vip", "missing"], tag_mode="any")],
            ["B", "A"])
        # Organization and tag conditions intersect.
        self.assertEqual(
            self.app.inactive_contacts("2026-10-05", 1, organization="Music", tags=["vip"]), [])
        self.assertEqual(
            [r["contact"]["contact_id"]
             for r in self.app.inactive_contacts("2026-10-05", 1, organization="Music", tags=[])],
            ["C"])

    def test_inactive_contacts_empty_legacy_and_readonly(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.inactive_contacts("2026-10-05", 3), [])
        self.assertFalse(fresh_root.exists())
        # Legacy document without followups or tags: everyone counts as never followed up.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"},
                          "M": {"contact_id": "M", "name": "Mai", "email": "m@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        result = ContactFlow(legacy_root).inactive_contacts("2026-10-05", 1)
        self.assertEqual([(r["contact"]["contact_id"], r["last_followup"], r["idle_days"]) for r in result],
                         [("L", None, None), ("M", None, None)])
        # Successful queries never rewrite the file.
        self.seed_inactive()
        before = self.app.path.read_bytes()
        self.app.inactive_contacts("2026-10-05", 1)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_inactive_contacts_validates_arguments_even_when_empty(self):
        for kwargs in [
            {"as_of": None, "inactive_days": 1},
            {"as_of": 5, "inactive_days": 1},
            {"as_of": "", "inactive_days": 1},
            {"as_of": "   ", "inactive_days": 1},
            {"as_of": "2026-02-30", "inactive_days": 1},
            {"as_of": "2026-13-01", "inactive_days": 1},
            {"as_of": "20261005", "inactive_days": 1},
            {"as_of": "2026-10-05", "inactive_days": None},
            {"as_of": "2026-10-05", "inactive_days": 0},
            {"as_of": "2026-10-05", "inactive_days": -3},
            {"as_of": "2026-10-05", "inactive_days": True},
            {"as_of": "2026-10-05", "inactive_days": False},
            {"as_of": "2026-10-05", "inactive_days": 1.0},
            {"as_of": "2026-10-05", "inactive_days": "3"},
            {"as_of": "2026-10-05", "inactive_days": 1, "organization": 5},
            {"as_of": "2026-10-05", "inactive_days": 1, "organization": ["Books"]},
            {"as_of": "2026-10-05", "inactive_days": 1, "tags": "vip"},
            {"as_of": "2026-10-05", "inactive_days": 1, "tags": ["ok", 1]},
            {"as_of": "2026-10-05", "inactive_days": 1, "tags": ["  "]},
            {"as_of": "2026-10-05", "inactive_days": 1, "tag_mode": "ALL"},
            {"as_of": "2026-10-05", "inactive_days": 1, "tag_mode": "weird"},
        ]:
            with self.assertRaises(ValueError):
                self.app.inactive_contacts(**kwargs)
        # The same validation runs against a missing data file and creates nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.inactive_contacts("2026-02-30", 1)
        with self.assertRaises(ValueError):
            fresh.inactive_contacts("2026-10-05", True)
        with self.assertRaises(ValueError):
            fresh.inactive_contacts("2026-10-05", 1, tag_mode="weird")
        self.assertFalse(fresh_root.exists())
        # Missing required arguments are a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            self.app.inactive_contacts("2026-10-05")
        with self.assertRaises(TypeError):
            self.app.inactive_contacts(inactive_days=3)
        # A legal leap day parses.
        self.assertEqual(fresh.inactive_contacts("2024-02-29", 1), [])

    def test_inactive_contacts_recomputes_after_update_import_and_merge(self):
        self.seed_inactive()
        # Profile update: organization filtering and the returned contact follow current data.
        self.app.update_contact("B", {"organization": "Music"})
        music = self.app.inactive_contacts("2026-10-05", 3, organization="music")
        self.assertEqual([r["contact"]["contact_id"] for r in music], ["B"])
        self.assertEqual(music[0]["contact"]["organization"], "Music")
        self.assertEqual(
            [r["contact"]["contact_id"]
             for r in self.app.inactive_contacts("2026-10-05", 3, organization="books")],
            ["D", "E", "陈", "A"])
        # Imported followups count on the next query; reopening reads the current file fresh.
        csv_path = self.root / "followups.csv"
        csv_path.write_text(
            "contact_id,on,note\nE,2026-10-04,imported recent\n", encoding="utf-8")
        self.app.import_followups(str(csv_path))
        reopened = ContactFlow(self.root)
        rows = {r["contact"]["contact_id"]: r for r in reopened.inactive_contacts("2026-10-05", 1)}
        # E now has a record with idle 1 (threshold inclusive), so it leaves the null group.
        self.assertEqual(rows["E"]["idle_days"], 1)
        self.assertEqual(rows["E"]["last_followup"]["note"], "imported recent")
        self.assertNotIn("E", [r["contact"]["contact_id"]
                               for r in reopened.inactive_contacts("2026-10-05", 2)])
        # Merge: B's followups move under A and B no longer appears; tags union.
        self.app.merge_contacts("B", "A")
        merged = ContactFlow(self.root).inactive_contacts("2026-10-05", 1, tags=["vip"])
        self.assertEqual([r["contact"]["contact_id"] for r in merged], ["A"])
        a = merged[0]
        self.assertEqual(a["last_followup"]["on"], "2026-10-02")
        self.assertNotIn("B", [r["contact"]["contact_id"]
                               for r in ContactFlow(self.root).inactive_contacts("2026-10-05", 1)])

    def test_cli_inactive_contacts_success_and_failure(self):
        self.seed_inactive()
        payload = self.root / "inactive.json"
        payload.write_text(json.dumps({"as_of": " 2026-10-05 ", "inactive_days": 3}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([r["contact"]["contact_id"] for r in json.loads(ok.stdout)],
                         ["D", "E", "陈", "B", "A"])
        # Array input behaves like repeated calls.
        payload.write_text(json.dumps([
            {"as_of": "2026-10-05", "inactive_days": 3, "tags": ["vip"]},
            {"as_of": "2026-10-05", "inactive_days": 100, "organization": "Music"},
        ]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([r["contact"]["contact_id"] for r in values[0]], ["B", "A"])
        self.assertEqual(values[1], [])
        # Invalid day count: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        payload.write_text(json.dumps({"as_of": "2026-10-05", "inactive_days": True}), encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing required argument is also exit 2 via the standard error envelope.
        payload.write_text(json.dumps({"as_of": "2026-10-05"}), encoding="utf-8")
        missing = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # A query over a nonexistent root returns [] and creates nothing.
        empty = self.root / "empty"
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(quiet.returncode, 2)  # missing inactive_days
        payload.write_text(json.dumps({"as_of": "2026-10-05", "inactive_days": 3}), encoding="utf-8")
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "inactive-contacts", str(payload)], text=True, capture_output=True)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty.exists())

    def seed_stalled(self):
        # Cutoff used by the tests: 2026-11-04.
        # A (Books, vip+华东): O1 qualified 2026-09-25 (age 40), O10 qualified
        # 2026-10-05 (age 30, boundary), O6 still new, O8 qualified in the future.
        # B (Books, vip): O2 qualified with a null date (unknown), O7 won (excluded).
        # C (Music): O3 qualified 2026-10-20 (age 15).
        # D (Books): O4 imported directly qualified with no history (unknown).
        # 陈 (Books): O5 qualified on the 2024 leap day (oldest dated start).
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Books")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Books")
        self.app.set_tags("A", ["VIP", "华东"])
        self.app.set_tags("B", ["vip"])
        self.app.add_opportunity("O1", "A", "First")
        self.app.set_stage("O1", "qualified", on="2026-09-25")
        self.app.add_opportunity("O10", "A", "Boundary")
        self.app.set_stage("O10", "qualified", on="2026-10-05")
        self.app.add_opportunity("O6", "A", "Still new")
        self.app.add_opportunity("O8", "A", "Future entry")
        self.app.set_stage("O8", "qualified", on="2026-11-10")
        self.app.add_opportunity("O2", "B", "Null date")
        self.app.set_stage("O2", "qualified")
        self.app.add_opportunity("O7", "B", "Won deal")
        self.app.set_stage("O7", "qualified", on="2026-10-01")
        self.app.set_stage("O7", "won", on="2026-10-15")
        self.app.add_opportunity("O3", "C", "Music deal")
        self.app.set_stage("O3", "qualified", on="2026-10-20")
        # O4 is imported directly as qualified, with no stage history back-filled.
        csv_path = self.root / "opportunities.csv"
        csv_path.write_text(
            "opportunity_id,contact_id,title,stage\nO4,D,Imported deal,qualified\n",
            encoding="utf-8")
        self.app.import_opportunities(str(csv_path))
        self.app.add_opportunity("O5", "陈", "Leap start")
        self.app.set_stage("O5", "qualified", on="2024-02-29")
        # Followups, reminders and amounts never affect inclusion.
        self.app.follow_up("C", "2026-11-03", "fresh contact")
        self.app.set_reminder("D", "2026-10-01", "nudge")
        self.app.set_opportunity_amount("O1", "880.00")

    def test_stalled_opportunities_content_threshold_and_ordering(self):
        self.seed_stalled()
        leap_age = (date(2026, 11, 4) - date(2024, 2, 29)).days
        result = self.app.stalled_opportunities(" 2026-11-04 ", 10)
        # Unknown starts first (O2, O4 by id), then ages descending, id ascending.
        self.assertEqual([(r["opportunity"]["opportunity_id"], r["entered_on"], r["age_days"])
                          for r in result],
                         [("O2", None, None), ("O4", None, None),
                          ("O5", "2024-02-29", leap_age),
                          ("O1", "2026-09-25", 40), ("O10", "2026-10-05", 30),
                          ("O3", "2026-10-20", 15)])
        for row in result:
            self.assertEqual(set(row), {"opportunity", "contact", "entered_on", "age_days"})
        # The opportunity is the full current object (amount kept, no back-fill elsewhere).
        self.assertEqual(result[2]["opportunity"],
                         {"opportunity_id": "O5", "contact_id": "陈", "title": "Leap start",
                          "stage": "qualified"})
        o1 = next(r for r in result if r["opportunity"]["opportunity_id"] == "O1")
        self.assertEqual(o1["opportunity"],
                         {"opportunity_id": "O1", "contact_id": "A", "title": "First",
                          "stage": "qualified", "amount": "880.00"})
        self.assertEqual(o1["contact"],
                         {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                          "organization": "Books"})
        # Null-start rows carry nulls and the current objects.
        self.assertIsNone(result[0]["entered_on"])
        self.assertIsNone(result[0]["age_days"])
        self.assertEqual(result[0]["opportunity"],
                         {"opportunity_id": "O2", "contact_id": "B", "title": "Null date",
                          "stage": "qualified"})
        self.assertEqual(result[0]["contact"],
                         {"contact_id": "B", "name": "Bob", "email": "b@example.test",
                          "organization": "Books"})
        # The threshold is inclusive: O10 is exactly 30 days old.
        ids_30 = [r["opportunity"]["opportunity_id"]
                  for r in self.app.stalled_opportunities("2026-11-04", 30)]
        self.assertIn("O10", ids_30)
        ids_31 = [r["opportunity"]["opportunity_id"]
                  for r in self.app.stalled_opportunities("2026-11-04", 31)]
        self.assertNotIn("O10", ids_31)
        # O3 (15 days) drops out at 16; the unknown-start rows always stay.
        ids_16 = [r["opportunity"]["opportunity_id"]
                  for r in self.app.stalled_opportunities("2026-11-04", 16)]
        self.assertNotIn("O3", ids_16)
        self.assertIn("O2", ids_16)
        # The future qualified entry and the non-qualified deals never appear.
        for excluded in ("O6", "O7", "O8"):
            self.assertNotIn(excluded, [r["opportunity"]["opportunity_id"] for r in result])
        # A cutoff after the leap-day start but before the other dated starts
        # keeps the unknown starts plus the one aged leap-day deal.
        early = self.app.stalled_opportunities("2026-01-01", 1)
        self.assertEqual([(r["opportunity"]["opportunity_id"], r["age_days"]) for r in early],
                         [("O2", None), ("O4", None),
                          ("O5", (date(2026, 1, 1) - date(2024, 2, 29)).days)])
        # Same-day start is age 0, so a threshold of 1 excludes it (but nulls stay).
        same_day = self.app.stalled_opportunities("2026-10-20", 1)
        self.assertEqual([r["opportunity"]["opportunity_id"] for r in same_day],
                         ["O2", "O4", "O5", "O1", "O10"])

    def test_stalled_opportunities_uses_last_saved_qualified_entry(self):
        # Legacy history shapes that the normal transition API cannot create are
        # stored verbatim: the start is the last saved entry into qualified.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps({
            "contacts": {
                "A": {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                      "organization": "Books"},
                "B": {"contact_id": "B", "name": "Bob", "email": "b@example.test",
                      "organization": "Books"}},
            "opportunities": {
                "P1": {"opportunity_id": "P1", "contact_id": "A", "title": "Re-entered",
                       "stage": "qualified"},
                "P2": {"opportunity_id": "P2", "contact_id": "A", "title": "Last null",
                       "stage": "qualified"},
                "P3": {"opportunity_id": "P3", "contact_id": "B", "title": "Moved out",
                       "stage": "won"}},
            "stage_history": {
                # Two qualified entries: the last dated one (2026-09-15) is the start.
                "P1": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "lost", "to_stage": "qualified", "on": "2026-09-15"}],
                # A dated qualified entry followed by a null-date one: unknown start.
                "P2": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "lost", "to_stage": "qualified", "on": None}],
                # Currently won: qualified history exists but the deal is excluded.
                "P3": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-10-01"}]}},
        ), encoding="utf-8")
        result = ContactFlow(legacy_root).stalled_opportunities("2026-11-04", 10)
        self.assertEqual([(r["opportunity"]["opportunity_id"], r["entered_on"], r["age_days"])
                          for r in result],
                         [("P2", None, None),
                          ("P1", "2026-09-15", (date(2026, 11, 4) - date(2026, 9, 15)).days)])

    def test_stalled_opportunities_filters_organization_tags_and_intersection(self):
        self.seed_stalled()
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities("2026-11-04", 10, organization="books")],
            ["O2", "O4", "O5", "O1", "O10"])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities("2026-11-04", 10, organization=" music ")],
            ["O3"])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities("2026-11-04", 10, tags=["vip"])],
            ["O2", "O1", "O10"])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities("2026-11-04", 10, tags=["vip", "华东"])],
            ["O1", "O10"])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities(
                 "2026-11-04", 10, tags=["vip", "华东"], tag_mode="any")],
            ["O2", "O1", "O10"])
        # Organization and tag conditions intersect.
        self.assertEqual(
            self.app.stalled_opportunities("2026-11-04", 10, organization="Music", tags=["vip"]),
            [])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities(
                 "2026-11-04", 10, organization="Music", tags=[])],
            ["O3"])
        self.assertEqual(
            self.app.stalled_opportunities("2026-11-04", 10, tags=None),
            self.app.stalled_opportunities("2026-11-04", 10, tags=[]))

    def test_stalled_opportunities_empty_legacy_and_readonly(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.stalled_opportunities("2026-11-04", 10), [])
        self.assertFalse(fresh_root.exists())
        # A legacy document with no opportunities/history/tags collections: empty result.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"L": {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                                "organization": "Old"}}}), encoding="utf-8")
        self.assertEqual(ContactFlow(legacy_root).stalled_opportunities("2026-11-04", 1), [])
        # Successful and rejected queries never rewrite the file or create directories.
        self.seed_stalled()
        before = self.app.path.read_bytes()
        self.app.stalled_opportunities("2026-11-04", 10)
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.stalled_opportunities("bad", 10)
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_stalled_opportunities_validates_arguments_even_when_empty(self):
        for kwargs in [
            {"as_of": None, "stalled_days": 10},
            {"as_of": 5, "stalled_days": 10},
            {"as_of": "", "stalled_days": 10},
            {"as_of": "   ", "stalled_days": 10},
            {"as_of": "2026-02-30", "stalled_days": 10},
            {"as_of": "2026-13-01", "stalled_days": 10},
            {"as_of": "20261104", "stalled_days": 10},
            {"as_of": "2026-11-04", "stalled_days": None},
            {"as_of": "2026-11-04", "stalled_days": 0},
            {"as_of": "2026-11-04", "stalled_days": -3},
            {"as_of": "2026-11-04", "stalled_days": True},
            {"as_of": "2026-11-04", "stalled_days": False},
            {"as_of": "2026-11-04", "stalled_days": 1.0},
            {"as_of": "2026-11-04", "stalled_days": "10"},
            {"as_of": "2026-11-04", "stalled_days": 10, "organization": 5},
            {"as_of": "2026-11-04", "stalled_days": 10, "organization": ["Books"]},
            {"as_of": "2026-11-04", "stalled_days": 10, "tags": "vip"},
            {"as_of": "2026-11-04", "stalled_days": 10, "tags": ["ok", 1]},
            {"as_of": "2026-11-04", "stalled_days": 10, "tags": ["  "]},
            {"as_of": "2026-11-04", "stalled_days": 10, "tag_mode": "ALL"},
            {"as_of": "2026-11-04", "stalled_days": 10, "tag_mode": "weird"},
        ]:
            with self.assertRaises(ValueError):
                self.app.stalled_opportunities(**kwargs)
        # The same validation runs against a missing data file and creates nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.stalled_opportunities("2026-02-30", 10)
        with self.assertRaises(ValueError):
            fresh.stalled_opportunities("2026-11-04", True)
        with self.assertRaises(ValueError):
            fresh.stalled_opportunities("2026-11-04", 10, tag_mode="weird")
        self.assertFalse(fresh_root.exists())
        # Missing required arguments are a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            self.app.stalled_opportunities("2026-11-04")
        with self.assertRaises(TypeError):
            self.app.stalled_opportunities(stalled_days=10)
        # A legal leap day parses against an empty store.
        self.assertEqual(fresh.stalled_opportunities("2024-02-29", 1), [])

    def test_stalled_opportunities_recomputes_after_profile_transfer_and_merge(self):
        self.seed_stalled()
        # Reopening the root recomputes from the current file.
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in ContactFlow(self.root).stalled_opportunities("2026-11-04", 10)],
            ["O2", "O4", "O5", "O1", "O10", "O3"])
        # Profile update: B moves to Music and its null-start deal is found there.
        self.app.update_contact("B", {"organization": "Music"})
        music = self.app.stalled_opportunities("2026-11-04", 10, organization="music")
        self.assertEqual([r["opportunity"]["opportunity_id"] for r in music], ["O2", "O3"])
        self.assertEqual(music[0]["contact"]["organization"], "Music")
        # Tag changes take effect immediately.
        self.app.set_tags("D", ["vip"])
        self.assertEqual(
            [r["opportunity"]["opportunity_id"]
             for r in self.app.stalled_opportunities("2026-11-04", 10, tags=["vip"])],
            ["O2", "O4", "O1", "O10"])
        # Transfer O3 (Music) to A: it now belongs to Books and the vip group.
        self.app.transfer_opportunities(
            [{"opportunity_id": "O3", "source_contact_id": "C", "target_contact_id": "A"}])
        transferred = self.app.stalled_opportunities("2026-11-04", 10, tags=["vip"])
        rows = {r["opportunity"]["opportunity_id"]: r for r in transferred}
        self.assertIn("O3", rows)
        self.assertEqual(rows["O3"]["contact"]["contact_id"], "A")
        # Moving O1 out of qualified excludes it; its history is untouched.
        self.app.set_stage("O1", "won", on="2026-11-01")
        after_win = self.app.stalled_opportunities("2026-11-04", 10)
        self.assertNotIn("O1", [r["opportunity"]["opportunity_id"] for r in after_win])
        self.assertEqual(ContactFlow(self.root).stage_history("O1")[-1]["to_stage"], "won")
        # Merge B into A: B's null-start O2 follows the current owner and tags.
        self.app.merge_contacts("B", "A")
        merged = ContactFlow(self.root).stalled_opportunities("2026-11-04", 10, tags=["华东"])
        merged_rows = {r["opportunity"]["opportunity_id"]: r for r in merged}
        self.assertIn("O2", merged_rows)
        self.assertEqual(merged_rows["O2"]["contact"]["contact_id"], "A")
        self.assertNotIn("B", {r["contact"]["contact_id"] for r in
                               ContactFlow(self.root).stalled_opportunities("2026-11-04", 10)})

    def test_cli_stalled_opportunities_success_and_failure(self):
        self.seed_stalled()
        payload = self.root / "stalled.json"
        payload.write_text(json.dumps({"as_of": " 2026-11-04 ", "stalled_days": 10}), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "stalled-opportunities", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual([r["opportunity"]["opportunity_id"] for r in json.loads(ok.stdout)],
                         ["O2", "O4", "O5", "O1", "O10", "O3"])
        # Outer array runs the query once per object.
        payload.write_text(json.dumps([
            {"as_of": "2026-11-04", "stalled_days": 10, "tags": ["vip"]},
            {"as_of": "2026-11-04", "stalled_days": 100, "organization": "Music"},
        ]), encoding="utf-8")
        batch = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                "stalled-opportunities", str(payload)], text=True, capture_output=True)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([r["opportunity"]["opportunity_id"] for r in values[0]],
                         ["O2", "O1", "O10"])
        self.assertEqual(values[1], [])
        # Invalid threshold: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        payload.write_text(json.dumps({"as_of": "2026-11-04", "stalled_days": True}),
                           encoding="utf-8")
        failed = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                 "stalled-opportunities", str(payload)],
                                text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing required argument is also exit 2 via the standard error envelope.
        payload.write_text(json.dumps({"as_of": "2026-11-04"}), encoding="utf-8")
        missing = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "stalled-opportunities", str(payload)],
                                 text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # A query over a nonexistent root returns [] and creates nothing.
        empty = self.root / "empty"
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "stalled-opportunities", str(payload)], text=True,
                               capture_output=True)
        self.assertEqual(quiet.returncode, 2)  # missing stalled_days
        payload.write_text(json.dumps({"as_of": "2026-11-04", "stalled_days": 10}), encoding="utf-8")
        quiet = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(empty),
                                "stalled-opportunities", str(payload)], text=True,
                               capture_output=True)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty.exists())

    def seed_complete(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("陈", "Chen", "chen@example.test", "Music")
        self.app.set_tags("A", ["vip", "华东"])
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.follow_up("A", "2026-10-02", "a one")
        self.app.follow_up("A", "2026-10-02", "a two")
        self.app.follow_up("A", "2026-10-02", "a two")  # duplicate kept verbatim
        self.app.follow_up("B", "2026-10-01", "b note")
        self.app.set_reminder("A", "2026-10-05", "old reminder")
        self.app.set_reminder("B", "2026-09-30", "b reminder")
        self.app.set_reminder("陈", "2099-01-01", "chen reminder")

    def test_complete_without_next_reminder_clears_and_appends_one(self):
        self.seed_complete()
        result = self.app.complete_reminder(" A ", " 2026-10-02 ", " 已电话 联系\n约好下次 ")
        # Public envelope: exactly followup and reminder; reminder is null when omitted.
        self.assertEqual(set(result), {"followup", "reminder"})
        self.assertIsNone(result["reminder"])
        self.assertEqual(set(result["followup"]), {"contact_id", "on", "note"})
        self.assertEqual(result["followup"],
                         {"contact_id": "A", "on": "2026-10-02", "note": "已电话 联系\n约好下次"})
        # Reopening the same root: one new followup appended after A's original same-day records;
        # A's reminder is gone, other contacts' reminders survive.
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")],
                         [("2026-10-02", "a one"), ("2026-10-02", "a two"),
                          ("2026-10-02", "a two"), ("2026-10-02", "已电话 联系\n约好下次")])
        self.assertEqual([(r["contact_id"], r["due_on"], r["note"])
                          for r in reopened.due_reminders("2099-12-31")],
                         [("B", "2026-09-30", "b reminder"),
                          ("陈", "2099-01-01", "chen reminder")])
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(list(raw["reminders"]), ["B", "陈"])
        self.assertEqual(len(raw["followups"]), 5)

    def test_complete_with_explicit_none_behaves_like_omitted(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "nudge")
        result = self.app.complete_reminder("A", "2026-10-02", "done", None)
        self.assertEqual(set(result), {"followup", "reminder"})
        self.assertIsNone(result["reminder"])
        self.assertEqual(result["followup"],
                         {"contact_id": "A", "on": "2026-10-02", "note": "done"})
        # Clearing the last reminder drops the whole collection; exactly one followup exists.
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertNotIn("reminders", raw)
        self.assertEqual(raw["followups"],
                         [{"contact_id": "A", "on": "2026-10-02", "note": "done"}])
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"), [])

    def test_complete_with_next_reminder_replaces_whole_entry(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        # Completion on a legal leap day, after the original due date; no system date is read.
        self.app.set_reminder("A", "2024-02-20", "old note")
        result = self.app.complete_reminder(
            "A", "2024-02-29", "leap completion",
            {"due_on": " 2024-03-01 ", "note": " 新 提醒\n第二 行 "})
        self.assertEqual(set(result), {"followup", "reminder"})
        self.assertEqual(result["followup"],
                         {"contact_id": "A", "on": "2024-02-29", "note": "leap completion"})
        self.assertEqual(set(result["reminder"]), {"contact_id", "due_on", "note"})
        self.assertEqual(result["reminder"],
                         {"contact_id": "A", "due_on": "2024-03-01", "note": "新 提醒\n第二 行"})
        # The old reminder entry is replaced wholesale (old due date and note are gone).
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["reminders"],
                         {"A": {"contact_id": "A", "due_on": "2024-03-01",
                                "note": "新 提醒\n第二 行"}})
        self.assertEqual(len(raw["followups"]), 1)
        self.assertEqual(ContactFlow(self.root).due_reminders("2024-12-31"),
                         [{"contact_id": "A", "due_on": "2024-03-01", "note": "新 提醒\n第二 行"}])

    def test_complete_normalizes_ids_dates_notes_and_accepts_either_side_of_due(self):
        # Completing before or after the original due date both work; values are only trimmed.
        for due_on, on in [("2026-11-05", "2026-10-01"), ("2026-10-01", "2026-11-05")]:
            root = self.root / (due_on + on)
            app = ContactFlow(root)
            app.add_contact("A", "Alice", "a@example.test", "Books")
            app.set_reminder("A", due_on, "nudge")
            result = app.complete_reminder("\t A \n", "  %s  " % on,
                                           "\n中文 备注\t第二 行\n", None)
            self.assertEqual(result["followup"],
                             {"contact_id": "A", "on": on, "note": "中文 备注\t第二 行"})
            self.assertIsNone(result["reminder"])
            self.assertEqual(ContactFlow(root).due_reminders("2099-01-01"), [])
        # Contact ids are case-sensitive: lowercase "a" is an unknown contact.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "nudge")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.complete_reminder("a", "2026-10-02", "x")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_complete_preserves_other_contacts_data_tags_deals_and_funnel(self):
        self.seed_complete()
        before_contacts = {c["contact_id"]: dict(c) for c in self.app.find()}
        funnel_before = self.app.funnel_report()
        self.app.complete_reminder("A", "2026-10-02", "a three",
                                   {"due_on": "2026-11-01", "note": "next"})
        reopened = ContactFlow(self.root)
        # Existing entries (including the duplicate) survive; the new same-day entry goes last.
        self.assertEqual([r["note"] for r in reopened.timeline("A")],
                         ["a one", "a two", "a two", "a three"])
        self.assertEqual([r["note"] for r in reopened.timeline("B")], ["b note"])
        # A's reminder is replaced; the other contacts' reminders are untouched.
        self.assertEqual([(r["contact_id"], r["due_on"], r["note"])
                          for r in reopened.due_reminders("2099-12-31")],
                         [("B", "2026-09-30", "b reminder"),
                          ("A", "2026-11-01", "next"),
                          ("陈", "2099-01-01", "chen reminder")])
        self.assertEqual({c["contact_id"]: c for c in reopened.find()}, before_contacts)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        self.assertEqual(reopened.find_opportunities(contact_id="A"),
                         [{"opportunity_id": "O1", "contact_id": "A", "title": "Deal",
                           "stage": "new"}])
        self.assertEqual(reopened.funnel_report(), funnel_before)
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["followups"]), 5)  # exactly one appended

    def test_complete_on_legacy_data_missing_collections(self):
        contact = {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                   "organization": "Old"}
        # Legacy document with a current reminder but no followups collection: still completable.
        legacy1 = self.root / "legacy-clear"
        legacy1.mkdir()
        (legacy1 / "data.json").write_text(json.dumps(
            {"contacts": {"L": contact},
             "reminders": {"L": {"contact_id": "L", "due_on": "2026-10-05", "note": "old"}}}),
            encoding="utf-8")
        app = ContactFlow(legacy1)
        result = app.complete_reminder("L", "2026-10-02", "done")
        self.assertEqual(result, {"followup": {"contact_id": "L", "on": "2026-10-02",
                                               "note": "done"},
                                  "reminder": None})
        raw = json.loads((legacy1 / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("reminders", raw)
        self.assertEqual(raw["followups"],
                         [{"contact_id": "L", "on": "2026-10-02", "note": "done"}])
        # Same shape, but scheduling a follow-up reminder creates the followups collection too.
        legacy2 = self.root / "legacy-replace"
        legacy2.mkdir()
        (legacy2 / "data.json").write_text(json.dumps(
            {"contacts": {"L": dict(contact)},
             "reminders": {"L": {"contact_id": "L", "due_on": "2026-10-05", "note": "old"}}}),
            encoding="utf-8")
        app2 = ContactFlow(legacy2)
        result2 = app2.complete_reminder("L", "2026-10-02", "done",
                                         {"due_on": "2026-11-01", "note": "next"})
        self.assertEqual(result2["reminder"],
                         {"contact_id": "L", "due_on": "2026-11-01", "note": "next"})
        raw2 = json.loads((legacy2 / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(raw2["reminders"]["L"]["note"], "next")
        self.assertEqual(len(raw2["followups"]), 1)
        # Legacy document without a reminders collection behaves like "no current reminder".
        legacy3 = self.root / "legacy-no-reminders"
        legacy3.mkdir()
        (legacy3 / "data.json").write_text(json.dumps({"contacts": {"L": dict(contact)}}),
                                           encoding="utf-8")
        app3 = ContactFlow(legacy3)
        before = (legacy3 / "data.json").read_bytes()
        for kwargs in ({}, {"next_reminder": {"due_on": "2026-11-01", "note": "x"}}):
            with self.assertRaises(ValueError):
                app3.complete_reminder("L", "2026-10-02", "done", **kwargs)
            self.assertEqual((legacy3 / "data.json").read_bytes(), before)
        self.assertEqual(ContactFlow(legacy3).timeline("L"), [])

    def test_complete_again_after_clearing_raises_without_appending(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.follow_up("A", "2026-10-01", "before")
        self.app.set_reminder("A", "2026-10-05", "nudge")
        self.app.complete_reminder("A", "2026-10-02", "first completion")
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.complete_reminder("A", "2026-10-02", "second")
        with self.assertRaises(ValueError):
            self.app.complete_reminder("A", "2026-10-02", "second",
                                       {"due_on": "2026-11-01", "note": "x"})
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("A")],
                         ["before", "first completion"])
        self.assertEqual(ContactFlow(self.root).due_reminders("2099-01-01"), [])

    def test_complete_rejects_bad_arguments_without_changing_data(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.follow_up("A", "2026-10-01", "kept note")
        self.app.set_reminder("A", "2026-10-05", "keep")
        before = self.app.path.read_bytes()
        base = {"contact_id": "A", "on": "2026-10-02", "note": "x"}
        bad_calls = []
        for bad in [None, 5, "", "   "]:
            bad_calls.append(dict(base, contact_id=bad))
            bad_calls.append(dict(base, note=bad))
        for bad in [None, 5, "", "   ", "2026-02-30", "2026-13-01", "2026-1-1",
                    "20261002", "2023-02-29"]:
            bad_calls.append(dict(base, on=bad))
        bad_calls.extend([
            dict(base, contact_id="ZZZ"),         # unknown contact
            dict(base, contact_id="a"),           # ids are case-sensitive
            dict(base, contact_id="B"),           # contact exists but has no reminder
        ])
        for bad in [[], "x", 5, True, {},
                    {"due_on": "2026-10-03"},                       # missing note
                    {"note": "x"},                                  # missing due_on
                    {"due_on": "2026-10-03", "note": "x",
                     "extra": "y"},                                 # extra key
                    {"due_on": "2026-02-30", "note": "x"},          # illegal date inside
                    {"due_on": 5, "note": "x"},
                    {"due_on": "  ", "note": "x"},
                    {"due_on": "2026-10-03", "note": None},
                    {"due_on": "2026-10-03", "note": "   "},
                    {"due_on": "2026-10-02", "note": "x"},          # equal to completion day
                    {"due_on": "2026-10-01", "note": "x"}]:         # earlier than completion day
            bad_calls.append(dict(base, next_reminder=bad))
        for kwargs in bad_calls:
            with self.assertRaises(ValueError):
                self.app.complete_reminder(**kwargs)
            self.assertEqual(self.app.path.read_bytes(), before)
        # No rejection appended a followup or touched the reminder.
        reopened = ContactFlow(self.root)
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["kept note"])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "A", "due_on": "2026-10-05", "note": "keep"}])
        # A rejected call against a root that never existed creates neither directory nor file.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.complete_reminder("ZZZ", "2026-10-02", "x")
        with self.assertRaises(ValueError):
            fresh.complete_reminder("ZZZ", "2026-10-02", "x",
                                    {"due_on": "2026-11-01", "note": "y"})
        self.assertFalse(fresh_root.exists())

    def test_complete_missing_arguments_are_type_errors(self):
        # Missing required parameters raise TypeError before any storage access.
        for call in [
            lambda: self.app.complete_reminder(),
            lambda: self.app.complete_reminder("A"),
            lambda: self.app.complete_reminder("A", "2026-10-02"),
            lambda: self.app.complete_reminder("A", note="x"),
            lambda: self.app.complete_reminder(on="2026-10-02", note="x"),
        ]:
            with self.assertRaises(TypeError):
                call()
        self.assertFalse(self.app.path.exists())

    def test_cli_complete_reminder_object_array_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Music")
        self.app.follow_up("A", "2026-10-02", "prior")
        self.app.set_reminder("A", "2026-10-05", "a note")
        self.app.set_reminder("B", "2026-10-05", "b note")
        self.app.set_reminder("C", "2026-10-05", "c note")
        self.app.set_reminder("D", "2026-10-05", "d note")
        payload = self.root / "complete.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "complete-reminder", str(payload)],
                                  text=True, capture_output=True)

        # Object input with an explicit JSON null clears the reminder and returns 0.
        ok = cli({"contact_id": " A ", "on": " 2026-10-02 ",
                  "note": " done\nline2 ", "next_reminder": None})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout),
                         {"followup": {"contact_id": "A", "on": "2026-10-02",
                                       "note": "done\nline2"},
                          "reminder": None})
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("A")],
                         ["prior", "done\nline2"])

        # A fully successful array matches the equivalent API calls, in input order. A twin
        # root receives the same calls so the whole batch can be compared byte-for-byte.
        twin = self.root / "twin"
        twin.mkdir()
        (twin / "data.json").write_bytes(self.app.path.read_bytes())
        rows = [
            {"contact_id": "B", "on": "2026-10-01", "note": "b done"},
            {"contact_id": "C", "on": "2026-10-01", "note": "c done",
             "next_reminder": {"due_on": "2026-12-01", "note": " c next "}},
        ]
        batch = cli(rows)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual([v["followup"]["contact_id"] for v in values], ["B", "C"])
        twin_app = ContactFlow(twin)
        expected = [twin_app.complete_reminder(**rows[0]),
                    twin_app.complete_reminder(**rows[1])]
        self.assertEqual(values, expected)
        self.assertEqual((self.root / "data.json").read_bytes(),
                         (twin / "data.json").read_bytes())

        # Object failure: exit 2, empty stdout, JSON error on stderr, no data change.
        before = self.app.path.read_bytes()
        failed = cli({"contact_id": "ZZZ", "on": "2026-10-01", "note": "x"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # A missing required argument goes through the same envelope (TypeError -> exit 2).
        missing = cli({"contact_id": "D", "on": "2026-10-01"})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))

        # Array executes in order: the first success is kept, the second failure stops the run,
        # and the third operation never executes.
        partial_rows = [
            {"contact_id": "C", "on": "2026-10-03", "note": "c again"},       # succeeds: clears
            {"contact_id": "A", "on": "2026-10-03", "note": "a again"},       # fails: no reminder
            {"contact_id": "D", "on": "2026-10-03", "note": "d never runs"},  # must not run
        ]
        partial = cli(partial_rows)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "D", "due_on": "2026-10-05", "note": "d note"}])
        self.assertEqual([r["note"] for r in reopened.timeline("C")], ["c done", "c again"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")],
                         ["prior", "done\nline2"])
        self.assertEqual(reopened.timeline("D"), [])

        # A failure against a nonexistent root leaves no directory or file behind.
        empty = self.root / "empty"
        gone = cli({"contact_id": "ZZZ", "on": "2026-10-01", "note": "x"}, root=empty)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(empty.exists())

    def test_complete_reminders_batch_clears_replaces_and_appends_in_order(self):
        self.seed_complete()
        result = self.app.complete_reminders([
            {"contact_id": " B ", "expected_due_on": " 2026-09-30 ", "on": " 2026-10-02 ",
             "note": " 已电话\n约好下次 "},
            {"contact_id": "A", "expected_due_on": "2026-10-05", "on": "2026-10-02",
             "note": "a three", "next_reminder": {"due_on": " 2026-11-01 ", "note": " a next "}},
            {"contact_id": "陈", "expected_due_on": "2099-01-01", "on": "2024-02-29",
             "note": "leap 完成", "next_reminder": None},
        ])
        # Results follow input order; each item has exactly followup and reminder.
        self.assertEqual([set(item) for item in result],
                         [{"followup", "reminder"}] * 3)
        self.assertEqual(result, [
            {"followup": {"contact_id": "B", "on": "2026-10-02",
                          "note": "已电话\n约好下次"}, "reminder": None},
            {"followup": {"contact_id": "A", "on": "2026-10-02", "note": "a three"},
             "reminder": {"contact_id": "A", "due_on": "2026-11-01", "note": "a next"}},
            {"followup": {"contact_id": "陈", "on": "2024-02-29", "note": "leap 完成"},
             "reminder": None},
        ])
        reopened = ContactFlow(self.root)
        # New same-day followups for A come after A's original same-day records.
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")],
                         [("2026-10-02", "a one"), ("2026-10-02", "a two"),
                          ("2026-10-02", "a two"), ("2026-10-02", "a three")])
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("B")],
                         [("2026-10-01", "b note"), ("2026-10-02", "已电话\n约好下次")])
        # B and 陈 cleared; only A's replaced reminder remains.
        self.assertEqual(reopened.due_reminders("2099-12-31"),
                         [{"contact_id": "A", "due_on": "2026-11-01", "note": "a next"}])
        raw = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertEqual(list(raw["reminders"]), ["A"])
        self.assertEqual(len(raw["followups"]), 7)

    def test_complete_reminders_matches_sequential_single_calls_bytes(self):
        self.seed_complete()
        twin_root = self.root / "twin"
        twin_root.mkdir()
        (twin_root / "data.json").write_bytes(self.app.path.read_bytes())
        rows = [
            {"contact_id": "A", "expected_due_on": "2026-10-05", "on": "2026-10-02",
             "note": "a three", "next_reminder": {"due_on": "2026-11-01", "note": "a next"}},
            {"contact_id": "B", "expected_due_on": "2026-09-30", "on": "2026-10-01",
             "note": "b done"},
        ]
        values = self.app.complete_reminders(rows)
        twin_app = ContactFlow(twin_root)
        expected = [twin_app.complete_reminder(
            row["contact_id"], row["on"], row["note"], row.get("next_reminder"))
            for row in rows]
        self.assertEqual(values, expected)
        self.assertEqual((self.root / "data.json").read_bytes(),
                         (twin_root / "data.json").read_bytes())

    def test_complete_reminders_expected_due_note_is_not_checked(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "the reminder note")
        # Only the due date is matched; the reminder's note plays no role.
        result = self.app.complete_reminders([
            {"contact_id": "A", "expected_due_on": "2026-10-05", "on": "2026-10-06",
             "note": "done late", "next_reminder": {"due_on": "2026-12-01", "note": "new"}}])
        self.assertEqual(result[0]["followup"]["on"], "2026-10-06")
        # A mismatched expected due date rejects the batch; completion may be earlier
        # or later than the real due date, but the expectation must be exact.
        before = self.app.path.read_bytes()
        for expected_due_on in ("2026-11-30", "2026-12-02", "  "):
            with self.assertRaises(ValueError):
                self.app.complete_reminders([
                    {"contact_id": "A", "expected_due_on": expected_due_on,
                     "on": "2026-12-03", "note": "x"}])
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_complete_reminders_requires_list_and_missing_argument_is_type_error(self):
        for bad in [None, {}, "x", 5, True, ({"contact_id": "A"},)]:
            with self.assertRaises(ValueError):
                self.app.complete_reminders(bad)
        self.assertFalse(self.app.path.exists())
        with self.assertRaises(TypeError):
            self.app.complete_reminders()

    def test_complete_reminders_empty_list_writes_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.complete_reminders([]), [])
        self.assertFalse(fresh_root.exists())
        self.seed_complete()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.complete_reminders([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_complete_reminders_validates_item_shape_and_fields_without_partial_changes(self):
        self.seed_complete()
        before = self.app.path.read_bytes()
        good = {"contact_id": "A", "expected_due_on": "2026-10-05",
                "on": "2026-10-02", "note": "x"}
        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [{"contact_id": "A", "expected_due_on": "2026-10-05", "on": "2026-10-02"}],
            [{"contact_id": "A", "expected_due_on": "2026-10-05", "note": "x"}],
            [{"contact_id": "A", "on": "2026-10-02", "note": "x"}],
            [{"expected_due_on": "2026-10-05", "on": "2026-10-02", "note": "x"}],
            [dict(good, extra="y")],
            [dict(good, contact_id=None)], [dict(good, contact_id=5)], [dict(good, contact_id="  ")],
            [dict(good, note=None)], [dict(good, note=5)], [dict(good, note="  ")],
            [dict(good, on=None)], [dict(good, on=5)], [dict(good, on="2026-02-30")],
            [dict(good, on="2026-1-1")], [dict(good, on="2023-02-29")],
            [dict(good, expected_due_on=None)], [dict(good, expected_due_on=5)],
            [dict(good, expected_due_on="not-a-date")], [dict(good, expected_due_on="2026-02-30")],
            [dict(good, next_reminder=[])], [dict(good, next_reminder="x")],
            [dict(good, next_reminder=True)],
            [dict(good, next_reminder={"due_on": "2026-11-01"})],
            [dict(good, next_reminder={"note": "x"})],
            [dict(good, next_reminder={"due_on": "2026-11-01", "note": "x", "extra": 1})],
            [dict(good, next_reminder={"due_on": "2026-02-30", "note": "x"})],
            [dict(good, next_reminder={"due_on": 5, "note": "x"})],
            [dict(good, next_reminder={"due_on": "2026-10-02", "note": "x"})],
            [dict(good, next_reminder={"due_on": "2026-10-01", "note": "x"})],
            # A valid first item followed by an invalid second item rolls both back.
            [good,
             {"contact_id": "B", "expected_due_on": "2026-09-30", "on": "2026-10-02"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.complete_reminders(batch)
            self.assertEqual(self.app.path.read_bytes(), before)
        # No rejection appended a followup or touched a reminder.
        reopened = ContactFlow(self.root)
        self.assertEqual(len(reopened.timeline("A")), 3)
        self.assertEqual([(r["contact_id"], r["due_on"])
                          for r in reopened.due_reminders("2099-12-31")],
                         [("B", "2026-09-30"), ("A", "2026-10-05"), ("陈", "2099-01-01")])

    def test_complete_reminders_rejects_duplicate_unknown_missing_and_mismatch_atomically(self):
        self.seed_complete()
        before = self.app.path.read_bytes()
        good_a = {"contact_id": "A", "expected_due_on": "2026-10-05",
                  "on": "2026-10-02", "note": "x"}
        # Duplicate normalized id, even with identical content, rejects the batch.
        with self.assertRaises(ValueError):
            self.app.complete_reminders([
                dict(good_a, contact_id=" A "),
                dict(good_a, contact_id="A", note="different")])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.complete_reminders([good_a, dict(good_a, note="again")])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Ids are case-sensitive: lowercase a is unknown.
        with self.assertRaises(ValueError):
            self.app.complete_reminders([dict(good_a, contact_id="a")])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Unknown contact, a contact without a reminder, and a due date mismatch
        # each reject a batch where an earlier item was otherwise valid.
        with self.assertRaises(ValueError):
            self.app.complete_reminders([
                {"contact_id": "ZZZ", "expected_due_on": "2026-10-05",
                 "on": "2026-10-02", "note": "x"}])
        self.app.add_contact("N", "Nina", "n@example.test", "Books")
        # Adding N itself commits; re-snapshot so the rejected batch is what is compared.
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.complete_reminders([
                good_a,
                {"contact_id": "N", "expected_due_on": "2026-10-05",
                 "on": "2026-10-02", "note": "x"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.complete_reminders([
                good_a,
                {"contact_id": "B", "expected_due_on": "2026-09-29",
                 "on": "2026-10-02", "note": "x"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual([r["note"] for r in reopened.timeline("A")],
                         ["a one", "a two", "a two"])
        self.assertEqual(len(reopened.due_reminders("2099-12-31")), 3)
        # A rejected batch against a root that never existed creates neither directory nor file.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.complete_reminders([
                {"contact_id": "ZZZ", "expected_due_on": "2026-10-05",
                 "on": "2026-10-02", "note": "x"}])
        self.assertFalse(fresh_root.exists())

    def test_complete_reminders_preserves_contacts_tags_deals_and_reports(self):
        self.seed_complete()
        before_contacts = {c["contact_id"]: dict(c) for c in self.app.find()}
        funnel_before = self.app.funnel_report()
        self.app.complete_reminders([
            {"contact_id": "A", "expected_due_on": "2026-10-05", "on": "2026-10-02",
             "note": "a three", "next_reminder": {"due_on": "2026-11-01", "note": "next"}}])
        reopened = ContactFlow(self.root)
        self.assertEqual({c["contact_id"]: c for c in reopened.find()}, before_contacts)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        self.assertEqual(reopened.find_opportunities(contact_id="A"),
                         [{"opportunity_id": "O1", "contact_id": "A", "title": "Deal",
                           "stage": "new"}])
        self.assertEqual(reopened.funnel_report(), funnel_before)
        self.assertEqual([r["note"] for r in reopened.timeline("B")], ["b note"])

    def test_complete_reminders_on_legacy_data_missing_collections(self):
        contact = {"contact_id": "L", "name": "Lee", "email": "l@example.test",
                   "organization": "Old"}
        # Legacy document with a reminder but no followups collection: still completable.
        legacy1 = self.root / "legacy-clear"
        legacy1.mkdir()
        (legacy1 / "data.json").write_text(json.dumps(
            {"contacts": {"L": contact},
             "reminders": {"L": {"contact_id": "L", "due_on": "2026-10-05", "note": "old"}}}),
            encoding="utf-8")
        app = ContactFlow(legacy1)
        result = app.complete_reminders(
            [{"contact_id": "L", "expected_due_on": "2026-10-05",
              "on": "2026-10-02", "note": "done"}])
        self.assertEqual(result, [{"followup": {"contact_id": "L", "on": "2026-10-02",
                                                "note": "done"},
                                  "reminder": None}])
        raw = json.loads((legacy1 / "data.json").read_text(encoding="utf-8"))
        self.assertNotIn("reminders", raw)
        self.assertEqual(raw["followups"],
                         [{"contact_id": "L", "on": "2026-10-02", "note": "done"}])
        # Legacy document without a reminders collection: a nonempty batch is rejected
        # as having no current reminder, and the file bytes stay unchanged.
        legacy2 = self.root / "legacy-no-reminders"
        legacy2.mkdir()
        (legacy2 / "data.json").write_text(json.dumps({"contacts": {"L": dict(contact)}}),
                                           encoding="utf-8")
        app2 = ContactFlow(legacy2)
        before = (legacy2 / "data.json").read_bytes()
        for item_kwargs in ({}, {"next_reminder": {"due_on": "2026-11-01", "note": "x"}}):
            with self.assertRaises(ValueError):
                app2.complete_reminders([
                    dict({"contact_id": "L", "expected_due_on": "2026-10-05",
                          "on": "2026-10-02", "note": "done"}, **item_kwargs)])
            self.assertEqual((legacy2 / "data.json").read_bytes(), before)
        self.assertEqual(ContactFlow(legacy2).timeline("L"), [])

    def test_cli_complete_reminders_object_array_and_failure(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.add_contact("D", "Dan", "d@example.test", "Music")
        self.app.follow_up("A", "2026-10-02", "prior")
        self.app.set_reminder("A", "2026-10-05", "a note")
        self.app.set_reminder("B", "2026-10-05", "b note")
        self.app.set_reminder("C", "2026-10-05", "c note")
        self.app.set_reminder("D", "2026-10-05", "d note")
        payload = self.root / "completions.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "complete-reminders", str(payload)],
                                  text=True, capture_output=True)

        # Object input: one atomic batch with mixed clear/replace results, exit 0.
        ok = cli({"completions": [
            {"contact_id": " A ", "expected_due_on": " 2026-10-05 ",
             "on": " 2026-10-02 ", "note": " done\nline2 ", "next_reminder": None},
            {"contact_id": "B", "expected_due_on": "2026-10-05", "on": "2026-10-01",
             "note": "b done",
             "next_reminder": {"due_on": " 2026-12-01 ", "note": " b next "}},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"followup": {"contact_id": "A", "on": "2026-10-02", "note": "done\nline2"},
             "reminder": None},
            {"followup": {"contact_id": "B", "on": "2026-10-01", "note": "b done"},
             "reminder": {"contact_id": "B", "due_on": "2026-12-01", "note": "b next"}},
        ])
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("A")],
                         ["prior", "done\nline2"])

        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"completions": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())

        # Validation failure (due date mismatch): exit 2, empty stdout, JSON error
        # on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"completions": [
            {"contact_id": "C", "expected_due_on": "2026-10-04",
             "on": "2026-10-01", "note": "never"},
            {"contact_id": "D", "expected_due_on": "2026-10-05",
             "on": "2026-10-01", "note": "never"},
        ]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.due_reminders("2026-10-05"),
                         [{"contact_id": "C", "due_on": "2026-10-05", "note": "c note"},
                          {"contact_id": "D", "due_on": "2026-10-05", "note": "d note"}])
        self.assertEqual(reopened.timeline("C"), [])
        self.assertEqual(reopened.timeline("D"), [])
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # completions must be a list.
        not_list = cli({"completions": {"contact_id": "C"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # A failure against a nonexistent root leaves no directory or file behind.
        gone_root = self.root / "gone"
        gone = cli({"completions": [
            {"contact_id": "ZZZ", "expected_due_on": "2026-10-05",
             "on": "2026-10-01", "note": "x"}]}, root=gone_root)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(gone_root.exists())

    def test_cli_complete_reminders_outer_array_keeps_earlier_batches(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Books")
        self.app.set_reminder("A", "2026-10-05", "a note")
        self.app.set_reminder("B", "2026-10-05", "b note")
        payload = self.root / "batches.json"
        # Outer array runs whole batches independently; a later failed batch keeps
        # the earlier successful batch (unlike atomicity inside one completions list).
        payload.write_text(json.dumps([
            {"completions": [
                {"contact_id": "A", "expected_due_on": "2026-10-05",
                 "on": "2026-10-02", "note": "a done"}]},
            {"completions": [
                {"contact_id": "B", "expected_due_on": "2026-09-30",
                 "on": "2026-10-02", "note": "b never"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "complete-reminders", str(payload)],
                                 text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        reopened = ContactFlow(self.root)
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["a done"])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "B", "due_on": "2026-10-05", "note": "b note"}])
        self.assertEqual(reopened.timeline("B"), [])

    def seed_stage_deals(self):
        self.app.add_contact("A", "Alice", "alice@example.test", "Books")
        self.app.add_contact("B", "Bob", "bob@example.test", "Music")
        # O1 new; O2 qualified with a dated history; O3 won (terminal) with an amount; O4 lost.
        self.app.add_opportunity("O1", "A", "First")
        self.app.add_opportunity("O2", "A", "Second")
        self.app.set_stage("O2", "qualified", "2026-09-01")
        self.app.add_opportunity("O3", "B", "Third")
        self.app.set_stage("O3", "qualified", "2026-09-10")
        self.app.set_stage("O3", "won", "2026-09-20")
        self.app.set_opportunity_amount("O3", "12.50")
        self.app.add_opportunity("O4", "B", "Fourth")
        self.app.set_stage("O4", "lost", "2026-08-01")

    def test_set_stages_batch_returns_full_opportunities_in_order(self):
        self.seed_stage_deals()
        result = self.app.set_stages([
            {"opportunity_id": " O2 ", "stage": " won ", "on": " 2026-09-25 "},
            {"opportunity_id": "O1", "stage": "qualified"},
            {"opportunity_id": "O3", "stage": "won", "on": "2026-09-20"},
        ])
        # Input order, not id order; values are trimmed like set-stage; the same-stage item is a no-op.
        self.assertEqual(result, [
            {"opportunity_id": "O2", "contact_id": "A", "title": "Second", "stage": "won"},
            {"opportunity_id": "O1", "contact_id": "A", "title": "First", "stage": "qualified"},
            {"opportunity_id": "O3", "contact_id": "B", "title": "Third", "stage": "won", "amount": "12.50"},
        ])
        reopened = ContactFlow(self.root)
        stages = {o["opportunity_id"]: o["stage"]
                  for o in reopened.find_opportunities()}
        self.assertEqual(stages, {"O1": "qualified", "O2": "won", "O3": "won", "O4": "lost"})
        # Amount survives on O3; deals never priced keep no amount field.
        by_id = {o["opportunity_id"]: o for o in reopened.find_opportunities()}
        self.assertEqual(by_id["O3"]["amount"], "12.50")
        self.assertNotIn("amount", by_id["O1"])
        # One history record per real change, in save order; the same-stage item adds nothing.
        self.assertEqual(reopened.stage_history("O1"),
                         [{"from_stage": "new", "to_stage": "qualified", "on": None}])
        self.assertEqual(reopened.stage_history("O2"), [
            {"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
            {"from_stage": "qualified", "to_stage": "won", "on": "2026-09-25"},
        ])
        self.assertEqual([h["to_stage"] for h in reopened.stage_history("O3")], ["qualified", "won"])

    def test_set_stages_matches_sequential_set_stage_bytes(self):
        self.seed_stage_deals()
        twin_root = self.root / "twin"
        twin = ContactFlow(twin_root)
        twin.add_contact("A", "Alice", "alice@example.test", "Books")
        twin.add_contact("B", "Bob", "bob@example.test", "Music")
        twin.add_opportunity("O1", "A", "First")
        twin.add_opportunity("O2", "A", "Second")
        twin.set_stage("O2", "qualified", "2026-09-01")
        twin.add_opportunity("O3", "B", "Third")
        twin.set_stage("O3", "qualified", "2026-09-10")
        twin.set_stage("O3", "won", "2026-09-20")
        twin.set_opportunity_amount("O3", "12.50")
        twin.add_opportunity("O4", "B", "Fourth")
        twin.set_stage("O4", "lost", "2026-08-01")
        updates = [
            {"opportunity_id": "O2", "stage": "won", "on": "2026-09-25"},
            {"opportunity_id": "O1", "stage": "qualified"},
            {"opportunity_id": "O3", "stage": "won", "on": "2026-09-20"},
        ]
        self.app.set_stages(updates)
        for row in updates:
            twin.set_stage(**row)
        self.assertEqual((self.root / "data.json").read_bytes(),
                         (twin_root / "data.json").read_bytes())

    def test_set_stages_requires_list_and_missing_argument_is_type_error(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        for bad in [None, {}, {"updates": []}, "x", 5, True, ({"opportunity_id": "O1", "stage": "lost"},)]:
            with self.assertRaises(ValueError):
                self.app.set_stages(bad)
            self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(TypeError):
            self.app.set_stages()

    def test_set_stages_empty_list_writes_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.set_stages([]), [])
        self.assertFalse(fresh_root.exists())
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.set_stages([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_set_stages_validates_item_shape_without_partial_changes(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [{"stage": "won"}],
            [{"opportunity_id": "O1"}],
            [{"opportunity_id": "O1", "stage": "won", "extra": 1}],
            [{"opportunity_id": "O1", "stage": "won", "on": "2026-10-01", "x": 0}],
            [{"opportunity_id": "O1", "stage": "Won"}],
            [{"opportunity_id": "O1", "stage": "closed"}],
            [{"opportunity_id": "O1", "stage": "  "}],
            [{"opportunity_id": "O1", "stage": None}],
            [{"opportunity_id": "O1", "stage": 5}],
            [{"opportunity_id": "", "stage": "qualified"}],
            [{"opportunity_id": "   ", "stage": "qualified"}],
            [{"opportunity_id": 5, "stage": "qualified"}],
            [{"opportunity_id": "O1", "stage": "qualified", "on": 5}],
            [{"opportunity_id": "O1", "stage": "qualified", "on": "2026-1-1"}],
            [{"opportunity_id": "O1", "stage": "qualified", "on": "2026-02-30"}],
            [{"opportunity_id": "O1", "stage": "qualified", "on": "not-a-date"}],
            # A valid first item followed by an invalid second item rolls both back.
            [{"opportunity_id": "O1", "stage": "qualified"},
             {"opportunity_id": "O2", "stage": "bad"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.set_stages(batch)
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_set_stages_rejects_unknown_duplicate_transition_and_dates(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        # Unknown opportunity, including a case-sensitive miss and an empty store.
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O9", "stage": "qualified"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "o1", "stage": "qualified"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.set_stages([{"opportunity_id": "O1", "stage": "qualified"}])
        self.assertFalse((self.root / "fresh").exists())
        # Duplicate normalized ids reject the batch, even when the items are identical.
        with self.assertRaises(ValueError):
            self.app.set_stages([
                {"opportunity_id": " O1 ", "stage": "qualified"},
                {"opportunity_id": "O1", "stage": "qualified"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.set_stages([
                {"opportunity_id": "O2", "stage": "won", "on": "2026-09-25"},
                {"opportunity_id": "O2", "stage": "won", "on": "2026-09-25"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Illegal transitions, including leaving a terminal stage, roll the batch back.
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O1", "stage": "won"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.set_stages([
                {"opportunity_id": "O1", "stage": "qualified"},
                {"opportunity_id": "O3", "stage": "qualified"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O4", "stage": "new"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Date regression against the pre-call latest non-null history date rejects; same day is fine.
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O2", "stage": "won", "on": "2026-08-31"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # The date is still checked on a same-stage no-op.
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O2", "stage": "qualified", "on": "2026-08-31"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Future dates, same-day changes and legal leap days are accepted.
        ok = self.app.set_stages([
            {"opportunity_id": "O2", "stage": "won", "on": "2099-01-01"},
        ])
        self.assertEqual(ok[0]["stage"], "won")
        self.app.add_opportunity("O5", "A", "Fifth")
        leap = self.app.set_stages([{"opportunity_id": "O5", "stage": "lost", "on": "2024-02-29"}])
        self.assertEqual(leap[0]["stage"], "lost")
        self.assertEqual(self.app.stage_history("O5"),
                         [{"from_stage": "new", "to_stage": "lost", "on": "2024-02-29"}])

    def test_set_stages_is_atomic_when_a_later_item_fails(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.set_stages([
                {"opportunity_id": "O1", "stage": "qualified", "on": "2026-10-01"},
                {"opportunity_id": "O3", "stage": "qualified"},  # terminal cannot move
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.find_opportunities(stage="qualified")[0]["opportunity_id"], "O2")
        self.assertEqual(reopened.stage_history("O1"), [])
        self.assertEqual(len(reopened.stage_history("O3")), 2)

    def test_set_stages_all_noop_returns_opportunities_without_rewrite(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        result = self.app.set_stages([
            {"opportunity_id": " O1 ", "stage": "new"},
            {"opportunity_id": "O2", "stage": "qualified", "on": "2026-09-01"},
            {"opportunity_id": "O3", "stage": "won", "on": "2099-12-31"},
        ])
        self.assertEqual([o["stage"] for o in result], ["new", "qualified", "won"])
        self.assertEqual(result[2]["amount"], "12.50")
        self.assertNotIn("amount", result[0])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(self.app.stage_history("O2"),
                         [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"}])

    def test_set_stages_preserves_related_records_and_reflects_in_reports(self):
        self.seed_stage_deals()
        self.app.set_tags("A", ["vip", "华东"])
        self.app.follow_up("A", "2026-09-15", "note one")
        self.app.set_reminder("B", "2026-11-05", "call back")
        self.app.set_stages([
            {"opportunity_id": "O1", "stage": "qualified", "on": "2026-09-12"},
            {"opportunity_id": "O2", "stage": "won", "on": "2026-09-25"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip", "华东"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["note one"])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "B", "due_on": "2026-11-05", "note": "call back"}])
        by_id = {o["opportunity_id"]: o for o in reopened.find_opportunities()}
        self.assertEqual((by_id["O1"]["contact_id"], by_id["O1"]["title"]), ("A", "First"))
        self.assertEqual((by_id["O2"]["contact_id"], by_id["O2"]["title"]), ("A", "Second"))
        funnel = reopened.funnel_report()
        self.assertEqual(funnel["total"],
                         {"contacts": 2, "new": 0, "qualified": 1, "won": 2, "lost": 1,
                          "opportunities": 4})
        amounts = reopened.opportunity_amount_report()["total"]
        self.assertEqual((amounts["won"], amounts["lost"], amounts["amount"]),
                         ("12.50", "0.00", "12.50"))
        changes = self.app.stage_change_report("2026-09-01", "2026-09-30")
        self.assertEqual([(r["opportunity_id"], r["to_stage"], r["on"]) for r in changes["records"]],
                         [("O2", "qualified", "2026-09-01"),
                          ("O3", "qualified", "2026-09-10"),
                          ("O1", "qualified", "2026-09-12"),
                          ("O3", "won", "2026-09-20"),
                          ("O2", "won", "2026-09-25")])

    def test_set_stages_on_legacy_data_without_collections(self):
        # No opportunities collection: a nonempty batch fails with the file bytes intact
        # and creates nothing extra; an empty batch still succeeds quietly.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                "email": "a@example.test", "organization": "Books"}}}),
            encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        self.assertEqual(legacy.set_stages([]), [])
        with self.assertRaises(ValueError):
            legacy.set_stages([{"opportunity_id": "O1", "stage": "qualified"}])
        self.assertEqual(json.loads((legacy_root / "data.json").read_text(encoding="utf-8")),
                         {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                             "email": "a@example.test", "organization": "Books"}}})
        # A legacy store with opportunities but no stage_history collection starts from empty history.
        other_root = self.root / "other"
        other_root.mkdir()
        (other_root / "data.json").write_text(json.dumps({
            "contacts": {"A": {"contact_id": "A", "name": "Alice",
                               "email": "a@example.test", "organization": "Books"}},
            "opportunities": {"O1": {"opportunity_id": "O1", "contact_id": "A",
                                     "title": "Deal", "stage": "new"}}}), encoding="utf-8")
        other = ContactFlow(other_root)
        result = other.set_stages([{"opportunity_id": "O1", "stage": "lost", "on": "2024-02-29"}])
        self.assertEqual(result, [{"opportunity_id": "O1", "contact_id": "A",
                                   "title": "Deal", "stage": "lost"}])
        self.assertEqual(ContactFlow(other_root).stage_history("O1"),
                         [{"from_stage": "new", "to_stage": "lost", "on": "2024-02-29"}])

    def test_cli_set_stages_object_array_and_failure(self):
        self.seed_stage_deals()
        payload = self.root / "batch.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "set-stages", str(payload)], text=True, capture_output=True)

        ok = cli({"updates": [
            {"opportunity_id": " O1 ", "stage": " qualified "},
            {"opportunity_id": "O2", "stage": "won", "on": "2026-09-25"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"opportunity_id": "O1", "contact_id": "A", "title": "First", "stage": "qualified"},
            {"opportunity_id": "O2", "contact_id": "A", "title": "Second", "stage": "won"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.stage_history("O1"),
                         [{"from_stage": "new", "to_stage": "qualified", "on": None}])
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"updates": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"updates": [
            {"opportunity_id": "O3", "stage": "qualified"},
            {"opportunity_id": "O1", "stage": "new"},
        ]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # updates must be a list.
        not_list = cli({"updates": {"opportunity_id": "O1", "stage": "lost"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # A failure against a nonexistent root leaves no directory or file behind.
        gone_root = self.root / "gone"
        gone = cli({"updates": [{"opportunity_id": "O1", "stage": "qualified"}]}, root=gone_root)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(gone_root.exists())

    def test_cli_set_stages_outer_array_keeps_earlier_batches(self):
        self.seed_stage_deals()
        payload = self.root / "batches.json"
        # Outer array runs whole batches independently; a later failed batch keeps
        # the earlier successful batch (unlike atomicity inside one updates list).
        payload.write_text(json.dumps([
            {"updates": [{"opportunity_id": "O1", "stage": "qualified"}]},
            {"updates": [{"opportunity_id": "ZZZ", "stage": "won"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "set-stages", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        by_id = {o["opportunity_id"]: o for o in ContactFlow(self.root).find_opportunities()}
        self.assertEqual(by_id["O1"]["stage"], "qualified")
        self.assertEqual(by_id["O2"]["stage"], "qualified")
        self.assertEqual(by_id["O3"]["stage"], "won")

    def test_reopen_opportunities_batch_returns_full_opportunities_in_order(self):
        self.seed_stage_deals()
        result = self.app.reopen_opportunities([
            {"opportunity_id": " O3 ", "expected_stage": " won ", "on": " 2026-10-01 "},
            {"opportunity_id": "O4", "expected_stage": "lost", "on": "2026-10-02"},
        ])
        # Input order, not id order; values are trimmed; the full opportunity is
        # returned with title, ownership, amount and id preserved.
        self.assertEqual(result, [
            {"opportunity_id": "O3", "contact_id": "B", "title": "Third",
             "stage": "qualified", "amount": "12.50"},
            {"opportunity_id": "O4", "contact_id": "B", "title": "Fourth", "stage": "qualified"},
        ])
        reopened = ContactFlow(self.root)
        by_id = {o["opportunity_id"]: o for o in reopened.find_opportunities()}
        self.assertEqual({oid: o["stage"] for oid, o in by_id.items()},
                         {"O1": "new", "O2": "qualified", "O3": "qualified", "O4": "qualified"})
        # Amount survives on O3; the never-priced O4 keeps no amount field.
        self.assertEqual(by_id["O3"]["amount"], "12.50")
        self.assertNotIn("amount", by_id["O4"])
        # One record appended at the end of each deal's existing history.
        self.assertEqual(reopened.stage_history("O3"), [
            {"from_stage": "new", "to_stage": "qualified", "on": "2026-09-10"},
            {"from_stage": "qualified", "to_stage": "won", "on": "2026-09-20"},
            {"from_stage": "won", "to_stage": "qualified", "on": "2026-10-01"},
        ])
        self.assertEqual(reopened.stage_history("O4"), [
            {"from_stage": "new", "to_stage": "lost", "on": "2026-08-01"},
            {"from_stage": "lost", "to_stage": "qualified", "on": "2026-10-02"},
        ])

    def test_reopen_opportunities_requires_list_and_missing_argument_is_type_error(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        for bad in [None, {}, {"reopens": []}, "x", 5, True,
                    ({"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"},)]:
            with self.assertRaises(ValueError):
                self.app.reopen_opportunities(bad)
            self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(TypeError):
            self.app.reopen_opportunities()

    def test_reopen_opportunities_empty_list_writes_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.reopen_opportunities([]), [])
        self.assertFalse(fresh_root.exists())
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.reopen_opportunities([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_reopen_opportunities_validates_item_shape_and_fields_without_partial_changes(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [{"opportunity_id": "O3", "expected_stage": "won"}],
            [{"opportunity_id": "O3", "on": "2026-10-01"}],
            [{"expected_stage": "won", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01", "extra": 1}],
            [{"opportunity_id": "O3", "expected_stage": "Won", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": "qualified", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": "new", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": "  ", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": None, "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": 5, "on": "2026-10-01"}],
            [{"opportunity_id": "", "expected_stage": "won", "on": "2026-10-01"}],
            [{"opportunity_id": "   ", "expected_stage": "won", "on": "2026-10-01"}],
            [{"opportunity_id": 5, "expected_stage": "won", "on": "2026-10-01"}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": None}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": 5}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-1-1"}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-02-30"}],
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "not-a-date"}],
            # A valid first item followed by an invalid second item rolls both back.
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"},
             {"opportunity_id": "O4", "expected_stage": "lost", "on": "bad"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.reopen_opportunities(batch)
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_reopen_opportunities_rejects_unknown_duplicate_mismatch_and_dates(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        # Unknown opportunity, including a case-sensitive miss and an empty store.
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O9", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "o3", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        fresh = ContactFlow(self.root / "fresh")
        with self.assertRaises(ValueError):
            fresh.reopen_opportunities(
                [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertFalse((self.root / "fresh").exists())
        # Duplicate normalized ids reject the batch, even when the items are identical.
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities([
                {"opportunity_id": " O3 ", "expected_stage": "won", "on": "2026-10-01"},
                {"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        # The expected stage must equal the current one: wrong terminal stage,
        # and deals still open (new or qualified) cannot reopen at all.
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O3", "expected_stage": "lost", "on": "2026-10-01"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O4", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O2", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Date regression against the latest non-null history date rejects; same
        # day, future dates and legal leap days are accepted.
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-09-19"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        ok = self.app.reopen_opportunities(
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-09-20"}])
        self.assertEqual(ok[0]["stage"], "qualified")
        self.app.set_stage("O3", "lost", "2099-01-01")
        again = self.app.reopen_opportunities(
            [{"opportunity_id": "O3", "expected_stage": "lost", "on": "2099-01-01"}])
        self.assertEqual(again[0]["stage"], "qualified")

    def test_reopen_opportunities_is_atomic_when_a_later_item_fails(self):
        self.seed_stage_deals()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities([
                {"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"},
                {"opportunity_id": "O4", "expected_stage": "won", "on": "2026-10-02"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.find_opportunities(stage="won")[0]["opportunity_id"], "O3")
        self.assertEqual(reopened.find_opportunities(stage="lost")[0]["opportunity_id"], "O4")
        self.assertEqual(len(reopened.stage_history("O3")), 2)
        self.assertEqual(len(reopened.stage_history("O4")), 1)

    def test_reopen_opportunities_again_requires_reclosing(self):
        self.seed_stage_deals()
        self.app.reopen_opportunities(
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"}])
        before = self.app.path.read_bytes()
        # Reopening again without re-closing rejects: the deal is qualified now.
        with self.assertRaises(ValueError):
            self.app.reopen_opportunities(
                [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-02"}])
        self.assertEqual(self.app.path.read_bytes(), before)
        # set-stage and set-stages still refuse to move a deal out of a terminal
        # stage directly; only reopen_opportunities brings it back to qualified.
        with self.assertRaises(ValueError):
            self.app.set_stage("O4", "qualified")
        with self.assertRaises(ValueError):
            self.app.set_stages([{"opportunity_id": "O4", "stage": "qualified"}])
        # After a fresh close the deal can be reopened once more.
        self.app.set_stage("O3", "won", "2026-10-05")
        result = self.app.reopen_opportunities(
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-06"}])
        self.assertEqual(result[0]["stage"], "qualified")
        self.assertEqual([h["to_stage"] for h in self.app.stage_history("O3")],
                         ["qualified", "won", "qualified", "won", "qualified"])

    def test_reopen_opportunities_reflects_in_reports_and_preserves_records(self):
        self.seed_stage_deals()
        self.app.set_tags("A", ["vip"])
        self.app.follow_up("A", "2026-09-15", "note one")
        self.app.set_reminder("B", "2026-11-05", "call back")
        self.app.reopen_opportunities([
            {"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"},
            {"opportunity_id": "O4", "expected_stage": "lost", "on": "2026-10-02"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual([r["note"] for r in reopened.timeline("A")], ["note one"])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "B", "due_on": "2026-11-05", "note": "call back"}])
        by_id = {o["opportunity_id"]: o for o in reopened.find_opportunities()}
        self.assertEqual((by_id["O3"]["contact_id"], by_id["O3"]["title"]), ("B", "Third"))
        # Funnel and amount reports count the deals as qualified; totals stay.
        funnel = reopened.funnel_report()
        self.assertEqual(funnel["total"],
                         {"contacts": 2, "new": 1, "qualified": 3, "won": 0, "lost": 0,
                          "opportunities": 4})
        amounts = reopened.opportunity_amount_report()["total"]
        self.assertEqual((amounts["qualified"], amounts["won"], amounts["amount"]),
                         ("12.50", "0.00", "12.50"))
        # The stage-change report shows the new reopen records.
        changes = reopened.stage_change_report("2026-08-01", "2026-10-31")
        self.assertEqual([(r["opportunity_id"], r["to_stage"], r["on"]) for r in changes["records"]],
                         [("O4", "lost", "2026-08-01"),
                          ("O2", "qualified", "2026-09-01"),
                          ("O3", "qualified", "2026-09-10"),
                          ("O3", "won", "2026-09-20"),
                          ("O3", "qualified", "2026-10-01"),
                          ("O4", "qualified", "2026-10-02")])
        # Stalled query measures from the reopen date, not the original entry.
        stalled = reopened.stalled_opportunities("2026-10-10", 5)
        self.assertEqual([(row["opportunity"]["opportunity_id"], row["age_days"])
                          for row in stalled], [("O2", 39), ("O3", 9), ("O4", 8)])
        # Win-cycle and conversion reports keep their existing rules: the old won
        # event still counts and the reopen record does not rewrite the outcome.
        cycle = reopened.win_cycle_report("2026-09-01", "2026-09-30")["total"]
        self.assertEqual((cycle["won"], cycle["measured"], cycle["days"], cycle["average"]),
                         (1, 1, 10, "10.00"))
        conversion = reopened.conversion_report("2026-09-01", "2026-09-30", "2026-09-30")["total"]
        self.assertEqual((conversion["entered"], conversion["won"], conversion["open"],
                          conversion["win_rate"]), (2, 1, 1, "50.00"))

    def test_reopen_opportunities_on_legacy_data_without_collections(self):
        # No opportunities collection: a nonempty batch fails with the file bytes
        # intact and creates nothing extra; an empty batch still succeeds quietly.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                "email": "a@example.test", "organization": "Books"}}}),
            encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        self.assertEqual(legacy.reopen_opportunities([]), [])
        with self.assertRaises(ValueError):
            legacy.reopen_opportunities(
                [{"opportunity_id": "O1", "expected_stage": "won", "on": "2026-10-01"}])
        self.assertEqual(json.loads((legacy_root / "data.json").read_text(encoding="utf-8")),
                         {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                             "email": "a@example.test", "organization": "Books"}}})
        # A legacy store with a closed deal but no stage_history collection starts
        # recording from the closed stage, with no back-filled earlier records.
        other_root = self.root / "other"
        other_root.mkdir()
        (other_root / "data.json").write_text(json.dumps({
            "contacts": {"A": {"contact_id": "A", "name": "Alice",
                               "email": "a@example.test", "organization": "Books"}},
            "opportunities": {"O1": {"opportunity_id": "O1", "contact_id": "A",
                                     "title": "Deal", "stage": "lost"}}}), encoding="utf-8")
        other = ContactFlow(other_root)
        result = other.reopen_opportunities(
            [{"opportunity_id": "O1", "expected_stage": "lost", "on": "2024-02-29"}])
        self.assertEqual(result, [{"opportunity_id": "O1", "contact_id": "A",
                                   "title": "Deal", "stage": "qualified"}])
        self.assertEqual(ContactFlow(other_root).stage_history("O1"),
                         [{"from_stage": "lost", "to_stage": "qualified", "on": "2024-02-29"}])

    def test_cli_reopen_opportunities_object_array_and_failure(self):
        self.seed_stage_deals()
        payload = self.root / "batch.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "reopen-opportunities", str(payload)], text=True, capture_output=True)

        ok = cli({"reopens": [
            {"opportunity_id": " O3 ", "expected_stage": " won ", "on": " 2026-10-01 "},
            {"opportunity_id": "O4", "expected_stage": "lost", "on": "2026-10-02"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"opportunity_id": "O3", "contact_id": "B", "title": "Third",
             "stage": "qualified", "amount": "12.50"},
            {"opportunity_id": "O4", "contact_id": "B", "title": "Fourth", "stage": "qualified"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual(reopened.stage_history("O4")[-1],
                         {"from_stage": "lost", "to_stage": "qualified", "on": "2026-10-02"})
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"reopens": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"reopens": [
            {"opportunity_id": "O1", "expected_stage": "new", "on": "2026-10-01"},
        ]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # reopens must be a list.
        not_list = cli({"reopens": {"opportunity_id": "O3", "expected_stage": "won",
                                    "on": "2026-10-01"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # A failure against a nonexistent root leaves no directory or file behind.
        gone_root = self.root / "gone"
        gone = cli({"reopens": [{"opportunity_id": "O3", "expected_stage": "won",
                                 "on": "2026-10-01"}]}, root=gone_root)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(gone_root.exists())

    def test_cli_reopen_opportunities_outer_array_keeps_earlier_batches(self):
        self.seed_stage_deals()
        payload = self.root / "batches.json"
        # Outer array runs whole batches independently; a later failed batch keeps
        # the earlier successful batch (unlike atomicity inside one reopens list).
        payload.write_text(json.dumps([
            {"reopens": [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-01"}]},
            {"reopens": [{"opportunity_id": "ZZZ", "expected_stage": "lost", "on": "2026-10-02"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "reopen-opportunities", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        by_id = {o["opportunity_id"]: o for o in ContactFlow(self.root).find_opportunities()}
        self.assertEqual(by_id["O3"]["stage"], "qualified")
        self.assertEqual(by_id["O4"]["stage"], "lost")

    def seed_corrections(self):
        # A's followups are saved out of date order, with a same-day group whose
        # last two records are identical twins; B has one entry; C has none.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_contact("C", "Cara", "c@example.test", "Music")
        self.app.follow_up("A", "2026-10-03", "a third day")
        self.app.follow_up("A", "2026-10-01", "a first day")
        self.app.follow_up("A", "2026-10-02", "a second day first save")
        self.app.follow_up("A", "2026-10-02", "a second day second save")
        self.app.follow_up("A", "2026-10-02", "a second day second save")  # identical twin
        self.app.follow_up("B", "2026-10-02", "b same day")
        # Tags, opportunities, history, amounts and reminders ride along untouched.
        self.app.set_tags("A", ["vip"])
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_stage("O1", "qualified", "2026-09-15")
        self.app.set_opportunity_amount("O1", "10.00")
        self.app.set_reminder("B", "2026-11-05", "call back")

    def test_correct_followups_positions_results_and_post_state(self):
        self.seed_corrections()
        # Indices address the pre-call timeline (date ascending, save order on
        # ties): the first item's date change reorders A's timeline, yet the
        # second item still corrects the record selected at pre-call index 2.
        result = self.app.correct_followups([
            {"contact_id": " A ", "index": 1, "expected_on": "2026-10-02",
             "expected_note": "a second day first save", "changes": {"on": " 2026-10-04 "}},
            {"contact_id": "A", "index": 2, "expected_on": "2026-10-02",
             "expected_note": "a second day second save",
             "changes": {"note": "  corrected  note\n第二行  "}},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day", "changes": {"on": "2026-09-30", "note": "b corrected"}},
        ])
        # Complete records in input order; ids and dates trim, notes trim but
        # keep inner whitespace and newlines.
        self.assertEqual(result, [
            {"contact_id": "A", "on": "2026-10-04", "note": "a second day first save"},
            {"contact_id": "A", "on": "2026-10-02", "note": "corrected  note\n第二行"},
            {"contact_id": "B", "on": "2026-09-30", "note": "b corrected"},
        ])
        reopened = ContactFlow(self.root)
        # The corrected twin moved; the identical twin at index 3 is untouched.
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2026-10-01", "a first day"),
            ("2026-10-02", "corrected  note\n第二行"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-03", "a third day"),
            ("2026-10-04", "a second day first save"),
        ])
        self.assertEqual(reopened.timeline("B"),
                         [{"contact_id": "B", "on": "2026-09-30", "note": "b corrected"}])
        # Save order and duplicate count in the stored collection are preserved;
        # corrections mutate the selected records in place.
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))["followups"]
        self.assertEqual([(f["contact_id"], f["on"], f["note"]) for f in stored], [
            ("A", "2026-10-03", "a third day"),
            ("A", "2026-10-01", "a first day"),
            ("A", "2026-10-04", "a second day first save"),
            ("A", "2026-10-02", "corrected  note\n第二行"),
            ("A", "2026-10-02", "a second day second save"),
            ("B", "2026-09-30", "b corrected"),
        ])
        # The followup report and its CSV recompute from the corrected content.
        report = reopened.followup_report("2026-09-01", "2026-10-31")
        self.assertEqual([(r["contact_id"], r["on"], r["note"]) for r in report["records"]], [
            ("B", "2026-09-30", "b corrected"),
            ("A", "2026-10-01", "a first day"),
            ("A", "2026-10-02", "corrected  note\n第二行"),
            ("A", "2026-10-02", "a second day second save"),
            ("A", "2026-10-03", "a third day"),
            ("A", "2026-10-04", "a second day first save"),
        ])
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["contact_id", "name", "email", "organization", "on", "note"])
        self.assertEqual([dict(zip(rows[0], row)) for row in rows[1:]], report["records"])
        # Inactivity uses the corrected latest dates: A idles 1 day from the
        # moved 2026-10-04 record, B idles 5 from 2026-09-30, C never appears.
        inactive = reopened.inactive_contacts("2026-10-05", 3)
        self.assertEqual([(r["contact"]["contact_id"], r["last_followup"], r["idle_days"])
                          for r in inactive],
                         [("C", None, None),
                          ("B", {"contact_id": "B", "on": "2026-09-30", "note": "b corrected"}, 5)])
        rows = {r["contact"]["contact_id"]: r
                for r in reopened.inactive_contacts("2026-10-05", 1)}
        self.assertEqual(rows["A"]["last_followup"],
                         {"contact_id": "A", "on": "2026-10-04", "note": "a second day first save"})
        self.assertEqual(rows["A"]["idle_days"], 1)
        # Contacts, tags, opportunities with history and amounts, and reminders
        # are all preserved.
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.find_opportunities(), [
            {"opportunity_id": "O1", "contact_id": "A", "title": "Deal",
             "stage": "qualified", "amount": "10.00"}])
        self.assertEqual(reopened.stage_history("O1"),
                         [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-15"}])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "B", "due_on": "2026-11-05", "note": "call back"}])

    def test_correct_followups_single_field_changes_and_date_acceptance(self):
        self.seed_corrections()
        # Only the note changes: the date and contact stay as they were.
        only_note = self.app.correct_followups([
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day", "changes": {"note": "first day renamed"}}])
        self.assertEqual(only_note,
                         [{"contact_id": "A", "on": "2026-10-01", "note": "first day renamed"}])
        # Only the date changes: the note is preserved. Legal leap days, past
        # and future dates are all accepted; positions resolve per batch.
        only_on = self.app.correct_followups([
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "first day renamed", "changes": {"on": " 2024-02-29 "}},
            {"contact_id": "A", "index": 4, "expected_on": "2026-10-03",
             "expected_note": "a third day", "changes": {"on": "2099-12-31"}},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day", "changes": {"on": "1999-12-31"}},
        ])
        self.assertEqual(only_on, [
            {"contact_id": "A", "on": "2024-02-29", "note": "first day renamed"},
            {"contact_id": "A", "on": "2099-12-31", "note": "a third day"},
            {"contact_id": "B", "on": "1999-12-31", "note": "b same day"},
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2024-02-29", "first day renamed"),
            ("2026-10-02", "a second day first save"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-02", "a second day second save"),
            ("2099-12-31", "a third day"),
        ])
        self.assertEqual(reopened.timeline("B"),
                         [{"contact_id": "B", "on": "1999-12-31", "note": "b same day"}])

    def test_correct_followups_expected_values_are_verbatim(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        base = {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                "expected_note": "a first day", "changes": {"note": "renamed"}}
        # Expected values must be strings.
        for key, bad in [("expected_on", None), ("expected_on", 5),
                         ("expected_on", ["2026-10-01"]),
                         ("expected_note", None), ("expected_note", 5),
                         ("expected_note", ["a first day"])]:
            with self.assertRaises(ValueError):
                self.app.correct_followups([dict(base, **{key: bad})])
        # Strings compare verbatim, never trimmed, casefolded or reformatted.
        for key, bad in [("expected_on", " 2026-10-01 "), ("expected_on", "2026-10-1"),
                         ("expected_on", "2026-10-02"),
                         ("expected_note", " a first day"), ("expected_note", "A FIRST DAY"),
                         ("expected_note", "a  first day")]:
            with self.assertRaises(ValueError):
                self.app.correct_followups([dict(base, **{key: bad})])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_followups_requires_list_and_missing_argument_is_type_error(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        for bad in [None, {}, {"corrections": []}, "x", 5, True,
                    ({"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                      "expected_note": "a first day", "changes": {"note": "x"}},)]:
            with self.assertRaises(ValueError):
                self.app.correct_followups(bad)
            self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(TypeError):
            self.app.correct_followups()

    def test_correct_followups_validates_items_without_partial_changes(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()

        def item(**overrides):
            base = {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                    "expected_note": "a first day", "changes": {"note": "renamed"}}
            base.update(overrides)
            return base

        def without(*keys):
            return {key: value for key, value in item().items() if key not in keys}

        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [without("contact_id")], [without("index")], [without("expected_on")],
            [without("expected_note")], [without("changes")],
            [item(extra=1)],
            # changes must be a nonempty object with only on and/or note.
            [item(changes={})], [item(changes=[])], [item(changes="note")], [item(changes=None)],
            [item(changes={"contact_id": "B"})],
            [item(changes={"on": "2026-10-05", "extra": 1})],
            # contact_id trims but stays case-sensitive; blanks and non-strings reject.
            [item(contact_id="")], [item(contact_id="   ")],
            [item(contact_id=5)], [item(contact_id=None)],
            [item(contact_id="Z")], [item(contact_id="a")],
            # index must be a nonnegative integer; bools are not integers here.
            [item(index=-1)], [item(index=True)], [item(index=False)],
            [item(index=1.0)], [item(index="0")], [item(index=None)],
            # Out of range: past A's five entries, past B's single entry, and
            # any position for C, which has no followups at all.
            [item(index=5)], [item(index=99)],
            [item(contact_id="B", index=1, expected_on="2026-10-02",
                  expected_note="b same day")],
            [item(contact_id="C", expected_on="2026-10-02", expected_note="x")],
            # New dates must be real trimmed YYYY-MM-DD strings.
            [item(changes={"on": None})], [item(changes={"on": 5})],
            [item(changes={"on": ""})], [item(changes={"on": "   "})],
            [item(changes={"on": "2026-1-1"})], [item(changes={"on": "2026-02-30"})],
            [item(changes={"on": "2026-13-01"})], [item(changes={"on": "not-a-date"})],
            # New notes must be nonempty strings.
            [item(changes={"note": None})], [item(changes={"note": 5})],
            [item(changes={"note": ""})], [item(changes={"note": "   "})],
            # The same normalized contact and position may appear only once,
            # even when both items are identical.
            [item(contact_id=" A "), item()],
            [item(), item()],
            # A valid first item followed by an invalid second item rolls both back.
            [item(), item(contact_id="A", index=1, expected_on="2026-10-02",
                          expected_note="a second day first save",
                          changes={"on": "2026-02-30"})],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.correct_followups(batch)
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_followups_is_atomic_when_a_later_item_fails(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.correct_followups([
                {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                 "expected_note": "a first day", "changes": {"on": "2026-09-15"}},
                {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
                 "expected_note": "stale note", "changes": {"note": "b corrected"}},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual([r["on"] for r in reopened.timeline("A")],
                         ["2026-10-01", "2026-10-02", "2026-10-02", "2026-10-02", "2026-10-03"])
        self.assertEqual(reopened.timeline("B"),
                         [{"contact_id": "B", "on": "2026-10-02", "note": "b same day"}])

    def test_correct_followups_empty_list_and_noop_write_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.correct_followups([]), [])
        self.assertFalse(fresh_root.exists())
        self.seed_corrections()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.correct_followups([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)
        # Normalized changes equal to the stored values: report the existing
        # records without rewriting the file.
        result = self.app.correct_followups([
            {"contact_id": " A ", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day",
             "changes": {"on": " 2026-10-01 ", "note": " a first day "}},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day", "changes": {"note": "b same day"}},
        ])
        self.assertEqual(result, [
            {"contact_id": "A", "on": "2026-10-01", "note": "a first day"},
            {"contact_id": "B", "on": "2026-10-02", "note": "b same day"},
        ])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_correct_followups_empty_store_and_legacy_data(self):
        # Empty store: a nonempty batch rejects as unknown contact, creating nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.correct_followups([{"contact_id": "A", "index": 0,
                                      "expected_on": "2026-10-01", "expected_note": "x",
                                      "changes": {"note": "y"}}])
        self.assertFalse(fresh_root.exists())
        # Legacy data without a followups collection: every position is out of
        # range and the file stays byte-for-byte intact.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                "email": "a@example.test", "organization": "Books"}}}),
            encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        before = (legacy_root / "data.json").read_bytes()
        with self.assertRaises(ValueError):
            legacy.correct_followups([{"contact_id": "A", "index": 0,
                                       "expected_on": "2026-10-01", "expected_note": "x",
                                       "changes": {"note": "y"}}])
        self.assertEqual((legacy_root / "data.json").read_bytes(), before)

    def test_cli_correct_followups_object_array_and_failure(self):
        self.seed_corrections()
        payload = self.root / "corrections.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "correct-followups", str(payload)], text=True, capture_output=True)

        ok = cli({"corrections": [
            {"contact_id": " A ", "index": 1, "expected_on": "2026-10-02",
             "expected_note": "a second day first save", "changes": {"on": " 2026-10-04 "}},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day", "changes": {"note": "b corrected"}},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A", "on": "2026-10-04", "note": "a second day first save"},
            {"contact_id": "B", "on": "2026-10-02", "note": "b corrected"},
        ])
        self.assertEqual(ContactFlow(self.root).timeline("A")[-1]["on"], "2026-10-04")
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"corrections": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"corrections": [
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day", "changes": {"note": "ok"}},
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day", "changes": {"note": "dup"}},
        ]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # corrections must be a list.
        not_list = cli({"corrections": {"contact_id": "A"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # A failure against a nonexistent root leaves no directory or file behind.
        gone_root = self.root / "gone"
        gone = cli({"corrections": [{"contact_id": "A", "index": 0,
                                     "expected_on": "2026-10-01", "expected_note": "x",
                                     "changes": {"note": "y"}}]}, root=gone_root)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(gone_root.exists())

    def test_cli_correct_followups_outer_array_keeps_earlier_batches(self):
        self.seed_corrections()
        payload = self.root / "batches.json"
        # Each outer item is an independent call: the second batch's positions
        # and expected values are read against the state left by the first one.
        payload.write_text(json.dumps([
            {"corrections": [{"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                              "expected_note": "a first day", "changes": {"on": "2026-10-05"}}]},
            {"corrections": [{"contact_id": "A", "index": 0, "expected_on": "2026-10-02",
                              "expected_note": "a second day first save",
                              "changes": {"note": "repositioned"}}]},
        ]), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "correct-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            [{"contact_id": "A", "on": "2026-10-05", "note": "a first day"}],
            [{"contact_id": "A", "on": "2026-10-02", "note": "repositioned"}],
        ])
        # A later failed batch keeps the earlier successful batch (unlike
        # atomicity inside one corrections list).
        payload.write_text(json.dumps([
            {"corrections": [{"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
                              "expected_note": "b same day", "changes": {"note": "b corrected"}}]},
            {"corrections": [{"contact_id": "ZZZ", "index": 0, "expected_on": "2026-10-02",
                              "expected_note": "x", "changes": {"note": "y"}}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "correct-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2026-10-02", "repositioned"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-03", "a third day"),
            ("2026-10-05", "a first day"),
        ])
        self.assertEqual(reopened.timeline("B"),
                         [{"contact_id": "B", "on": "2026-10-02", "note": "b corrected"}])

    def test_remove_followups_positions_results_and_post_state(self):
        self.seed_corrections()
        # Indices address the pre-call timeline (date ascending, save order on
        # ties): removing index 0 and 1 of A together works, and the identical
        # twins at indices 2 and 3 are distinguished by position. B's sole
        # record rides in the same batch.
        result = self.app.remove_followups([
            {"contact_id": " A ", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day"},
            {"contact_id": "A", "index": 1, "expected_on": "2026-10-02",
             "expected_note": "a second day first save"},
            {"contact_id": "A", "index": 3, "expected_on": "2026-10-02",
             "expected_note": "a second day second save"},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day"},
        ])
        # Deleted records in request order, trimmed id, original date and note verbatim.
        self.assertEqual(result, [
            {"contact_id": "A", "on": "2026-10-01", "note": "a first day"},
            {"contact_id": "A", "on": "2026-10-02", "note": "a second day first save"},
            {"contact_id": "A", "on": "2026-10-02", "note": "a second day second save"},
            {"contact_id": "B", "on": "2026-10-02", "note": "b same day"},
        ])
        reopened = ContactFlow(self.root)
        # One of the identical twins survives, plus the later-day record; the
        # surviving content and relative save order are preserved.
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2026-10-02", "a second day second save"),
            ("2026-10-03", "a third day"),
        ])
        self.assertEqual(reopened.timeline("B"), [])
        stored = json.loads(self.app.path.read_text(encoding="utf-8"))["followups"]
        self.assertEqual([(f["contact_id"], f["on"], f["note"]) for f in stored], [
            ("A", "2026-10-03", "a third day"),
            ("A", "2026-10-02", "a second day second save"),
        ])
        # The followup report and its CSV recompute from the remaining records.
        report = reopened.followup_report("2026-09-01", "2026-10-31")
        self.assertEqual([(r["contact_id"], r["on"], r["note"]) for r in report["records"]], [
            ("A", "2026-10-02", "a second day second save"),
            ("A", "2026-10-03", "a third day"),
        ])
        rows = list(csv.reader(io.StringIO(report["csv"])))
        self.assertEqual(rows[0], ["contact_id", "name", "email", "organization", "on", "note"])
        self.assertEqual([dict(zip(rows[0], row)) for row in rows[1:]], report["records"])
        # Inactivity follows the remaining records: A idles 2 days from its
        # surviving 2026-10-03 record; B (all records deleted) and C (none to
        # begin with) both read as never followed up.
        inactive = reopened.inactive_contacts("2026-10-05", 3)
        self.assertEqual([r["contact"]["contact_id"] for r in inactive], ["B", "C"])
        self.assertTrue(all(r["last_followup"] is None and r["idle_days"] is None
                            for r in inactive))
        rows = {r["contact"]["contact_id"]: r
                for r in reopened.inactive_contacts("2026-10-05", 1)}
        self.assertEqual(rows["A"]["last_followup"],
                         {"contact_id": "A", "on": "2026-10-03", "note": "a third day"})
        self.assertEqual(rows["A"]["idle_days"], 2)
        # Contacts, tags, opportunities with history and amounts, and reminders
        # are all preserved.
        self.assertEqual(reopened.get_tags("A"), ["vip"])
        self.assertEqual(reopened.find_opportunities(), [
            {"opportunity_id": "O1", "contact_id": "A", "title": "Deal",
             "stage": "qualified", "amount": "10.00"}])
        self.assertEqual(reopened.stage_history("O1"),
                         [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-15"}])
        self.assertEqual(reopened.due_reminders("2099-01-01"),
                         [{"contact_id": "B", "due_on": "2026-11-05", "note": "call back"}])

    def test_remove_followups_earlier_deletion_never_shifts_later_position(self):
        self.seed_corrections()
        # Both items resolve against the pre-call timeline: index 0 is the
        # first-day record and index 4 is the third-day record, even though
        # deleting index 0 first would have shifted it to 3.
        result = self.app.remove_followups([
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day"},
            {"contact_id": "A", "index": 4, "expected_on": "2026-10-03",
             "expected_note": "a third day"},
        ])
        self.assertEqual([(r["on"], r["note"]) for r in result], [
            ("2026-10-01", "a first day"),
            ("2026-10-03", "a third day"),
        ])
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2026-10-02", "a second day first save"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-02", "a second day second save"),
        ])

    def test_remove_followups_deleting_every_record_drops_collection(self):
        self.seed_corrections()
        # Delete every stored record in one batch (indices are pre-call); the
        # followups collection itself then disappears from the document.
        result = self.app.remove_followups([
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day"},
            {"contact_id": "A", "index": 1, "expected_on": "2026-10-02",
             "expected_note": "a second day first save"},
            {"contact_id": "A", "index": 2, "expected_on": "2026-10-02",
             "expected_note": "a second day second save"},
            {"contact_id": "A", "index": 3, "expected_on": "2026-10-02",
             "expected_note": "a second day second save"},
            {"contact_id": "A", "index": 4, "expected_on": "2026-10-03",
             "expected_note": "a third day"},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day"},
        ])
        self.assertEqual([(r["contact_id"], r["on"], r["note"]) for r in result], [
            ("A", "2026-10-01", "a first day"),
            ("A", "2026-10-02", "a second day first save"),
            ("A", "2026-10-02", "a second day second save"),
            ("A", "2026-10-02", "a second day second save"),
            ("A", "2026-10-03", "a third day"),
            ("B", "2026-10-02", "b same day"),
        ])
        document = json.loads(self.app.path.read_text(encoding="utf-8"))
        self.assertNotIn("followups", document)
        reopened = ContactFlow(self.root)
        # No-followups behavior: empty timelines, empty reports, contacts kept.
        self.assertEqual(reopened.timeline("A"), [])
        self.assertEqual(reopened.timeline("B"), [])
        report = reopened.followup_report("2026-01-01", "2026-12-31")
        self.assertEqual(report["records"], [])
        self.assertTrue(report["csv"].endswith("\n"))
        never = reopened.inactive_contacts("2026-10-05", 1)
        self.assertEqual([(r["contact"]["contact_id"], r["last_followup"], r["idle_days"])
                          for r in never],
                         [("A", None, None), ("B", None, None), ("C", None, None)])
        # Appending still works on a store whose followups collection was dropped.
        reopened.follow_up("B", "2026-10-06", "back in touch")
        self.assertEqual([r["note"] for r in ContactFlow(self.root).timeline("B")],
                         ["back in touch"])

    def test_remove_followups_expected_values_are_verbatim(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        base = {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                "expected_note": "a first day"}
        # Expected values must be strings.
        for key, bad in [("expected_on", None), ("expected_on", 5),
                         ("expected_on", ["2026-10-01"]), ("expected_on", True),
                         ("expected_note", None), ("expected_note", 5),
                         ("expected_note", ["a first day"])]:
            with self.assertRaises(ValueError):
                self.app.remove_followups([dict(base, **{key: bad})])
        # Strings compare verbatim, never trimmed, reformatted or casefolded.
        for key, bad in [("expected_on", " 2026-10-01 "), ("expected_on", "2026-10-1"),
                         ("expected_on", "2026-10-02"),
                         ("expected_note", " a first day"), ("expected_note", "A FIRST DAY"),
                         ("expected_note", "a  first day")]:
            with self.assertRaises(ValueError):
                self.app.remove_followups([dict(base, **{key: bad})])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_remove_followups_requires_list_and_missing_argument_is_type_error(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        for bad in [None, {}, {"removals": []}, "x", 5, True,
                    ({"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                      "expected_note": "a first day"},)]:
            with self.assertRaises(ValueError):
                self.app.remove_followups(bad)
            self.assertEqual(self.app.path.read_bytes(), before)
        with self.assertRaises(TypeError):
            self.app.remove_followups()

    def test_remove_followups_validates_items_without_partial_changes(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()

        def item(**overrides):
            base = {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                    "expected_note": "a first day"}
            base.update(overrides)
            return base

        def without(*keys):
            return {key: value for key, value in item().items() if key not in keys}

        bad_batches = [
            [None], [5], ["x"], [[]], [{}],
            [without("contact_id")], [without("index")], [without("expected_on")],
            [without("expected_note")],
            [item(extra=1)],
            # contact_id trims but stays case-sensitive; blanks and non-strings reject.
            [item(contact_id="")], [item(contact_id="   ")],
            [item(contact_id=5)], [item(contact_id=None)],
            [item(contact_id="Z")], [item(contact_id="a")],
            # index must be a nonnegative integer; bools are not integers here.
            [item(index=-1)], [item(index=True)], [item(index=False)],
            [item(index=1.0)], [item(index="0")], [item(index=None)],
            # Out of range: past A's five entries, past B's single entry, and
            # any position for C, which has no followups at all.
            [item(index=5)], [item(index=99)],
            [item(contact_id="B", index=1, expected_on="2026-10-02",
                  expected_note="b same day")],
            [item(contact_id="C", expected_on="2026-10-02", expected_note="x")],
            # Expected fields must be strings and match verbatim.
            [item(expected_on=None)], [item(expected_on=20261001)],
            [item(expected_note=None)], [item(expected_note=0)],
            [item(expected_on=" 2026-10-01 ")], [item(expected_note=" a first day")],
            [item(expected_on="2026-10-02")], [item(expected_note="other")],
            # The same normalized contact and position may appear only once,
            # even when both items are identical.
            [item(contact_id=" A "), item()],
            [item(), item()],
            # A valid first item followed by an invalid second item rolls both back.
            [item(), item(contact_id="A", index=1, expected_on="2026-10-02",
                          expected_note="stale note")],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.remove_followups(batch)
            self.assertEqual(self.app.path.read_bytes(), before)

    def test_remove_followups_is_atomic_when_a_later_item_fails(self):
        self.seed_corrections()
        before = self.app.path.read_bytes()
        with self.assertRaises(ValueError):
            self.app.remove_followups([
                {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                 "expected_note": "a first day"},
                {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
                 "expected_note": "stale note"},
            ])
        self.assertEqual(self.app.path.read_bytes(), before)
        reopened = ContactFlow(self.root)
        self.assertEqual([r["on"] for r in reopened.timeline("A")],
                         ["2026-10-01", "2026-10-02", "2026-10-02", "2026-10-02", "2026-10-03"])
        self.assertEqual(reopened.timeline("B"),
                         [{"contact_id": "B", "on": "2026-10-02", "note": "b same day"}])

    def test_remove_followups_empty_list_and_fresh_store_write_nothing(self):
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        self.assertEqual(fresh.remove_followups([]), [])
        self.assertFalse(fresh_root.exists())
        self.seed_corrections()
        before = self.app.path.read_bytes()
        self.assertEqual(self.app.remove_followups([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_remove_followups_empty_store_and_legacy_data(self):
        # Empty store: a nonempty batch rejects as unknown contact, creating nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.remove_followups([{"contact_id": "A", "index": 0,
                                     "expected_on": "2026-10-01", "expected_note": "x"}])
        self.assertFalse(fresh_root.exists())
        # Legacy data without a followups collection: every position is out of
        # range and the file stays byte-for-byte intact.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps(
            {"contacts": {"A": {"contact_id": "A", "name": "Alice",
                                "email": "a@example.test", "organization": "Books"}}}),
            encoding="utf-8")
        legacy = ContactFlow(legacy_root)
        before = (legacy_root / "data.json").read_bytes()
        with self.assertRaises(ValueError):
            legacy.remove_followups([{"contact_id": "A", "index": 0,
                                      "expected_on": "2026-10-01", "expected_note": "x"}])
        self.assertEqual((legacy_root / "data.json").read_bytes(), before)

    def test_cli_remove_followups_object_array_and_failure(self):
        self.seed_corrections()
        payload = self.root / "removals.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "remove-followups", str(payload)], text=True, capture_output=True)

        ok = cli({"removals": [
            {"contact_id": " A ", "index": 1, "expected_on": "2026-10-02",
             "expected_note": "a second day first save"},
            {"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
             "expected_note": "b same day"},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            {"contact_id": "A", "on": "2026-10-02", "note": "a second day first save"},
            {"contact_id": "B", "on": "2026-10-02", "note": "b same day"},
        ])
        self.assertEqual([(r["on"], r["note"]) for r in ContactFlow(self.root).timeline("A")], [
            ("2026-10-01", "a first day"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-03", "a third day"),
        ])
        self.assertEqual(ContactFlow(self.root).timeline("B"), [])
        # Empty list prints [] and creates nothing in a fresh root.
        empty_root = self.root / "empty"
        quiet = cli({"removals": []}, root=empty_root)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(json.loads(quiet.stdout), [])
        self.assertFalse(empty_root.exists())
        # Validation failure: exit 2, empty stdout, JSON error on stderr, byte-for-byte rollback.
        before = self.app.path.read_bytes()
        failed = cli({"removals": [
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day"},
            {"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
             "expected_note": "a first day"},
        ]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing the required parameter is a TypeError surfaced through the same envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("error", json.loads(missing.stderr))
        # removals must be a list.
        not_list = cli({"removals": {"contact_id": "A"}})
        self.assertEqual(not_list.returncode, 2)
        self.assertEqual(not_list.stdout, "")
        # A failure against a nonexistent root leaves no directory or file behind.
        gone_root = self.root / "gone"
        gone = cli({"removals": [{"contact_id": "A", "index": 0,
                                  "expected_on": "2026-10-01", "expected_note": "x"}]},
                   root=gone_root)
        self.assertEqual(gone.returncode, 2)
        self.assertEqual(gone.stdout, "")
        self.assertFalse(gone_root.exists())

    def test_cli_remove_followups_outer_array_keeps_earlier_batches(self):
        self.seed_corrections()
        payload = self.root / "batches.json"
        # Each outer item is an independent call: the second batch's positions
        # are read against the state left by the first one.
        payload.write_text(json.dumps([
            {"removals": [{"contact_id": "A", "index": 0, "expected_on": "2026-10-01",
                           "expected_note": "a first day"}]},
            {"removals": [{"contact_id": "A", "index": 0, "expected_on": "2026-10-02",
                           "expected_note": "a second day first save"}]},
        ]), encoding="utf-8")
        ok = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                             "remove-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout), [
            [{"contact_id": "A", "on": "2026-10-01", "note": "a first day"}],
            [{"contact_id": "A", "on": "2026-10-02", "note": "a second day first save"}],
        ])
        # A later failed batch keeps the earlier successful batches (unlike
        # atomicity inside one removals list).
        payload.write_text(json.dumps([
            {"removals": [{"contact_id": "B", "index": 0, "expected_on": "2026-10-02",
                           "expected_note": "b same day"}]},
            {"removals": [{"contact_id": "ZZZ", "index": 0, "expected_on": "2026-10-02",
                           "expected_note": "x"}]},
        ]), encoding="utf-8")
        partial = subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(self.root),
                                  "remove-followups", str(payload)], text=True, capture_output=True)
        self.assertEqual(partial.returncode, 2)
        self.assertEqual(partial.stdout, "")
        self.assertIn("error", json.loads(partial.stderr))
        reopened = ContactFlow(self.root)
        self.assertEqual([(r["on"], r["note"]) for r in reopened.timeline("A")], [
            ("2026-10-02", "a second day second save"),
            ("2026-10-02", "a second day second save"),
            ("2026-10-03", "a third day"),
        ])
        self.assertEqual(reopened.timeline("B"), [])

    def seed_close_dates(self):
        # Two organizations; opportunities mix stages, amounts, dates and no dates.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_opportunity("O1", "A", "Deal one")        # new
        self.app.add_opportunity("O2", "A", "Deal two")        # -> qualified
        self.app.add_opportunity("O3", "A", "Deal three")      # -> won
        self.app.add_opportunity("O4", "B", "Deal four")       # new, no amount
        self.app.set_opportunity_amount("O1", "100.00")
        self.app.set_opportunity_amount("O2", "200.00")
        self.app.set_opportunity_amount("O3", "999.00")
        # O2 reaches qualified with an October history date although its
        # expected close date is elsewhere: the window must never borrow it.
        self.app.set_stage("O2", "qualified", on="2026-10-02")
        self.app.set_stage("O3", "qualified", on="2026-09-01")
        self.app.set_stage("O3", "won", on="2026-09-05")

    def test_set_close_dates_sets_trims_clears_and_persists(self):
        self.seed_close_dates()
        results = self.app.set_close_dates([
            {"opportunity_id": " O1 ", "expected_close_on": " 2026-10-05 "},
            {"opportunity_id": "O2", "expected_close_on": "2024-02-29"},  # legal leap day
            {"opportunity_id": "O3", "expected_close_on": "2031-01-30"},
            {"opportunity_id": "O4", "expected_close_on": None},          # never had one
        ])
        self.assertEqual([o["opportunity_id"] for o in results], ["O1", "O2", "O3", "O4"])
        by_id = {o["opportunity_id"]: o for o in results}
        self.assertEqual(by_id["O1"]["expected_close_on"], "2026-10-05")
        self.assertEqual(by_id["O2"]["expected_close_on"], "2024-02-29")
        self.assertEqual(by_id["O3"]["expected_close_on"], "2031-01-30")
        self.assertNotIn("expected_close_on", by_id["O4"])
        # Complete opportunities: other fields and the amount stay, and no
        # amount is invented for a deal that was never priced.
        self.assertEqual(by_id["O1"]["amount"], "100.00")
        self.assertEqual(sorted(by_id["O4"]), ["contact_id", "opportunity_id", "stage", "title"])
        # Reopening the same root shows the maintained dates, including on a terminal deal.
        reopened = ContactFlow(self.root)
        found = {o["opportunity_id"]: o for o in reopened.find_opportunities()}
        self.assertEqual(found["O1"]["expected_close_on"], "2026-10-05")
        self.assertEqual(found["O2"]["expected_close_on"], "2024-02-29")
        self.assertEqual(found["O3"]["expected_close_on"], "2031-01-30")
        self.assertEqual(found["O3"]["stage"], "won")
        self.assertNotIn("expected_close_on", found["O4"])

        # An all-no-op batch (same date and an already-absent one) does not rewrite.
        before = self.app.path.read_bytes()
        noop = self.app.set_close_dates([
            {"opportunity_id": "O1", "expected_close_on": "2026-10-05"},
            {"opportunity_id": "O4", "expected_close_on": None},
        ])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.assertEqual(noop[0]["expected_close_on"], "2026-10-05")
        self.assertNotIn("expected_close_on", noop[1])

        # Clearing removes the field from the stored object; other fields survive.
        cleared = self.app.set_close_dates([{"opportunity_id": " O1 ", "expected_close_on": None}])
        self.assertNotIn("expected_close_on", cleared[0])
        self.assertEqual(cleared[0]["amount"], "100.00")
        self.assertNotIn("expected_close_on",
                         ContactFlow(self.root).find_opportunities(stage="new")[0])

    def test_set_close_dates_validation_atomicity_and_no_creation(self):
        self.seed_close_dates()
        self.app.set_close_dates([{"opportunity_id": "O1", "expected_close_on": "2026-10-05"}])
        before = self.app.path.read_bytes()
        bad_batches = [
            "not a list",
            [{"opportunity_id": "O1"}],                                       # missing key
            [{"opportunity_id": "O1", "expected_close_on": "2026-10-05",
              "extra": 1}],                                                   # extra key
            ["not an object"],
            [{"opportunity_id": " O1 ", "expected_close_on": "2026-10-05"},
             {"opportunity_id": "O1", "expected_close_on": "2026-10-06"}],    # duplicate normalized id
            [{"opportunity_id": "  ", "expected_close_on": "2026-10-05"}],    # blank id
            [{"opportunity_id": 7, "expected_close_on": "2026-10-05"}],       # id type
            [{"opportunity_id": "O1", "expected_close_on": "2026-10"}],       # bad format
            [{"opportunity_id": "O1", "expected_close_on": "2026-02-30"}],    # not a real date
            [{"opportunity_id": "O1", "expected_close_on": 5}],               # non-string date
            [{"opportunity_id": "O1", "expected_close_on": "  "}],            # blank date
            [{"opportunity_id": "ZZZ", "expected_close_on": "2026-10-05"}],   # unknown opportunity
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                self.app.set_close_dates(batch)
            self.assertEqual(self.app.path.read_bytes(), before)
        # The one valid item in a rejected mixed batch must not have applied.
        with self.assertRaises(ValueError):
            self.app.set_close_dates([
                {"opportunity_id": "O2", "expected_close_on": "2026-12-01"},
                {"opportunity_id": "O1", "expected_close_on": "not-a-date"},
            ])
        self.assertNotIn("expected_close_on",
                         [o for o in ContactFlow(self.root).find_opportunities()
                          if o["opportunity_id"] == "O2"][0])
        # Empty batch: empty result, no write; a nonexistent root stays absent.
        self.assertEqual(self.app.set_close_dates([]), [])
        self.assertEqual(self.app.path.read_bytes(), before)
        fresh_root = self.root / "fresh"
        self.assertEqual(ContactFlow(fresh_root).set_close_dates([]), [])
        self.assertFalse(fresh_root.exists())
        # A rejected nonempty batch against a nonexistent root creates nothing.
        gone_root = self.root / "gone"
        with self.assertRaises(ValueError):
            ContactFlow(gone_root).set_close_dates(
                [{"opportunity_id": "O1", "expected_close_on": "2026-10-05"}])
        self.assertFalse(gone_root.exists())
        # Missing the required argument is a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            self.app.set_close_dates()

    def test_close_dates_survive_stage_ownership_changes_and_skips_new_imports(self):
        self.seed_close_dates()
        self.app.set_close_dates([
            {"opportunity_id": "O1", "expected_close_on": "2026-10-05"},
            {"opportunity_id": "O3", "expected_close_on": "2026-10-10"},
        ])
        # A stage change keeps the date.
        self.app.set_stage("O1", "qualified", on="2026-10-03")
        # Reopening a terminal deal keeps the date.
        self.app.reopen_opportunities(
            [{"opportunity_id": "O3", "expected_stage": "won", "on": "2026-10-08"}])
        # Transfer keeps the date.
        self.app.add_contact("C", "Cara", "c@example.test", "Games")
        self.app.transfer_opportunities(
            [{"opportunity_id": "O3", "source_contact_id": "A", "target_contact_id": "C"}])
        # A contact merge keeps dates on every moved opportunity.
        self.app.merge_contacts("C", "B")
        # A contact rename keeps dates on the opportunities that follow them.
        self.app.rename_contacts([{"old_id": "B", "new_id": "B2"}])
        found = {o["opportunity_id"]: o for o in ContactFlow(self.root).find_opportunities()}
        self.assertEqual(found["O1"]["expected_close_on"], "2026-10-05")
        self.assertEqual(found["O3"]["expected_close_on"], "2026-10-10")
        self.assertEqual(found["O3"]["contact_id"], "B2")
        # New opportunities and CSV imports never receive a date automatically.
        self.app.add_opportunity("O5", "A", "Brand new")
        import csv as csv_module
        csv_path = self.root / "opps.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as stream:
            writer = csv_module.writer(stream)
            writer.writerow(["opportunity_id", "contact_id", "title", "stage"])
            writer.writerow(["O6", "A", "Imported", "new"])
        self.app.import_opportunities(str(csv_path))
        found = {o["opportunity_id"]: o for o in ContactFlow(self.root).find_opportunities()}
        self.assertNotIn("expected_close_on", found["O5"])
        self.assertNotIn("expected_close_on", found["O6"])

    def test_forecast_report_window_includes_only_dated_open_deals(self):
        self.seed_close_dates()
        self.app.set_close_dates([
            {"opportunity_id": "O1", "expected_close_on": "2026-10-05"},   # new, 100.00
            {"opportunity_id": "O2", "expected_close_on": "2026-09-15"},   # qualified, 200.00, out
            {"opportunity_id": "O3", "expected_close_on": "2026-10-10"},   # won, excluded
            {"opportunity_id": "O4", "expected_close_on": "2026-10-20"},   # new, no amount
        ])
        probs = {"new": "50", "qualified": "25"}
        legacy = self.app.forecast_report(probs)
        # Omitting both or passing null for both keeps the original behavior exactly.
        self.assertEqual(self.app.forecast_report(probs, start_on=None, end_on=None), legacy)
        # Legacy counts every open deal: new = (100.00 + 0.00) * 50% = 50.00,
        # qualified = 200.00 * 25% = 50.00.
        self.assertEqual(legacy["total"], {"new": "50.00", "qualified": "50.00", "amount": "100.00"})

        report = self.app.forecast_report(probs, start_on="2026-10-01", end_on="2026-10-31")
        # O2 is out of range, O3 is terminal, so only O1 (priced) and O4 (0.00) count.
        self.assertEqual(report["total"], {"new": "50.00", "qualified": "0.00", "amount": "50.00"})
        rows = {row["organization"]: row for row in report["organizations"]}
        # Both filtered organizations survive: Books with its forecast, Music as a zero row.
        self.assertEqual(set(rows), {"Books", "Music"})
        self.assertEqual(rows["Books"], {"organization": "Books", "new": "50.00",
                                         "qualified": "0.00", "amount": "50.00"})
        self.assertEqual(rows["Music"], {"organization": "Music", "new": "0.00",
                                         "qualified": "0.00", "amount": "0.00"})
        self.assertEqual(report["csv"],
                         "organization,new,qualified,amount\n"
                         "Books,50.00,0.00,50.00\n"
                         "Music,0.00,0.00,0.00\n")

        # The range is inclusive: a one-day window on O1's date still includes it.
        single = self.app.forecast_report(probs, start_on="2026-10-05", end_on="2026-10-05")
        self.assertEqual(single["total"]["new"], "50.00")
        # A window matching no deal keeps zero rows for every filtered organization.
        empty_window = self.app.forecast_report(probs, start_on="2026-11-01", end_on="2026-11-30")
        self.assertEqual(empty_window["total"],
                         {"new": "0.00", "qualified": "0.00", "amount": "0.00"})
        self.assertEqual([row["organization"] for row in empty_window["organizations"]],
                         ["Books", "Music"])
        self.assertEqual(empty_window["csv"],
                         "organization,new,qualified,amount\n"
                         "Books,0.00,0.00,0.00\n"
                         "Music,0.00,0.00,0.00\n")

        # Independent per-level rounding: two organizations each have one 0.01
        # deal at 50%, so each rounds 0.5 cent up to 0.01 while the total only
        # carries the combined 1 cent: it is not derived from the rounded
        # organization rows (Books 50.00 + Music 0.01 + Games 0.01 = 50.02, but
        # the independently rounded total stays 50.01).
        self.app.add_contact("D", "Dan", "d@example.test", "Games")
        self.app.add_opportunity("O7", "D", "Small games deal")
        self.app.set_opportunity_amount("O7", "0.01")
        self.app.set_close_dates([{"opportunity_id": "O7", "expected_close_on": "2026-10-25"}])
        self.app.set_opportunity_amount("O4", "0.01")
        rounded = self.app.forecast_report(probs, start_on="2026-10-01", end_on="2026-10-31")
        rounded_rows = {row["organization"]: row for row in rounded["organizations"]}
        self.assertEqual(rounded_rows["Music"]["new"], "0.01")
        self.assertEqual(rounded_rows["Games"]["new"], "0.01")
        self.assertEqual(rounded["total"]["new"], "50.01")

        # Clearing the date immediately removes the deal from windowed forecasts.
        self.app.set_close_dates([{"opportunity_id": "O1", "expected_close_on": None}])
        after_clear = self.app.forecast_report(probs, start_on="2026-10-01", end_on="2026-10-31")
        self.assertEqual(after_clear["total"]["new"], "0.01")

        # Organization/tag filters still intersect the window, and the original
        # positional calling convention stays compatible.
        positional = self.app.forecast_report(probs, "music", None, "all",
                                              "2026-10-01", "2026-10-31")
        self.assertEqual([row["organization"] for row in positional["organizations"]], ["Music"])
        tagged = self.app.forecast_report(probs, tags=["missing"],
                                          start_on="2026-10-01", end_on="2026-10-31")
        self.assertEqual(tagged["organizations"], [])

    def test_forecast_report_window_validation_empty_store_and_readonly(self):
        probs = {"new": "50", "qualified": "25"}
        for kwargs in [
            {"start_on": "2026-10-01"},                            # only one side
            {"end_on": "2026-10-31"},
            {"start_on": None, "end_on": "2026-10-31"},
            {"start_on": "2026-10-01", "end_on": None},
            {"start_on": "bad", "end_on": "2026-10-31"},           # bad date
            {"start_on": "2026-10-01", "end_on": "2026-02-30"},    # not a real date
            {"start_on": 5, "end_on": "2026-10-31"},               # wrong type
            {"start_on": "2026-11-01", "end_on": "2026-10-31"},    # start after end
        ]:
            with self.assertRaises(ValueError):
                self.app.forecast_report(probs, **kwargs)
        # Validation runs even on an empty store, and creates nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.forecast_report(probs, start_on="2026-10-01")
        report = fresh.forecast_report(probs, start_on="2026-01-01", end_on="2026-12-31")
        self.assertEqual(report["total"], {"new": "0.00", "qualified": "0.00", "amount": "0.00"})
        self.assertEqual(report["organizations"], [])
        self.assertEqual(report["csv"], "organization,new,qualified,amount\n")
        self.assertFalse(fresh_root.exists())
        # A populated store is also untouched by a read-only windowed query.
        self.seed_close_dates()
        before = self.app.path.read_bytes()
        self.app.forecast_report(probs, start_on="2026-10-01", end_on="2026-10-31")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_set_close_dates_and_forecast_window(self):
        self.seed_close_dates()
        payload = self.root / "close.json"

        def cli(action, row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   action, str(payload)],
                                  text=True, capture_output=True)

        ok = cli("set-close-dates", {"updates": [
            {"opportunity_id": " O1 ", "expected_close_on": " 2026-10-05 "},
            {"opportunity_id": "O2", "expected_close_on": None},
        ]})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        values = json.loads(ok.stdout)
        self.assertEqual(values[0]["expected_close_on"], "2026-10-05")
        self.assertNotIn("expected_close_on", values[1])
        found = {o["opportunity_id"]: o for o in ContactFlow(self.root).find_opportunities()}
        self.assertEqual(found["O1"]["expected_close_on"], "2026-10-05")
        self.assertNotIn("expected_close_on", found["O2"])

        # Validation failure: exit 2, empty stdout, JSON error, bytes preserved.
        before = self.app.path.read_bytes()
        failed = cli("set-close-dates",
                     {"updates": [{"opportunity_id": "O2", "expected_close_on": "2026-13-01"}]})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing updates is a TypeError surfaced through the same envelope.
        missing = cli("set-close-dates", {})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))

        # An outer array is independent batch calls; a later failure keeps the first.
        array_run = cli("set-close-dates", [
            {"updates": [{"opportunity_id": "O2", "expected_close_on": "2026-09-15"}]},
            {"updates": [{"opportunity_id": "ZZZ", "expected_close_on": "2026-09-15"}]},
        ])
        self.assertEqual(array_run.returncode, 2)
        self.assertEqual(array_run.stdout, "")
        self.assertEqual(
            [o for o in ContactFlow(self.root).find_opportunities()
             if o["opportunity_id"] == "O2"][0]["expected_close_on"], "2026-09-15")

        # Empty batch prints [] against a nonexistent root and creates nothing.
        empty_root = self.root / "empty"
        empty = cli("set-close-dates", {"updates": []}, root=empty_root)
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual(json.loads(empty.stdout), [])
        self.assertFalse(empty_root.exists())

        # forecast-report accepts the window as JSON arguments.
        probs = {"new": "50", "qualified": "25"}
        window_ok = cli("forecast-report",
                        {"probabilities": probs, "start_on": "2026-10-01", "end_on": "2026-10-31"})
        self.assertEqual(window_ok.returncode, 0, window_ok.stderr)
        self.assertEqual(json.loads(window_ok.stdout)["total"],
                         {"new": "50.00", "qualified": "0.00", "amount": "50.00"})
        window_bad = cli("forecast-report", {"probabilities": probs, "start_on": "2026-10-01"})
        self.assertEqual(window_bad.returncode, 2)
        self.assertEqual(window_bad.stdout, "")
        self.assertIn("error", json.loads(window_bad.stderr))

    def test_monthly_forecast_report_groups_by_month_and_matches_windowed_total(self):
        self.seed_close_dates()
        self.app.set_close_dates([
            {"opportunity_id": "O1", "expected_close_on": "2026-10-05"},   # new, 100.00
            {"opportunity_id": "O2", "expected_close_on": "2026-11-15"},   # qualified, 200.00
            {"opportunity_id": "O3", "expected_close_on": "2026-10-10"},   # won, excluded
            {"opportunity_id": "O4", "expected_close_on": "2026-12-31"},   # new, no amount
        ])
        probs = {"new": "50", "qualified": "25"}
        report = self.app.monthly_forecast_report(probs, "2026-10-01", "2026-12-31")
        # Every month the window touches appears, including the empty December
        # (O4 has no amount, so it contributes 0.00 there).
        self.assertEqual(report["months"], [
            {"month": "2026-10", "new": "50.00", "qualified": "0.00", "amount": "50.00"},
            {"month": "2026-11", "new": "0.00", "qualified": "50.00", "amount": "50.00"},
            {"month": "2026-12", "new": "0.00", "qualified": "0.00", "amount": "0.00"},
        ])
        # The total equals the windowed forecast_report total for the same input.
        windowed = self.app.forecast_report(probs, start_on="2026-10-01", end_on="2026-12-31")
        self.assertEqual(report["total"], windowed["total"])
        self.assertEqual(report["total"], {"new": "50.00", "qualified": "50.00", "amount": "100.00"})
        self.assertEqual(set(report), {"total", "months", "csv"})
        self.assertEqual(report["csv"],
                         "month,new,qualified,amount\n"
                         "2026-10,50.00,0.00,50.00\n"
                         "2026-11,0.00,50.00,50.00\n"
                         "2026-12,0.00,0.00,0.00\n")

        # Cross-year windows list every touched month in ascending order, and
        # the first/last months only count in-window dates.
        spanning = self.app.monthly_forecast_report(probs, "2026-12-15", "2027-02-10")
        self.assertEqual([row["month"] for row in spanning["months"]],
                         ["2026-12", "2027-01", "2027-02"])
        self.assertEqual(spanning["total"], {"new": "0.00", "qualified": "0.00", "amount": "0.00"})
        # A legal leap day is accepted and lands in its month.
        self.app.set_close_dates([{"opportunity_id": "O1", "expected_close_on": "2028-02-29"}])
        leap = self.app.monthly_forecast_report(probs, "2028-02-01", "2028-02-29")
        self.assertEqual(leap["months"],
                         [{"month": "2028-02", "new": "50.00", "qualified": "0.00", "amount": "50.00"}])
        self.app.set_close_dates([{"opportunity_id": "O1", "expected_close_on": "2026-10-05"}])

        # Independent per-level rounding: two 0.01 deals at 50% in different
        # months each round half a cent up to 0.01, while the total carries
        # the combined raw cent and stays 0.01 instead of the summed 0.02.
        self.app.set_opportunity_amount("O4", "0.01")
        self.app.add_contact("D", "Dan", "d@example.test", "Games")
        self.app.add_opportunity("O7", "D", "Small games deal")
        self.app.set_opportunity_amount("O7", "0.01")
        self.app.set_close_dates([{"opportunity_id": "O7", "expected_close_on": "2026-11-20"}])
        rounded = self.app.monthly_forecast_report(probs, "2026-11-01", "2026-12-31")
        by_month = {row["month"]: row for row in rounded["months"]}
        self.assertEqual(by_month["2026-11"]["new"], "0.01")
        self.assertEqual(by_month["2026-12"]["new"], "0.01")
        self.assertEqual(rounded["total"]["new"], "0.01")

        # Organization/tag filters intersect the window exactly like forecast_report.
        music = self.app.monthly_forecast_report(probs, "2026-10-01", "2026-12-31",
                                                 organization="music")
        self.assertEqual(music["total"], {"new": "0.01", "qualified": "0.00", "amount": "0.01"})
        self.app.set_tags("A", ["vip"])
        tagged = self.app.monthly_forecast_report(probs, "2026-10-01", "2026-12-31", tags=["vip"])
        self.assertEqual(tagged["total"], windowed["total"])
        none_matched = self.app.monthly_forecast_report(probs, "2026-10-01", "2026-12-31",
                                                        tags=["missing"])
        self.assertEqual(none_matched["total"], {"new": "0.00", "qualified": "0.00", "amount": "0.00"})
        self.assertEqual([row["month"] for row in none_matched["months"]],
                         ["2026-10", "2026-11", "2026-12"])
        self.assertTrue(all(row["amount"] == "0.00" for row in none_matched["months"]))

    def test_monthly_forecast_report_validation_empty_store_and_readonly(self):
        probs = {"new": "50", "qualified": "25"}
        # Missing any required argument is a TypeError, not a ValueError.
        with self.assertRaises(TypeError):
            self.app.monthly_forecast_report()
        with self.assertRaises(TypeError):
            self.app.monthly_forecast_report(probs)
        with self.assertRaises(TypeError):
            self.app.monthly_forecast_report(probs, "2026-10-01")
        for kwargs in [
            {"probabilities": {"new": "50"}},                              # missing stage
            {"probabilities": {"new": "50", "qualified": "25", "won": "10"}},
            {"probabilities": {"new": "101", "qualified": "25"}},          # out of range
            {"probabilities": {"new": "abc", "qualified": "25"}},          # not a number
            {"probabilities": {"new": 50, "qualified": "25"}},             # not a string
            {"start_on": None},                                            # null date
            {"end_on": None},
            {"start_on": "bad"},                                           # bad date
            {"start_on": "2026-10-01", "end_on": "2026-02-30"},            # not a real date
            {"start_on": 5},                                               # wrong type
            {"start_on": "2026-11-01", "end_on": "2026-10-31"},            # start after end
            {"organization": 5},                                           # bad filter
            {"tags": "vip"},
            {"tag_mode": "some"},
        ]:
            arguments = {"probabilities": probs, "start_on": "2026-10-01", "end_on": "2026-12-31"}
            arguments.update(kwargs)
            with self.assertRaises(ValueError):
                self.app.monthly_forecast_report(**arguments)
        # Validation runs even on an empty store, and creates nothing.
        fresh_root = self.root / "fresh"
        fresh = ContactFlow(fresh_root)
        with self.assertRaises(ValueError):
            fresh.monthly_forecast_report(probs, "2026-10-01", None)
        report = fresh.monthly_forecast_report(probs, "2026-10-01", "2026-12-31")
        self.assertEqual(report["total"], {"new": "0.00", "qualified": "0.00", "amount": "0.00"})
        self.assertEqual(report["months"], [
            {"month": "2026-10", "new": "0.00", "qualified": "0.00", "amount": "0.00"},
            {"month": "2026-11", "new": "0.00", "qualified": "0.00", "amount": "0.00"},
            {"month": "2026-12", "new": "0.00", "qualified": "0.00", "amount": "0.00"},
        ])
        self.assertEqual(report["csv"],
                         "month,new,qualified,amount\n"
                         "2026-10,0.00,0.00,0.00\n"
                         "2026-11,0.00,0.00,0.00\n"
                         "2026-12,0.00,0.00,0.00\n")
        self.assertFalse(fresh_root.exists())
        # A populated store is also untouched by the read-only query.
        self.seed_close_dates()
        before = self.app.path.read_bytes()
        self.app.monthly_forecast_report(probs, "2026-10-01", "2026-12-31")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_cli_monthly_forecast_report(self):
        self.seed_close_dates()
        self.app.set_close_dates([
            {"opportunity_id": "O1", "expected_close_on": "2026-10-05"},
            {"opportunity_id": "O2", "expected_close_on": "2026-11-15"},
        ])
        payload = self.root / "monthly.json"

        def cli(action, row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   action, str(payload)],
                                  text=True, capture_output=True)

        probs = {"new": "50", "qualified": "25"}
        ok = cli("monthly-forecast-report",
                 {"probabilities": probs, "start_on": "2026-10-01", "end_on": "2026-12-31"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        result = json.loads(ok.stdout)
        self.assertEqual(result["total"], {"new": "50.00", "qualified": "50.00", "amount": "100.00"})
        self.assertEqual([row["month"] for row in result["months"]],
                         ["2026-10", "2026-11", "2026-12"])
        # An outer array is independent calls; results come back in order.
        array_ok = cli("monthly-forecast-report", [
            {"probabilities": probs, "start_on": "2026-10-01", "end_on": "2026-10-31"},
            {"probabilities": probs, "start_on": "2026-11-01", "end_on": "2026-11-30"},
        ])
        self.assertEqual(array_ok.returncode, 0, array_ok.stderr)
        values = json.loads(array_ok.stdout)
        self.assertEqual([value["total"]["amount"] for value in values], ["50.00", "50.00"])
        # Validation failure: exit 2, empty stdout, JSON error, bytes preserved.
        before = self.app.path.read_bytes()
        bad = cli("monthly-forecast-report", {"probabilities": probs, "start_on": "2026-10-01"})
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)

    def seed_qualified_durations(self):
        # Cutoff used by the tests: 2026-11-15. The raw document holds history
        # shapes the transition API cannot create (null-date exits, a stray
        # non-qualified record, future dates).
        # A (Books, vip+华东): O1 qualified twice with won then lost exits;
        # O2 a null-date entry (no row) and a dated entry with a null-date win;
        # O3 enters qualified only after the cutoff; O4 open with a future win;
        # O5 enters qualified on the cutoff itself.
        # B (Books, vip): O6 won... actually lost on the same day it entered.
        # C (Music): O7 a measured win; O8 imported qualified without history.
        # D (Books): O9 exit preceded by a stray non-qualified record; O10 two
        # rounds, the second ending in a null-date loss.
        # 陈 (Books): 陈1 still open since September.
        # E (org needing CSV escaping): O11 still open.
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "data.json").write_text(json.dumps({
            "contacts": {
                "A": {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                      "organization": "Books"},
                "B": {"contact_id": "B", "name": "Bob", "email": "b@example.test",
                      "organization": "Books"},
                "C": {"contact_id": "C", "name": "Cara", "email": "c@example.test",
                      "organization": "Music"},
                "D": {"contact_id": "D", "name": "Dan", "email": "d@example.test",
                      "organization": "Books"},
                "陈": {"contact_id": "陈", "name": "Chen", "email": "chen@example.test",
                       "organization": "Books"},
                "E": {"contact_id": "E", "name": "Eve", "email": "e@example.test",
                      "organization": 'Tea, "Q"\nLine2'}},
            "tags": {"A": ["vip", "华东"], "B": ["vip"]},
            "opportunities": {
                "O1": {"opportunity_id": "O1", "contact_id": "A", "title": "Two rounds",
                       "stage": "lost", "amount": "12.00"},
                "O2": {"opportunity_id": "O2", "contact_id": "A", "title": "Null dates",
                       "stage": "won"},
                "O3": {"opportunity_id": "O3", "contact_id": "A", "title": "Future entry",
                       "stage": "qualified"},
                "O4": {"opportunity_id": "O4", "contact_id": "A", "title": "Future win",
                       "stage": "qualified"},
                "O5": {"opportunity_id": "O5", "contact_id": "A", "title": "Same day",
                       "stage": "qualified"},
                "O6": {"opportunity_id": "O6", "contact_id": "B", "title": "Zero day",
                       "stage": "lost"},
                "O7": {"opportunity_id": "O7", "contact_id": "C", "title": "Measured",
                       "stage": "won"},
                "O8": {"opportunity_id": "O8", "contact_id": "C", "title": "Imported",
                       "stage": "qualified"},
                "O9": {"opportunity_id": "O9", "contact_id": "D", "title": "Stray record",
                       "stage": "won"},
                "O10": {"opportunity_id": "O10", "contact_id": "D", "title": "Null second exit",
                        "stage": "lost"},
                "陈1": {"opportunity_id": "陈1", "contact_id": "陈", "title": "Open",
                        "stage": "qualified"},
                "O11": {"opportunity_id": "O11", "contact_id": "E", "title": "Escaped org",
                        "stage": "qualified"}},
            "stage_history": {
                "O1": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-09-11"},
                       {"from_stage": "won", "to_stage": "qualified", "on": "2026-10-01"},
                       {"from_stage": "qualified", "to_stage": "lost", "on": "2026-10-06"}],
                "O2": [{"from_stage": "new", "to_stage": "qualified", "on": None},
                       {"from_stage": "lost", "to_stage": "qualified", "on": "2026-10-10"},
                       {"from_stage": "qualified", "to_stage": "won", "on": None}],
                "O3": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-12-01"}],
                "O4": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-11-10"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-11-20"}],
                "O5": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-11-15"}],
                "O6": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"},
                       {"from_stage": "qualified", "to_stage": "lost", "on": "2026-09-01"}],
                "O7": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-10-01"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-10-11"}],
                "O9": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-08-01"},
                       {"from_stage": "new", "to_stage": "lost", "on": "2026-08-05"},
                       {"from_stage": "qualified", "to_stage": "won", "on": "2026-08-20"}],
                "O10": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-07-01"},
                        {"from_stage": "qualified", "to_stage": "won", "on": "2026-07-05"},
                        {"from_stage": "won", "to_stage": "qualified", "on": "2026-08-01"},
                        {"from_stage": "qualified", "to_stage": "lost", "on": None}],
                "陈1": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-01"}],
                "O11": [{"from_stage": "new", "to_stage": "qualified", "on": "2026-09-15"}]}},
        ), encoding="utf-8")
        self.app = ContactFlow(self.root)

    def test_qualified_duration_report_rounds_outcomes_and_order(self):
        self.seed_qualified_durations()
        result = self.app.qualified_duration_report(" 2026-11-15 ")
        self.assertEqual(set(result), {"records", "csv"})
        rows = [(r["opportunity_id"], r["entry_index"], r["entered_on"], r["ended_on"],
                 r["outcome"], r["days"]) for r in result["records"]]
        as_of = "2026-11-15"

        def span(start, end):
            return (date.fromisoformat(end) - date.fromisoformat(start)).days

        self.assertEqual(rows, [
            ("O10", 0, "2026-07-01", "2026-07-05", "won", span("2026-07-01", "2026-07-05")),
            # Same entry day for two opportunities orders by id code point.
            ("O10", 2, "2026-08-01", None, "unknown", None),
            ("O9", 0, "2026-08-01", "2026-08-20", "won", span("2026-08-01", "2026-08-20")),
            ("O1", 0, "2026-09-01", "2026-09-11", "won", 10),
            # Same entry day orders by opportunity id code point; zero-day is 0.
            ("O6", 0, "2026-09-01", "2026-09-01", "lost", 0),
            ("陈1", 0, "2026-09-01", as_of, "open", span("2026-09-01", as_of)),
            ("O11", 0, "2026-09-15", as_of, "open", span("2026-09-15", as_of)),
            ("O1", 2, "2026-10-01", "2026-10-06", "lost", 5),
            ("O7", 0, "2026-10-01", "2026-10-11", "won", 10),
            # Null-date win: unknown, no borrowed date from the other round.
            ("O2", 1, "2026-10-10", None, "unknown", None),
            # Exit dated after the cutoff leaves the round open ended at as_of.
            ("O4", 0, "2026-11-10", as_of, "open", 5),
            ("O5", 0, "2026-11-15", as_of, "open", 0),
        ])
        # Every row carries exactly the eight specified fields; current profile
        # values are used and no amount leaks in.
        for record in result["records"]:
            self.assertEqual(set(record),
                             {"opportunity_id", "contact_id", "organization", "entry_index",
                              "entered_on", "ended_on", "outcome", "days"})
        o10 = [r for r in result["records"] if r["opportunity_id"] == "O10"]
        self.assertEqual([(r["contact_id"], r["organization"]) for r in o10],
                         [("D", "Books"), ("D", "Books")])
        # Excluded entirely: future entry and history-less import; O2's null
        # round produces no row while its dated round still does.
        ids = {r["opportunity_id"] for r in result["records"]}
        o2_rows = [r for r in result["records"] if r["opportunity_id"] == "O2"]
        self.assertEqual([r["entry_index"] for r in o2_rows], [1])
        self.assertNotIn("O3", ids)
        self.assertNotIn("O8", ids)

    def test_qualified_duration_report_csv_matches_records(self):
        self.seed_qualified_durations()
        result = self.app.qualified_duration_report("2026-11-15")
        csv_text = result["csv"]
        self.assertNotIn("\r", csv_text)
        self.assertTrue(csv_text.endswith("\n"))
        parsed = list(csv.reader(io.StringIO(csv_text), strict=True))
        self.assertEqual(parsed[0],
                         ["opportunity_id", "contact_id", "organization", "entry_index",
                          "entered_on", "ended_on", "outcome", "days"])
        self.assertEqual(len(parsed), len(result["records"]) + 1)
        for raw_row, record in zip(parsed[1:], result["records"]):
            expected = []
            for field in parsed[0]:
                value = record[field]
                expected.append("" if value is None else str(value))
            self.assertEqual(raw_row, expected)
        # Standard escaping: quoting, doubled quotes and an embedded LF.
        self.assertIn('"Tea, ""Q""\nLine2"', csv_text)
        # Unknown rows have empty ended_on and days fields.
        unknown_line = next(line for line in csv_text.splitlines()
                            if line.startswith("O2,"))
        self.assertEqual(unknown_line, "O2,A,Books,1,2026-10-10,,unknown,")

    def test_qualified_duration_report_filters_organization_tags_and_intersection(self):
        self.seed_qualified_durations()

        def ids(**filters):
            return [(r["opportunity_id"], r["entry_index"])
                    for r in self.app.qualified_duration_report("2026-11-15", **filters)["records"]]

        # Only C works at Music: just O7's single round.
        self.assertEqual(ids(organization=" music "), [("O7", 0)])
        # Books excludes the Music and escaped-org owners; every other round remains.
        self.assertEqual(len(ids(organization="Books")), 10)
        # vip covers A and B: O1 (two rounds), O2, O4, O5, O6.
        self.assertEqual(ids(tags=["VIP"]),
                         [("O1", 0), ("O6", 0), ("O1", 2), ("O2", 1), ("O4", 0), ("O5", 0)])
        # all requires every tag: A alone.
        self.assertEqual(ids(tags=["vip", "华东"]),
                         [("O1", 0), ("O1", 2), ("O2", 1), ("O4", 0), ("O5", 0)])
        # any keeps the union.
        self.assertEqual(ids(tags=["vip", "华东"], tag_mode="any"),
                         [("O1", 0), ("O6", 0), ("O1", 2), ("O2", 1), ("O4", 0), ("O5", 0)])
        # Organization and tags intersect.
        self.assertEqual(len(ids(organization="Books", tags=["华东"])), 5)
        self.assertEqual(ids(organization="Music", tags=["vip"]), [])

    def test_qualified_duration_report_uses_current_ownership_profile(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "Music")
        self.app.add_opportunity("O1", "A", "Deal")
        self.app.set_stage("O1", "qualified", on="2026-10-01")
        self.app.set_stage("O1", "won", on="2026-10-10")
        self.app.transfer_opportunities([
            {"opportunity_id": "O1", "source_contact_id": "A", "target_contact_id": "B"}])
        self.app.update_contact("B", {"organization": "Games"})
        result = self.app.qualified_duration_report("2026-11-01", organization="games")
        self.assertEqual(len(result["records"]), 1)
        record = result["records"][0]
        self.assertEqual(record["contact_id"], "B")
        self.assertEqual(record["organization"], "Games")
        self.assertEqual((record["entered_on"], record["ended_on"], record["outcome"],
                          record["days"]), ("2026-10-01", "2026-10-10", "won", 9))
        # The old owner's organization no longer matches the moved deal.
        self.assertEqual(
            self.app.qualified_duration_report("2026-11-01", organization="Books")["records"], [])

    def test_qualified_duration_report_empty_legacy_and_readonly(self):
        # No data file at all: header-only CSV, and nothing is created.
        empty_root = self.root / "empty"
        quiet = ContactFlow(empty_root)
        result = quiet.qualified_duration_report("2026-11-15")
        self.assertEqual(result["records"], [])
        self.assertEqual(
            result["csv"],
            "opportunity_id,contact_id,organization,entry_index,entered_on,ended_on,outcome,days\n")
        self.assertFalse(empty_root.exists())
        # Old data missing the opportunities, history and tags collections reads
        # as empty without error.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps({
            "contacts": {"A": {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                               "organization": "Books"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root).qualified_duration_report("2026-11-15")
        self.assertEqual(legacy["records"], [])
        self.assertTrue(legacy["csv"].endswith("\n"))
        # Queries never rewrite the bytes of populated data.
        self.seed_qualified_durations()
        before = self.app.path.read_bytes()
        self.app.qualified_duration_report("2026-11-15")
        self.app.qualified_duration_report("2026-01-01", tags=["vip"], organization="Music")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_qualified_duration_report_validates_arguments_even_when_empty(self):
        for kwargs in ({"as_of": "2026-13-01"}, {"as_of": "2026-02-30"}, {"as_of": None},
                       {"as_of": 20261115}, {"as_of": "  "}):
            with self.assertRaises(ValueError):
                self.app.qualified_duration_report(**kwargs)
        with self.assertRaises(ValueError):
            self.app.qualified_duration_report("2026-11-15", organization=7)
        with self.assertRaises(ValueError):
            self.app.qualified_duration_report("2026-11-15", tags=[" "])
        with self.assertRaises(ValueError):
            self.app.qualified_duration_report("2026-11-15", tag_mode="off")
        # Filter validation runs before any data read, so an empty store rejects too.
        with self.assertRaises(ValueError):
            ContactFlow(self.root / "missing").qualified_duration_report(
                "2026-11-15", tags=[" "])
        # A missing required argument is a plain TypeError from the signature.
        with self.assertRaises(TypeError):
            self.app.qualified_duration_report()

    def test_cli_qualified_duration_report_success_and_failure(self):
        self.seed_qualified_durations()
        payload = self.root / "qualified.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "qualified-duration-report", str(payload)],
                                  text=True, capture_output=True)

        ok = cli({"as_of": " 2026-11-15 "})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(set(value), {"records", "csv"})
        self.assertEqual(len(value["records"]), 12)
        # Outer array runs one independent query per object.
        batch = cli([{"as_of": "2026-11-15", "tags": ["vip"]},
                     {"as_of": "2026-11-15", "organization": "Music"}])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual(len(values[0]["records"]), 6)
        self.assertEqual([r["opportunity_id"] for r in values[1]["records"]], ["O7"])
        # Bad date: exit 2, empty stdout, JSON error on stderr, data untouched.
        before = self.app.path.read_bytes()
        failed = cli({"as_of": "2026-11-15", "tag_mode": "off"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing as_of is a TypeError through the same error envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # A query over a nonexistent root returns the header-only CSV and
        # creates nothing.
        empty = self.root / "empty"
        quiet = cli({"as_of": "2026-11-15"}, root=empty)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        quiet_value = json.loads(quiet.stdout)
        self.assertEqual(quiet_value["records"], [])
        self.assertEqual(
            quiet_value["csv"],
            "opportunity_id,contact_id,organization,entry_index,entered_on,ended_on,outcome,days\n")
        self.assertFalse(empty.exists())

    def test_qualified_duration_summary_totals_groups_and_stats(self):
        self.seed_qualified_durations()
        result = self.app.qualified_duration_summary(" 2026-11-15 ")
        self.assertEqual(set(result), {"total", "organizations", "csv"})
        # The same twelve rounds as the detail report: ten measurable, two
        # unknown (O10's null-date loss and O2's null-date win).
        self.assertEqual(result["total"], {
            "rounds": 12, "measured": 10, "unmeasured": 2,
            # Days 0,0,4,5,5,10,10,19,61,75: sum 189, middle two 5 and 10,
            # the ceil(0.9*10)=9th item counting from one is 61.
            "average": "18.90", "median": "7.50", "p90": 61})
        self.assertEqual(result["organizations"], [
            {"organization": "Books", "rounds": 10, "measured": 8, "unmeasured": 2,
             # Days 0,0,4,5,5,10,19,75: sum 118, middle two 5 and 5.
             "average": "14.75", "median": "5.00", "p90": 75},
            {"organization": "Music", "rounds": 1, "measured": 1, "unmeasured": 0,
             "average": "10.00", "median": "10.00", "p90": 10},
            {"organization": 'Tea, "Q"\nLine2', "rounds": 1, "measured": 1, "unmeasured": 0,
             "average": "61.00", "median": "61.00", "p90": 61}])
        # Every organization row carries exactly the display name plus the six
        # total fields; groups order by the casefolded organization.
        for row in result["organizations"]:
            self.assertEqual(set(row), {"organization", "rounds", "measured", "unmeasured",
                                        "average", "median", "p90"})
        # The summary aggregates exactly the rounds the detail report selects.
        detail = self.app.qualified_duration_report("2026-11-15")["records"]
        days = sorted(r["days"] for r in detail if r["days"] is not None)
        self.assertEqual(len(detail), result["total"]["rounds"])
        self.assertEqual(len(days), result["total"]["measured"])
        self.assertEqual(days[-1], 75)

    def test_qualified_duration_summary_rounding_and_p90(self):
        # Seven zero-day rounds and one one-day round: average 1/8 = 0.125
        # rounds half-up to 0.13; the median of eight values averages the two
        # middle zeros; the p90 position ceil(0.9*8)=8 counting from one lands
        # on the one-day round.
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        for index in range(7):
            opportunity_id = "O%d" % index
            self.app.add_opportunity(opportunity_id, "A", "Deal %d" % index)
            self.app.set_stage(opportunity_id, "qualified", on="2026-10-01")
            self.app.set_stage(opportunity_id, "won", on="2026-10-01")
        self.app.add_opportunity("O7", "A", "Deal 7")
        self.app.set_stage("O7", "qualified", on="2026-10-01")
        self.app.set_stage("O7", "won", on="2026-10-02")
        result = self.app.qualified_duration_summary("2026-11-15")
        self.assertEqual(result["total"], {
            "rounds": 8, "measured": 8, "unmeasured": 0,
            "average": "0.13", "median": "0.00", "p90": 1})
        self.assertEqual(result["organizations"][0]["average"], "0.13")
        # One more zero-day round: 1/9 = 0.111... rounds down to 0.11.
        self.app.add_opportunity("O8", "A", "Deal 8")
        self.app.set_stage("O8", "qualified", on="2026-10-01")
        self.app.set_stage("O8", "won", on="2026-10-01")
        self.assertEqual(self.app.qualified_duration_summary("2026-11-15")["total"]["average"],
                         "0.11")

    def test_qualified_duration_summary_csv_matches_organizations(self):
        self.seed_qualified_durations()
        result = self.app.qualified_duration_summary("2026-11-15")
        csv_text = result["csv"]
        self.assertNotIn("\r", csv_text)
        self.assertTrue(csv_text.endswith("\n"))
        parsed = list(csv.reader(io.StringIO(csv_text), strict=True))
        self.assertEqual(parsed[0], ["organization", "rounds", "measured", "unmeasured",
                                     "average", "median", "p90"])
        self.assertEqual(len(parsed), len(result["organizations"]) + 1)
        for raw_row, row in zip(parsed[1:], result["organizations"]):
            self.assertEqual(raw_row, ["" if row[field] is None else str(row[field])
                                       for field in parsed[0]])
        # Standard escaping: quoting, doubled quotes and an embedded LF.
        self.assertIn('"Tea, ""Q""\nLine2",1,1,0,61.00,61.00,61\n', csv_text)
        self.assertEqual(csv_text.splitlines()[1], "Books,10,8,2,14.75,5.00,75")

    def test_qualified_duration_summary_filters_and_empty(self):
        self.seed_qualified_durations()
        # Only C works at Music: O7's single measured round.
        music = self.app.qualified_duration_summary("2026-11-15", organization=" music ")
        self.assertEqual(music["total"], {"rounds": 1, "measured": 1, "unmeasured": 0,
                                          "average": "10.00", "median": "10.00", "p90": 10})
        self.assertEqual([row["organization"] for row in music["organizations"]], ["Music"])
        # vip covers A and B: O1 twice, O2, O4, O5, O6 — one unknown (O2).
        vip = self.app.qualified_duration_summary("2026-11-15", tags=["VIP"])
        self.assertEqual(vip["total"], {"rounds": 6, "measured": 5, "unmeasured": 1,
                                        # Days 0,0,5,5,10: sum 20, median 5.
                                        "average": "4.00", "median": "5.00", "p90": 10})
        self.assertEqual([row["organization"] for row in vip["organizations"]], ["Books"])
        # No selected round: zero counts, null statistics, no groups, header only.
        none = self.app.qualified_duration_summary("2026-11-15", organization="Music",
                                                   tags=["vip"])
        self.assertEqual(none["total"], {"rounds": 0, "measured": 0, "unmeasured": 0,
                                         "average": None, "median": None, "p90": None})
        self.assertEqual(none["organizations"], [])
        self.assertEqual(none["csv"],
                         "organization,rounds,measured,unmeasured,average,median,p90\n")
        # Every round unmeasurable: counts still report, statistics are null.
        self.app.add_contact("Z", "Zed", "z@example.test", "Void")
        self.app.add_opportunity("OZ", "Z", "Null exit")
        self.app.set_stage("OZ", "qualified", on="2026-10-01")
        self.app.set_stage("OZ", "won", on=None)
        void = self.app.qualified_duration_summary("2026-11-15", organization="void")
        self.assertEqual(void["total"], {"rounds": 1, "measured": 0, "unmeasured": 1,
                                         "average": None, "median": None, "p90": None})
        self.assertEqual(void["csv"],
                         "organization,rounds,measured,unmeasured,average,median,p90\n"
                         "Void,1,0,1,,,\n")

    def test_qualified_duration_summary_uses_current_ownership_profile(self):
        self.app.add_contact("A", "Alice", "a@example.test", "Books")
        self.app.add_contact("B", "Bob", "b@example.test", "books")
        self.app.add_opportunity("O1", "A", "Deal 1")
        self.app.set_stage("O1", "qualified", on="2026-10-01")
        self.app.set_stage("O1", "won", on="2026-10-10")
        self.app.add_opportunity("O2", "B", "Deal 2")
        self.app.set_stage("O2", "qualified", on="2026-10-01")
        self.app.set_stage("O2", "won", on="2026-10-06")
        # Same casefolded organization from two contacts: one group, display
        # name is the code-point-smallest original value among the rounds.
        result = self.app.qualified_duration_summary("2026-11-01")
        self.assertEqual(result["organizations"], [
            {"organization": "Books", "rounds": 2, "measured": 2, "unmeasured": 0,
             "average": "7.00", "median": "7.00", "p90": 9}])
        # After a transfer and a profile update both rounds regroup to the new
        # organization of the current owner.
        self.app.transfer_opportunities([
            {"opportunity_id": "O1", "source_contact_id": "A", "target_contact_id": "B"}])
        self.app.update_contact("B", {"organization": "Games"})
        moved = self.app.qualified_duration_summary("2026-11-01")
        self.assertEqual([row["organization"] for row in moved["organizations"]], ["Games"])
        self.assertEqual(moved["total"]["rounds"], 2)
        self.assertEqual(self.app.qualified_duration_summary(
            "2026-11-01", organization="Books")["total"]["rounds"], 0)
        # Reopening the root recomputes from the same stored data.
        self.assertEqual(ContactFlow(self.root).qualified_duration_summary("2026-11-01"),
                         moved)

    def test_qualified_duration_summary_empty_legacy_and_readonly(self):
        # No data file at all: zero counts, null statistics, header-only CSV,
        # and nothing is created.
        empty_root = self.root / "empty"
        quiet = ContactFlow(empty_root)
        result = quiet.qualified_duration_summary("2026-11-15")
        self.assertEqual(result["total"], {"rounds": 0, "measured": 0, "unmeasured": 0,
                                           "average": None, "median": None, "p90": None})
        self.assertEqual(result["organizations"], [])
        self.assertEqual(result["csv"],
                         "organization,rounds,measured,unmeasured,average,median,p90\n")
        self.assertFalse(empty_root.exists())
        # Old data missing the opportunities, history and tags collections
        # reads as empty without error.
        legacy_root = self.root / "legacy"
        legacy_root.mkdir()
        (legacy_root / "data.json").write_text(json.dumps({
            "contacts": {"A": {"contact_id": "A", "name": "Alice", "email": "a@example.test",
                               "organization": "Books"}}}), encoding="utf-8")
        legacy = ContactFlow(legacy_root).qualified_duration_summary("2026-11-15")
        self.assertEqual(legacy["total"]["rounds"], 0)
        self.assertTrue(legacy["csv"].endswith("\n"))
        # Queries never rewrite the bytes of populated data.
        self.seed_qualified_durations()
        before = self.app.path.read_bytes()
        self.app.qualified_duration_summary("2026-11-15")
        self.app.qualified_duration_summary("2026-01-01", tags=["vip"], organization="Music")
        self.assertEqual(self.app.path.read_bytes(), before)

    def test_qualified_duration_summary_validates_arguments_even_when_empty(self):
        for kwargs in ({"as_of": "2026-13-01"}, {"as_of": "2026-02-30"}, {"as_of": None},
                       {"as_of": 20261115}, {"as_of": "  "}):
            with self.assertRaises(ValueError):
                self.app.qualified_duration_summary(**kwargs)
        with self.assertRaises(ValueError):
            self.app.qualified_duration_summary("2026-11-15", organization=7)
        with self.assertRaises(ValueError):
            self.app.qualified_duration_summary("2026-11-15", tags=[" "])
        with self.assertRaises(ValueError):
            self.app.qualified_duration_summary("2026-11-15", tag_mode="off")
        # Filter validation runs before any data read, so an empty store rejects too.
        with self.assertRaises(ValueError):
            ContactFlow(self.root / "missing").qualified_duration_summary(
                "2026-11-15", tags=[" "])
        # A missing required argument is a plain TypeError from the signature.
        with self.assertRaises(TypeError):
            self.app.qualified_duration_summary()

    def test_cli_qualified_duration_summary_success_and_failure(self):
        self.seed_qualified_durations()
        payload = self.root / "qualified-summary.json"

        def cli(row, root=self.root):
            payload.write_text(json.dumps(row), encoding="utf-8")
            return subprocess.run([sys.executable, "-m", "contact_flow", "--root", str(root),
                                   "qualified-duration-summary", str(payload)],
                                  text=True, capture_output=True)

        ok = cli({"as_of": " 2026-11-15 "})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        value = json.loads(ok.stdout)
        self.assertEqual(set(value), {"total", "organizations", "csv"})
        self.assertEqual(value["total"]["rounds"], 12)
        # Outer array runs one independent query per object.
        batch = cli([{"as_of": "2026-11-15", "tags": ["vip"]},
                     {"as_of": "2026-11-15", "organization": "Music"}])
        self.assertEqual(batch.returncode, 0, batch.stderr)
        values = json.loads(batch.stdout)
        self.assertEqual(values[0]["total"]["rounds"], 6)
        self.assertEqual(values[1]["total"]["rounds"], 1)
        # Bad filter: exit 2, empty stdout, JSON error on stderr, data untouched.
        before = self.app.path.read_bytes()
        failed = cli({"as_of": "2026-11-15", "tag_mode": "off"})
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", json.loads(failed.stderr))
        self.assertEqual(self.app.path.read_bytes(), before)
        # Missing as_of is a TypeError through the same error envelope.
        missing = cli({})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stderr))
        # A query over a nonexistent root returns the header-only CSV and
        # creates nothing.
        empty = self.root / "empty"
        quiet = cli({"as_of": "2026-11-15"}, root=empty)
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        quiet_value = json.loads(quiet.stdout)
        self.assertEqual(quiet_value["organizations"], [])
        self.assertEqual(quiet_value["csv"],
                         "organization,rounds,measured,unmeasured,average,median,p90\n")
        self.assertFalse(empty.exists())

if __name__ == "__main__":
    unittest.main()
