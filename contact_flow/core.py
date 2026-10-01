from datetime import date
from .storage import JsonStore, text

def normalize_tags(tags, label="tags"):
    if not isinstance(tags, list):
        raise ValueError(label + " must be a list")
    normalized = set()
    for item in tags:
        if not isinstance(item, str):
            raise ValueError(label + " must contain only strings")
        value = item.strip().casefold()
        if not value:
            raise ValueError(label + " must contain only nonempty strings")
        normalized.add(value)
    return normalized

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
        del contacts[source_id]
        tags = data.get("tags", {})
        if source_id in tags or target_id in tags:
            union = set(tags.get(target_id, [])) | set(tags.pop(source_id, []))
            if union:
                tags[target_id] = sorted(union)
            else:
                tags.pop(target_id, None)
        self._write(data)
        return {"contact": contacts[target_id], "moved_followups": moved}

    def find(self, organization=None, tags=None, tag_mode="all"):
        data = self._read()
        wanted = normalize_tags(tags, "tags") if tags is not None else None
        if not wanted:
            wanted = None
        mode = text(tag_mode, "tag_mode")
        if mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        contacts = data.get("contacts", {}).values()
        by_tags = data.get("tags", {})

        def matches(contact):
            if organization is not None and contact["organization"].casefold() != organization.strip().casefold():
                return False
            if wanted is not None:
                current = set(by_tags.get(contact["contact_id"], []))
                if mode == "all" and not wanted <= current:
                    return False
                if mode == "any" and not (wanted & current):
                    return False
            return True

        return sorted((c for c in contacts if matches(c)), key=lambda c: c["contact_id"])

    def set_tags(self, contact_id, tags):
        contact_id = text(contact_id, "contact_id")
        normalized = normalize_tags(tags, "tags")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        store = data.setdefault("tags", {})
        if normalized:
            store[contact_id] = sorted(normalized)
        else:
            store.pop(contact_id, None)
        self._write(data)
        return sorted(normalized)

    def get_tags(self, contact_id):
        contact_id = text(contact_id, "contact_id")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return sorted(data.get("tags", {}).get(contact_id, []))

    def timeline(self, contact_id):
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return sorted((r for r in data.get("followups", []) if r["contact_id"] == contact_id), key=lambda r: r["on"])
