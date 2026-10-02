import csv
from datetime import date
import io
from pathlib import Path
from .storage import JsonStore, text

IMPORT_COLUMNS = ("contact_id", "name", "email", "organization")

def contact_record(contact_id, name, email, organization):
    contact_id, name = text(contact_id, "contact_id"), text(name, "name")
    email, organization = text(email, "email").lower(), text(organization, "organization")
    if email.count("@") != 1 or any(c.isspace() for c in email) or not all(email.split("@")):
        raise ValueError("invalid email")
    return {"contact_id": contact_id, "name": name, "email": email, "organization": organization}

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
        contact = contact_record(contact_id, name, email, organization)
        data = self._read()
        contacts = data.setdefault("contacts", {})
        if contact["contact_id"] in contacts or any(c["email"] == contact["email"] for c in contacts.values()):
            raise ValueError("contact id or email already exists")
        contacts[contact["contact_id"]] = contact
        self._write(data)
        return contact

    def import_contacts(self, csv_path):
        if not isinstance(csv_path, str) or not csv_path.strip():
            raise ValueError("csv_path must be a nonempty string")
        try:
            raw = Path(csv_path).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("csv file must be valid UTF-8") from error
        try:
            rows = [row for row in csv.reader(io.StringIO(raw), strict=True) if row]
        except csv.Error as error:
            raise ValueError("invalid csv syntax") from error
        if not rows:
            raise ValueError("csv file must have a header row")
        header = rows[0]
        if len(header) != len(IMPORT_COLUMNS) or sorted(header) != sorted(IMPORT_COLUMNS):
            raise ValueError("csv header must contain exactly: " + ", ".join(IMPORT_COLUMNS))
        records = []
        for row in rows[1:]:
            if len(row) != len(IMPORT_COLUMNS):
                raise ValueError("csv record has wrong number of fields")
            values = dict(zip(header, row))
            records.append(contact_record(values["contact_id"], values["name"], values["email"], values["organization"]))
        data = self._read()
        contacts = data.setdefault("contacts", {})
        seen_ids = set(contacts)
        seen_emails = {c["email"] for c in contacts.values()}
        for record in records:
            if record["contact_id"] in seen_ids or record["email"] in seen_emails:
                raise ValueError("contact id or email already exists")
            seen_ids.add(record["contact_id"])
            seen_emails.add(record["email"])
        if not records:
            return []
        for record in records:
            contacts[record["contact_id"]] = record
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
        tag_store = data.setdefault("tags", {})
        merged_tags = sorted(set(tag_store.get(target_id, [])) | set(tag_store.pop(source_id, [])))
        if merged_tags:
            tag_store[target_id] = merged_tags
        else:
            tag_store.pop(target_id, None)
        if not tag_store:
            data.pop("tags", None)
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

    def timeline(self, contact_id):
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return sorted((r for r in data.get("followups", []) if r["contact_id"] == contact_id), key=lambda r: r["on"])
