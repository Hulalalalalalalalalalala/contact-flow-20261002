import csv
import io
import unicodedata
from datetime import date
from .storage import JsonStore, text, calendar_day, positive

CONTACT_FIELDS = ("contact_id", "name", "email", "organization")
FOLLOWUP_FIELDS = ("contact_id", "on", "note")
OPPORTUNITY_FIELDS = ("opportunity_id", "contact_id", "title", "stage")
UPDATABLE_FIELDS = ("name", "email", "organization")
STAGES = ("new", "qualified", "won", "lost")
STAGE_TRANSITIONS = {"new": ("qualified", "lost"), "qualified": ("won", "lost")}

def normalize_tags(value):
    if not isinstance(value, list):
        raise ValueError("tags must be a list")
    tags = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("each tag must be a string")
        tag = item.strip().casefold()
        if not tag:
            raise ValueError("tags must be nonempty strings")
        tags.add(tag)
    return sorted(tags)

class ContactFlow(JsonStore):
    def add_contact(self, contact_id, name, email, organization):
        contact_id, name = text(contact_id, "contact_id"), text(name, "name")
        email, organization = text(email, "email").lower(), text(organization, "organization")
        if email.count("@") != 1 or any(c.isspace() for c in email) or not all(email.split("@")):
            raise ValueError("invalid email")
        data = self._read()
        contacts = data.setdefault("contacts", {})
        if contact_id in contacts or any(c["email"] == email for c in contacts.values()):
            raise ValueError("contact id or email already exists")
        contact = {"contact_id": contact_id, "name": name, "email": email, "organization": organization}
        contacts[contact_id] = contact
        self._write(data)
        return contact

    def update_contact(self, contact_id, changes):
        contact_id = text(contact_id, "contact_id")
        normalized = self._normalize_changes(changes)
        data = self._read()
        contacts = data.get("contacts", {})
        contact = contacts.get(contact_id)
        if contact is None:
            raise ValueError("unknown contact")
        new_email = normalized.get("email")
        if new_email is not None and new_email != contact["email"] and any(
                other["email"] == new_email for other_id, other in contacts.items() if other_id != contact_id):
            raise ValueError("contact id or email already exists")
        # Everything validates before this point; a fully identical update reports the contact
        # without rewriting the file, so rejected and no-op calls never create the data directory.
        if all(contact[key] == value for key, value in normalized.items()):
            return contact
        contact.update(normalized)
        self._write(data)
        return contact

    @staticmethod
    def _normalize_changes(changes):
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a nonempty object")
        normalized = {}
        for key, value in changes.items():
            if key not in UPDATABLE_FIELDS:
                raise ValueError("changes contains an unsupported field: " + str(key))
            # None and other non-strings can never clear a field; a trimmed value must stay nonempty.
            clean = text(value, key)
            if key == "email":
                clean = clean.lower()
                if clean.count("@") != 1 or any(c.isspace() for c in clean) or not all(clean.split("@")):
                    raise ValueError("invalid email")
            normalized[key] = clean
        return normalized

    def update_contacts(self, updates):
        # The whole batch validates before any contact changes, so emails can be
        # swapped or cycled within the batch while final duplicates still reject it.
        if not isinstance(updates, list):
            raise ValueError("updates must be a list")
        if not updates:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        entries = []
        seen = set()
        for item in updates:
            if not isinstance(item, dict) or set(item) != {"contact_id", "changes"}:
                raise ValueError("each update must be an object with exactly contact_id and changes")
            contact_id = text(item["contact_id"], "contact_id")
            if contact_id in seen:
                raise ValueError("duplicate contact id in updates")
            seen.add(contact_id)
            entries.append((contact_id, self._normalize_changes(item["changes"])))
        data = self._read()
        contacts = data.get("contacts", {})
        planned = []
        updated_by_id = {}
        for contact_id, normalized in entries:
            contact = contacts.get(contact_id)
            if contact is None:
                raise ValueError("unknown contact")
            updated = dict(contact)
            updated.update(normalized)
            planned.append((contact, updated))
            updated_by_id[contact_id] = updated
        # Uniqueness is judged on the final whole-store state, including non-participants;
        # members swapping or cycling emails among themselves therefore stays unique.
        final_emails = [updated_by_id[cid]["email"] if cid in updated_by_id else contact["email"]
                        for cid, contact in contacts.items()]
        if len(set(final_emails)) != len(final_emails):
            raise ValueError("contact id or email already exists")
        # Results are the complete contacts in input order, unchanged when the batch is a no-op.
        results = [updated for _, updated in planned]
        if all(contact == updated for contact, updated in planned):
            return results
        for contact, updated in planned:
            contact.update(updated)
        self._write(data)
        return results

    def import_contacts(self, csv_path):
        if not isinstance(csv_path, str) or not csv_path.strip():
            raise ValueError("csv_path must be a nonempty string")
        with open(csv_path, encoding="utf-8-sig", newline="") as stream:
            try:
                content = stream.read()
            except UnicodeDecodeError as error:
                raise ValueError("CSV file must be valid UTF-8") from error
        try:
            rows = csv.reader(io.StringIO(content), strict=True)
            header = next(rows, None)
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error
        if header is None:
            raise ValueError("CSV file is empty")
        if len(header) != len(CONTACT_FIELDS) or set(header) != set(CONTACT_FIELDS) or len(set(header)) != len(header):
            raise ValueError("CSV header must contain exactly contact_id,name,email,organization in any order")

        data = self._read()
        contacts = data.setdefault("contacts", {})
        existing_emails = {c["email"] for c in contacts.values()}
        seen_ids = set()
        seen_emails = set()
        imported = []
        try:
            for row in rows:
                if not row:
                    continue
                if len(row) != len(header):
                    raise ValueError("each CSV record must have %d fields" % len(header))
                values = dict(zip(header, row))
                contact_id = text(values["contact_id"], "contact_id")
                name = text(values["name"], "name")
                organization = text(values["organization"], "organization")
                email = text(values["email"], "email").lower()
                if email.count("@") != 1 or any(c.isspace() for c in email) or not all(email.split("@")):
                    raise ValueError("invalid email")
                if contact_id in contacts or email in existing_emails:
                    raise ValueError("contact id or email already exists")
                if contact_id in seen_ids or email in seen_emails:
                    raise ValueError("duplicate contact id or email within import file")
                seen_ids.add(contact_id)
                seen_emails.add(email)
                contact = {"contact_id": contact_id, "name": name, "email": email, "organization": organization}
                contacts[contact_id] = contact
                imported.append(contact)
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error
        if imported:
            self._write(data)
        return imported

    def import_followups(self, csv_path):
        if not isinstance(csv_path, str) or not csv_path.strip():
            raise ValueError("csv_path must be a nonempty string")
        with open(csv_path, encoding="utf-8-sig", newline="") as stream:
            try:
                content = stream.read()
            except UnicodeDecodeError as error:
                raise ValueError("CSV file must be valid UTF-8") from error
        try:
            rows = csv.reader(io.StringIO(content), strict=True)
            header = next(rows, None)
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error
        if header is None:
            raise ValueError("CSV file is empty")
        if (len(header) != len(FOLLOWUP_FIELDS) or set(header) != set(FOLLOWUP_FIELDS)
                or len(set(header)) != len(header)):
            raise ValueError("CSV header must contain exactly contact_id,on,note in any order")

        records = []
        try:
            for row in rows:
                if not row:
                    continue
                if len(row) != len(header):
                    raise ValueError("each CSV record must have %d fields" % len(header))
                values = dict(zip(header, row))
                contact_id = text(values["contact_id"], "contact_id")
                on = calendar_day(values["on"], "on")
                note = text(values["note"], "note")
                records.append({"contact_id": contact_id, "on": on, "note": note})
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error

        data = self._read()
        contacts = data.get("contacts", {})
        # The whole batch validates first, so an unknown contact rejects every row.
        for entry in records:
            if entry["contact_id"] not in contacts:
                raise ValueError("unknown contact")
        imported = []
        if records:
            store = data.setdefault("followups", [])
            for entry in records:
                # Identical rows, within the batch or against existing data, are kept verbatim.
                store.append(dict(entry))
                imported.append(dict(entry))
            self._write(data)
        return imported

    def import_opportunities(self, csv_path):
        if not isinstance(csv_path, str) or not csv_path.strip():
            raise ValueError("csv_path must be a nonempty string")
        with open(csv_path, encoding="utf-8-sig", newline="") as stream:
            try:
                content = stream.read()
            except UnicodeDecodeError as error:
                raise ValueError("CSV file must be valid UTF-8") from error
        try:
            rows = csv.reader(io.StringIO(content), strict=True)
            header = next(rows, None)
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error
        if header is None:
            raise ValueError("CSV file is empty")
        if (len(header) != len(OPPORTUNITY_FIELDS) or set(header) != set(OPPORTUNITY_FIELDS)
                or len(set(header)) != len(header)):
            raise ValueError(
                "CSV header must contain exactly opportunity_id,contact_id,title,stage in any order")

        records = []
        try:
            for row in rows:
                if not row:
                    continue
                if len(row) != len(header):
                    raise ValueError("each CSV record must have %d fields" % len(header))
                values = dict(zip(header, row))
                opportunity_id = text(values["opportunity_id"], "opportunity_id")
                contact_id = text(values["contact_id"], "contact_id")
                title = text(values["title"], "title")
                stage = text(values["stage"], "stage")
                # Historical stages import verbatim; transition rules apply only to set_stage.
                if stage not in STAGES:
                    raise ValueError("invalid stage")
                records.append({"opportunity_id": opportunity_id, "contact_id": contact_id,
                                "title": title, "stage": stage})
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error

        data = self._read()
        contacts = data.get("contacts", {})
        opportunities = data.get("opportunities", {})
        # The whole batch validates before anything is stored, so one bad row rejects all.
        seen_ids = set()
        for entry in records:
            if entry["contact_id"] not in contacts:
                raise ValueError("unknown contact")
            if entry["opportunity_id"] in opportunities or entry["opportunity_id"] in seen_ids:
                raise ValueError("opportunity id already exists")
            seen_ids.add(entry["opportunity_id"])
        if records:
            store = data.setdefault("opportunities", {})
            for entry in records:
                store[entry["opportunity_id"]] = dict(entry)
            self._write(data)
        return records

    def follow_up(self, contact_id, on, note):
        note = text(note, "note")
        on = date.fromisoformat(on).isoformat()
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        entry = {"contact_id": contact_id, "on": on, "note": note}
        data.setdefault("followups", []).append(entry)
        self._write(data)
        return entry

    def set_reminder(self, contact_id, due_on, note):
        contact_id = text(contact_id, "contact_id")
        note = text(note, "note")
        due_on = calendar_day(due_on, "due_on")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        # Each contact keeps at most one reminder; setting again replaces the whole entry.
        reminder = {"contact_id": contact_id, "due_on": due_on, "note": note}
        data.setdefault("reminders", {})[contact_id] = reminder
        self._write(data)
        return dict(reminder)

    def clear_reminder(self, contact_id):
        contact_id = text(contact_id, "contact_id")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        store = data.get("reminders", {})
        if contact_id not in store:
            # Nothing to clear: report False without touching the file.
            return False
        del store[contact_id]
        if not store:
            data.pop("reminders", None)
        self._write(data)
        return True

    def complete_reminder(self, contact_id, on, note, next_reminder=None):
        contact_id = text(contact_id, "contact_id")
        note = text(note, "note")
        on = calendar_day(on, "on")
        pending = None
        if next_reminder is not None:
            if not isinstance(next_reminder, dict) or set(next_reminder) != {"due_on", "note"}:
                raise ValueError("next_reminder must be an object with exactly due_on and note")
            due_on = calendar_day(next_reminder["due_on"], "due_on")
            if due_on <= on:
                raise ValueError("next reminder due_on must be later than the completion date")
            pending = {"contact_id": contact_id, "due_on": due_on,
                       "note": text(next_reminder["note"], "note")}
        # Everything validates before this point, so rejected calls never touch the file.
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        store = data.get("reminders", {})
        if contact_id not in store:
            raise ValueError("no current reminder")
        # Followup append and reminder clear/replace commit in a single write.
        entry = {"contact_id": contact_id, "on": on, "note": note}
        data.setdefault("followups", []).append(entry)
        if pending is None:
            del store[contact_id]
            if not store:
                data.pop("reminders", None)
        else:
            store[contact_id] = pending
        self._write(data)
        return {"followup": dict(entry),
                "reminder": dict(pending) if pending is not None else None}

    def due_reminders(self, as_of):
        as_of = calendar_day(as_of, "as_of")
        data = self._read()
        due = [dict(reminder) for reminder in data.get("reminders", {}).values()
               if reminder["due_on"] <= as_of]
        # Ascending due date; the same day orders by contact id code point.
        return sorted(due, key=lambda reminder: (reminder["due_on"], reminder["contact_id"]))

    def set_tags(self, contact_id, tags):
        contact_id = text(contact_id, "contact_id")
        tags = normalize_tags(tags)
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        store = data.setdefault("tags", {})
        if tags:
            store[contact_id] = tags
        else:
            store.pop(contact_id, None)
        if not store:
            data.pop("tags", None)
        self._write(data)
        return list(tags)

    def get_tags(self, contact_id):
        contact_id = text(contact_id, "contact_id")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return list(data.get("tags", {}).get(contact_id, []))

    def add_opportunity(self, opportunity_id, contact_id, title):
        opportunity_id = text(opportunity_id, "opportunity_id")
        title = text(title, "title")
        contact_id = text(contact_id, "contact_id")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        opportunities = data.setdefault("opportunities", {})
        if opportunity_id in opportunities:
            raise ValueError("opportunity id already exists")
        opportunity = {"opportunity_id": opportunity_id, "contact_id": contact_id, "title": title, "stage": "new"}
        opportunities[opportunity_id] = opportunity
        self._write(data)
        return {"opportunity_id": opportunity_id, "contact_id": contact_id, "title": title, "stage": "new"}

    def set_stage(self, opportunity_id, stage):
        opportunity_id = text(opportunity_id, "opportunity_id")
        stage = text(stage, "stage")
        if stage not in STAGES:
            raise ValueError("invalid stage")
        data = self._read()
        opportunity = data.get("opportunities", {}).get(opportunity_id)
        if opportunity is None:
            raise ValueError("unknown opportunity")
        current = opportunity["stage"]
        if stage != current and stage not in STAGE_TRANSITIONS.get(current, ()):
            raise ValueError("invalid stage transition")
        if stage != current:
            opportunity["stage"] = stage
            self._write(data)
        return dict(opportunity)

    def find_opportunities(self, contact_id=None, stage=None):
        if contact_id is not None:
            contact_id = text(contact_id, "contact_id")
        if stage is not None:
            stage = text(stage, "stage")
            if stage not in STAGES:
                raise ValueError("invalid stage")
        data = self._read()
        contacts = data.get("contacts", {})
        if contact_id is not None and contact_id not in contacts:
            raise ValueError("unknown contact")

        def matches(opportunity):
            if contact_id is not None and opportunity["contact_id"] != contact_id:
                return False
            if stage is not None and opportunity["stage"] != stage:
                return False
            return True

        return [dict(opportunity) for opportunity in sorted(
            (o for o in data.get("opportunities", {}).values() if matches(o)),
            key=lambda o: o["opportunity_id"])]

    def merge_contacts(self, source_id, target_id):
        source_id, target_id = text(source_id, "source_id"), text(target_id, "target_id")
        if source_id == target_id:
            raise ValueError("source and target must differ")
        data = self._read()
        contacts = data.get("contacts", {})
        if source_id not in contacts or target_id not in contacts:
            raise ValueError("unknown contact")
        moved = 0
        for entry in data.get("followups", []):
            if entry["contact_id"] == source_id:
                entry["contact_id"] = target_id
                moved += 1
        for opportunity in data.get("opportunities", {}).values():
            if opportunity["contact_id"] == source_id:
                opportunity["contact_id"] = target_id
        tag_store = data.setdefault("tags", {})
        merged_tags = sorted(set(tag_store.get(target_id, [])) | set(tag_store.pop(source_id, [])))
        if merged_tags:
            tag_store[target_id] = merged_tags
        else:
            tag_store.pop(target_id, None)
        if not tag_store:
            data.pop("tags", None)
        reminder_store = data.get("reminders")
        if reminder_store is not None:
            source_reminder = reminder_store.pop(source_id, None)
            target_reminder = reminder_store.get(target_id)
            # One side present keeps that one; both present keeps the earlier due
            # date, and a tie keeps the target's original reminder.
            chosen = target_reminder
            if source_reminder is not None and (
                    target_reminder is None or source_reminder["due_on"] < target_reminder["due_on"]):
                chosen = source_reminder
            if chosen is not None:
                chosen = dict(chosen)
                chosen["contact_id"] = target_id
                reminder_store[target_id] = chosen
            else:
                reminder_store.pop(target_id, None)
            if not reminder_store:
                data.pop("reminders", None)
        del contacts[source_id]
        self._write(data)
        return {"contact": contacts[target_id], "moved_followups": moved}

    def find(self, organization=None, tags=None, tag_mode="all"):
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {}).values()
        tag_store = data.get("tags", {})

        def matches(contact):
            if organization is not None and contact["organization"].casefold() != organization.strip().casefold():
                return False
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            return True

        return sorted((c for c in contacts if matches(c)), key=lambda c: c["contact_id"])

    @staticmethod
    def _comparison_value(value):
        # NFKC, then casefold, then drop every Unicode whitespace code point.
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(char for char in normalized if not char.isspace())

    @staticmethod
    def _name_distance(left, right):
        # Levenshtein distance capped at 1: a single code point insert, delete,
        # or substitute. A transposition is two operations and never matches.
        if left == right:
            return 0
        if len(left) == len(right):
            differences = sum(1 for a, b in zip(left, right) if a != b)
            return 1 if differences == 1 else None
        if abs(len(left) - len(right)) != 1:
            return None
        if len(left) > len(right):
            left, right = right, left
        index = 0
        while index < len(left) and left[index] == right[index]:
            index += 1
        return 1 if left[index:] == right[index + 1:] else None

    def duplicate_candidates(self, organization=None, tags=None, tag_mode="all"):
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        # Filtering (and its validation) is exactly find's; pairing happens only
        # inside the filtered set, so a pair with a non-matching member is dropped.
        contacts = self.find(organization=organization, tags=tags, tag_mode=tag_mode)
        entries = [(self._comparison_value(contact["organization"]),
                    self._comparison_value(contact["name"]), contact)
                   for contact in contacts]
        pairs = []
        for first in range(len(entries)):
            for second in range(first + 1, len(entries)):
                org_a, name_a, contact_a = entries[first]
                org_b, name_b, contact_b = entries[second]
                if org_a != org_b:
                    continue
                distance = self._name_distance(name_a, name_b)
                if distance is None:
                    continue
                left, right = ((contact_a, contact_b)
                               if contact_a["contact_id"] < contact_b["contact_id"]
                               else (contact_b, contact_a))
                pairs.append({"left": left, "right": right, "distance": distance})
        pairs.sort(key=lambda pair: (pair["distance"], pair["left"]["contact_id"],
                                     pair["right"]["contact_id"]))
        return pairs

    def funnel_report(self, organization=None, tags=None, tag_mode="all"):
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {})
        tag_store = data.get("tags", {})
        org_key = organization.strip().casefold() if organization is not None else None

        def matches(contact):
            if org_key is not None and contact["organization"].casefold() != org_key:
                return False
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            return True

        def empty_counts():
            return {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0}

        totals = empty_counts()
        groups = {}
        for contact in contacts.values():
            if not matches(contact):
                continue
            key = contact["organization"].casefold()
            group = groups.get(key)
            if group is None:
                groups[key] = {"display": contact["organization"], "counts": empty_counts()}
            elif contact["organization"] < group["display"]:
                # Display name is the code-point-smallest original value among filtered contacts.
                group["display"] = contact["organization"]
            groups[key]["counts"]["contacts"] += 1
            totals["contacts"] += 1

        for opportunity in data.get("opportunities", {}).values():
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            counts = groups[contact["organization"].casefold()]["counts"]
            counts[opportunity["stage"]] += 1
            counts["opportunities"] += 1
            totals[opportunity["stage"]] += 1
            totals["opportunities"] += 1

        organizations = []
        for key in sorted(groups):
            group = groups[key]
            row = {"organization": group["display"]}
            row.update(group["counts"])
            organizations.append(row)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("organization", "contacts", "new", "qualified", "won", "lost", "opportunities"))
        for row in organizations:
            writer.writerow((row["organization"], row["contacts"], row["new"], row["qualified"],
                            row["won"], row["lost"], row["opportunities"]))

        return {"total": totals, "organizations": organizations, "csv": buffer.getvalue()}

    def followup_report(self, start_on, end_on, organization=None, tags=None, tag_mode="all"):
        start_on = calendar_day(start_on, "start_on")
        end_on = calendar_day(end_on, "end_on")
        if start_on > end_on:
            raise ValueError("start_on must not be later than end_on")
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {})
        tag_store = data.get("tags", {})
        org_key = organization.strip().casefold() if organization is not None else None

        def matches(contact):
            if org_key is not None and contact["organization"].casefold() != org_key:
                return False
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            return True

        allowed = {contact_id for contact_id, contact in contacts.items() if matches(contact)}
        entries = [entry for entry in data.get("followups", [])
                   if entry["contact_id"] in allowed and start_on <= entry["on"] <= end_on]
        # Ascending date, then contact id code point; the stable sort keeps save
        # order among entries sharing the same day and contact.
        entries.sort(key=lambda entry: (entry["on"], entry["contact_id"]))

        records = []
        for entry in entries:
            contact = contacts[entry["contact_id"]]
            records.append({"contact_id": contact["contact_id"], "name": contact["name"],
                            "email": contact["email"], "organization": contact["organization"],
                            "on": entry["on"], "note": entry["note"]})

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("contact_id", "name", "email", "organization", "on", "note"))
        for record in records:
            writer.writerow((record["contact_id"], record["name"], record["email"],
                             record["organization"], record["on"], record["note"]))

        return {"records": records, "csv": buffer.getvalue()}

    def timeline(self, contact_id):
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return sorted((r for r in data.get("followups", []) if r["contact_id"] == contact_id), key=lambda r: r["on"])

    def inactive_contacts(self, as_of, inactive_days, organization=None, tags=None, tag_mode="all"):
        as_of = calendar_day(as_of, "as_of")
        # type(...) is int rejects bools, which are ints in Python but never a day count.
        inactive_days = positive(inactive_days, "inactive_days")
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {})
        tag_store = data.get("tags", {})
        org_key = organization.strip().casefold() if organization is not None else None

        def matches(contact):
            if org_key is not None and contact["organization"].casefold() != org_key:
                return False
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            return True

        cutoff = date.fromisoformat(as_of)
        # Only records on/before as_of count; scanning in save order and replacing
        # on an equal or later day leaves the last saved same-day entry as the latest.
        latest = {}
        for entry in data.get("followups", []):
            if entry["on"] <= as_of:
                current = latest.get(entry["contact_id"])
                if current is None or entry["on"] >= current[0]:
                    latest[entry["contact_id"]] = (entry["on"], dict(entry))

        never, idle_rows = [], []
        for contact in contacts.values():
            if not matches(contact):
                continue
            current = latest.get(contact["contact_id"])
            if current is None:
                # Never followed up, or only future records: no record qualifies by as_of.
                never.append({"contact": dict(contact), "last_followup": None, "idle_days": None})
            else:
                on, entry = current
                idle_days = (cutoff - date.fromisoformat(on)).days
                if idle_days >= inactive_days:
                    idle_rows.append({"contact": dict(contact), "last_followup": entry,
                                      "idle_days": idle_days})

        never.sort(key=lambda row: row["contact"]["contact_id"])
        idle_rows.sort(key=lambda row: (-row["idle_days"], row["contact"]["contact_id"]))
        return never + idle_rows
