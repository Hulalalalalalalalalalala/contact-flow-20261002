import csv
import io
from datetime import date
from .storage import JsonStore, text

CONTACT_FIELDS = ("contact_id", "name", "email", "organization")
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

    def funnel_report(self, organization=None, tags=None, tag_mode="all"):
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        tag_store = data.get("tags", {})
        target_org = None if organization is None else organization.strip().casefold()

        def matches(contact):
            if target_org is not None and contact["organization"].casefold() != target_org:
                return False
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            return True

        selected = {c["contact_id"]: c for c in data.get("contacts", {}).values() if matches(c)}
        groups = {}

        def bucket(key):
            return groups.setdefault(key, {"contacts": 0, "new": 0, "qualified": 0, "won": 0,
                                          "lost": 0, "opportunities": 0, "names": []})

        for contact in selected.values():
            entry = bucket(contact["organization"].casefold())
            entry["contacts"] += 1
            entry["names"].append(contact["organization"])

        for opportunity in data.get("opportunities", {}).values():
            contact = selected.get(opportunity["contact_id"])
            if contact is None:
                continue
            entry = groups[contact["organization"].casefold()]
            entry["opportunities"] += 1
            entry[opportunity["stage"]] += 1

        total = {"contacts": 0, "new": 0, "qualified": 0, "won": 0, "lost": 0, "opportunities": 0}
        organizations = []
        for key in sorted(groups):
            entry = groups[key]
            for field in ("contacts", "new", "qualified", "won", "lost", "opportunities"):
                total[field] += entry[field]
            organizations.append({
                "organization": min(entry["names"]),
                "contacts": entry["contacts"],
                "new": entry["new"],
                "qualified": entry["qualified"],
                "won": entry["won"],
                "lost": entry["lost"],
                "opportunities": entry["opportunities"],
            })

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("organization", "contacts", "new", "qualified", "won", "lost", "opportunities"))
        for row in organizations:
            writer.writerow((row["organization"], row["contacts"], row["new"], row["qualified"],
                             row["won"], row["lost"], row["opportunities"]))
        return {"total": total, "organizations": organizations, "csv": buffer.getvalue()}

    def timeline(self, contact_id):
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        return sorted((r for r in data.get("followups", []) if r["contact_id"] == contact_id), key=lambda r: r["on"])
