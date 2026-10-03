import csv
import io
import json
import unicodedata
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from .storage import JsonStore, text, calendar_day, positive, amount_string, amount_cents, format_cents

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

def _scan_json_string(expression, start):
    # A tag is one JSON double-quoted string; operators and parentheses inside
    # the quotes are tag content. The token ends at the first unescaped quote.
    index = start + 1
    while index < len(expression):
        char = expression[index]
        if char == "\\":
            index += 2
        elif char == '"':
            try:
                value = json.loads(expression[start:index + 1])
            except ValueError as error:
                raise ValueError("invalid JSON string in tag expression") from error
            # Decoded tags follow set-tags normalization: trim, casefold, keep
            # inner whitespace and every other character.
            tag = value.strip().casefold()
            if not tag:
                raise ValueError("tags must be nonempty strings")
            return ("tag", tag), index + 1
        else:
            index += 1
    raise ValueError("unterminated string in tag expression")

def _tokenize_tag_expression(expression):
    # Unicode whitespace between tokens is ignored; anything that is not a
    # quoted tag, &&, ||, ! or a parenthesis is an unknown symbol (a bare tag
    # included).
    tokens = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char.isspace():
            index += 1
        elif char == '"':
            token, index = _scan_json_string(expression, index)
            tokens.append(token)
        elif expression.startswith("&&", index):
            tokens.append("&&")
            index += 2
        elif expression.startswith("||", index):
            tokens.append("||")
            index += 2
        elif char in "!()":
            tokens.append(char)
            index += 1
        else:
            raise ValueError("unexpected character in tag expression: " + char)
    return tokens

def _parse_or(tokens, position):
    left, position = _parse_and(tokens, position)
    while position < len(tokens) and tokens[position] == "||":
        right, position = _parse_and(tokens, position + 1)
        left = _both_or(left, right)
    return left, position

def _both_or(left, right):
    return lambda tags: left(tags) or right(tags)

def _parse_and(tokens, position):
    left, position = _parse_unary(tokens, position)
    while position < len(tokens) and tokens[position] == "&&":
        right, position = _parse_unary(tokens, position + 1)
        left = _both_and(left, right)
    return left, position

def _both_and(left, right):
    return lambda tags: left(tags) and right(tags)

def _parse_unary(tokens, position):
    # Negation binds tightest and stacks: !!“a” is “a”.
    if position < len(tokens) and tokens[position] == "!":
        inner, position = _parse_unary(tokens, position + 1)
        return (lambda tags, inner=inner: not inner(tags)), position
    return _parse_primary(tokens, position)

def _parse_primary(tokens, position):
    if position >= len(tokens):
        raise ValueError("missing operand in tag expression")
    token = tokens[position]
    if isinstance(token, tuple):
        tag = token[1]
        return (lambda tags, tag=tag: tag in tags), position + 1
    if token == "(":
        inner, position = _parse_or(tokens, position + 1)
        if position >= len(tokens) or tokens[position] != ")":
            raise ValueError("unbalanced parentheses in tag expression")
        return inner, position + 1
    # An operator or ")" where an operand belongs: missing operand (this also
    # covers empty parentheses).
    raise ValueError("missing operand in tag expression")

def _tag_expression_predicate(expression):
    # The whole expression tokenizes and parses up front, so a malformed tail
    # rejects even when the store is empty or earlier conditions already decide.
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("tag_expression must be a nonempty string")
    tokens = _tokenize_tag_expression(expression)
    predicate, position = _parse_or(tokens, 0)
    if position != len(tokens):
        # Adjacent tags with no operator, a stray ")" or any other trailing
        # content after a complete expression.
        raise ValueError("unexpected content after complete tag expression")
    return predicate

# Distinguishes an omitted next_reminder (auto-renew a monthly reminder) from
# an explicit None (terminate repetition and clear the reminder).
_UNSET = object()

def _is_repeating(reminder):
    # Reminders saved before monthly repetition existed lack both fields and
    # read as one-time reminders.
    return reminder.get("repeat_monthly") is True and type(reminder.get("anchor_day")) is int

def _next_monthly_due(due_on, anchor_day, on):
    # Candidates run from the month after the current due date, pinned to the
    # anchor day and clamped to the end of shorter months; the first candidate
    # strictly later than the completion date wins, so completing early never
    # keeps the current month and skipped months stay skipped.
    year, month = int(due_on[:4]), int(due_on[5:7])
    month += 1
    if month > 12:
        year, month = year + 1, 1
    while True:
        if year > 9999:
            raise ValueError("next reminder due_on would exceed 9999-12-31")
        candidate = "%04d-%02d-%02d" % (year, month, min(anchor_day, monthrange(year, month)[1]))
        if candidate > on:
            return candidate
        month += 1
        if month > 12:
            year, month = year + 1, 1

def _renewed_reminder(current, on):
    # Auto-renewal keeps the original note and repetition rule; the completion
    # note only goes into the followup record.
    return {"contact_id": current["contact_id"],
            "due_on": _next_monthly_due(current["due_on"], current["anchor_day"], on),
            "note": current["note"],
            "repeat_monthly": True,
            "anchor_day": current["anchor_day"]}

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
                stage = values["stage"].strip()
                if stage not in STAGES:
                    raise ValueError("invalid stage")
                records.append({"opportunity_id": opportunity_id, "contact_id": contact_id,
                                "title": title, "stage": stage})
        except csv.Error as error:
            raise ValueError("invalid CSV syntax") from error

        data = self._read()
        contacts = data.get("contacts", {})
        opportunities = data.get("opportunities", {})
        # The whole batch validates before any write: unknown contacts and
        # duplicate ids (existing or within the batch, even identical rows) reject it all.
        seen = set()
        for entry in records:
            if entry["contact_id"] not in contacts:
                raise ValueError("unknown contact")
            if entry["opportunity_id"] in opportunities or entry["opportunity_id"] in seen:
                raise ValueError("opportunity id already exists")
            seen.add(entry["opportunity_id"])
        imported = []
        if records:
            store = data.setdefault("opportunities", {})
            for entry in records:
                opportunity = dict(entry)
                store[entry["opportunity_id"]] = opportunity
                imported.append(dict(opportunity))
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

    def set_reminder(self, contact_id, due_on, note, repeat_monthly=False):
        contact_id = text(contact_id, "contact_id")
        note = text(note, "note")
        due_on = calendar_day(due_on, "due_on")
        # Only a real boolean toggles repetition; ints, strings and null reject.
        if type(repeat_monthly) is not bool:
            raise ValueError("repeat_monthly must be a boolean")
        data = self._read()
        if contact_id not in data.get("contacts", {}):
            raise ValueError("unknown contact")
        # Each contact keeps at most one reminder; setting again replaces the whole
        # entry, so an omitted or false repeat_monthly also drops any previous rule.
        reminder = {"contact_id": contact_id, "due_on": due_on, "note": note}
        if repeat_monthly:
            # The first due date's day of month anchors every later renewal.
            reminder["repeat_monthly"] = True
            reminder["anchor_day"] = int(due_on[8:10])
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

    def complete_reminder(self, contact_id, on, note, next_reminder=_UNSET):
        contact_id = text(contact_id, "contact_id")
        note = text(note, "note")
        on = calendar_day(on, "on")
        pending = None
        if next_reminder is not _UNSET and next_reminder is not None:
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
        current = store.get(contact_id)
        if current is None:
            raise ValueError("no current reminder")
        if next_reminder is _UNSET and _is_repeating(current):
            # An omitted next_reminder renews a monthly reminder from its anchor
            # instead of clearing it; an explicit null still terminates it.
            pending = _renewed_reminder(current, on)
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

    def complete_reminders(self, completions):
        # The whole batch validates before any followup is appended or reminder
        # cleared/replaced, so a rejected item never leaves half the batch applied;
        # every append and reminder change commits in a single write.
        if not isinstance(completions, list):
            raise ValueError("completions must be a list")
        if not completions:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        REQUIRED_KEYS = {"contact_id", "expected_due_on", "on", "note"}
        COMPLETION_KEYS = REQUIRED_KEYS | {"next_reminder"}
        entries = []
        seen = set()
        for item in completions:
            if not isinstance(item, dict) or not REQUIRED_KEYS <= set(item) <= COMPLETION_KEYS:
                raise ValueError(
                    "each completion must be an object with contact_id, expected_due_on, "
                    "on, note and an optional next_reminder")
            contact_id = text(item["contact_id"], "contact_id")
            note = text(item["note"], "note")
            on = calendar_day(item["on"], "on")
            expected_due_on = calendar_day(item["expected_due_on"], "expected_due_on")
            # An omitted next_reminder is resolved against the stored reminder
            # after the read; an explicit null terminates any repetition.
            pending = _UNSET
            if "next_reminder" in item:
                next_reminder = item["next_reminder"]
                if next_reminder is None:
                    pending = None
                else:
                    if not isinstance(next_reminder, dict) or set(next_reminder) != {"due_on", "note"}:
                        raise ValueError(
                            "next_reminder must be an object with exactly due_on and note")
                    due_on = calendar_day(next_reminder["due_on"], "due_on")
                    if due_on <= on:
                        raise ValueError(
                            "next reminder due_on must be later than the completion date")
                    pending = {"contact_id": contact_id, "due_on": due_on,
                               "note": text(next_reminder["note"], "note")}
            # Normalized ids are case-sensitive; a repeated contact id (even an
            # identical item) rejects the whole batch.
            if contact_id in seen:
                raise ValueError("duplicate contact id in completions")
            seen.add(contact_id)
            entries.append((contact_id, expected_due_on, on, note, pending))
        data = self._read()
        contacts = data.get("contacts", {})
        store = data.get("reminders", {})
        planned = []
        for contact_id, expected_due_on, on, note, pending in entries:
            if contact_id not in contacts:
                raise ValueError("unknown contact")
            current = store.get(contact_id)
            if current is None:
                raise ValueError("no current reminder")
            # The note never participates in the check: only the current due date
            # must equal the expected one supplied with the completion.
            if current["due_on"] != expected_due_on:
                raise ValueError("expected_due_on does not match the current reminder due_on")
            if pending is _UNSET:
                # Omitted next_reminder: a monthly reminder renews from its
                # anchor; a one-time reminder simply clears.
                pending = _renewed_reminder(current, on) if _is_repeating(current) else None
            entry = {"contact_id": contact_id, "on": on, "note": note}
            planned.append((contact_id, entry, pending))
        # Everything validated; results follow input order and complete-reminder's shape.
        results = [{"followup": dict(entry),
                    "reminder": dict(pending) if pending is not None else None}
                   for _, entry, pending in planned]
        followups = data.setdefault("followups", [])
        for contact_id, entry, pending in planned:
            followups.append(entry)
            if pending is None:
                del store[contact_id]
            else:
                store[contact_id] = pending
        if not store:
            data.pop("reminders", None)
        self._write(data)
        return results

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

    def set_stage(self, opportunity_id, stage, on=None):
        opportunity_id = text(opportunity_id, "opportunity_id")
        stage = text(stage, "stage")
        if stage not in STAGES:
            raise ValueError("invalid stage")
        # An omitted or None date is stored as null; a provided date must be a
        # trimmed real YYYY-MM-DD string (past, future and leap days allowed).
        on = None if on is None else calendar_day(on, "on")
        data = self._read()
        opportunity = data.get("opportunities", {}).get(opportunity_id)
        if opportunity is None:
            raise ValueError("unknown opportunity")
        current = opportunity["stage"]
        if stage != current and stage not in STAGE_TRANSITIONS.get(current, ()):
            raise ValueError("invalid stage transition")
        # Every provided date is validated, even on a same-stage no-op: it must
        # not precede the latest non-null date already recorded for this deal.
        # Null records never move that lower bound.
        history = data.get("stage_history", {}).get(opportunity_id, [])
        if on is not None:
            dated = [entry["on"] for entry in history if entry["on"] is not None]
            if dated and on < dated[-1]:
                raise ValueError("on must not be earlier than the latest recorded stage date")
        if stage != current:
            opportunity["stage"] = stage
            # Stage change and history append commit in a single write; the
            # history lives outside the opportunity object itself.
            data.setdefault("stage_history", {}).setdefault(opportunity_id, []).append(
                {"from_stage": current, "to_stage": stage, "on": on})
            self._write(data)
        return dict(opportunity)

    def set_stages(self, updates):
        # The whole batch validates against each deal's pre-call stage before any
        # stage or history changes, so a rejected item never leaves half the batch
        # applied; stage changes and history appends commit in a single write.
        if not isinstance(updates, list):
            raise ValueError("updates must be a list")
        if not updates:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        allowed_keys = {"opportunity_id", "stage", "on"}
        entries = []
        seen = set()
        for item in updates:
            if (not isinstance(item, dict)
                    or not {"opportunity_id", "stage"} <= set(item)
                    or not set(item) <= allowed_keys):
                raise ValueError(
                    "each update must be an object with opportunity_id, stage and an optional on")
            opportunity_id = text(item["opportunity_id"], "opportunity_id")
            stage = text(item["stage"], "stage")
            if stage not in STAGES:
                raise ValueError("invalid stage")
            # An omitted or None date is stored as null; a provided date must be a
            # trimmed real YYYY-MM-DD string (past, future and leap days allowed).
            on = item.get("on")
            on = None if on is None else calendar_day(on, "on")
            # Normalized ids are case-sensitive; a repeated opportunity id (even an
            # identical item) rejects the whole batch.
            if opportunity_id in seen:
                raise ValueError("duplicate opportunity id in updates")
            seen.add(opportunity_id)
            entries.append((opportunity_id, stage, on))
        data = self._read()
        opportunities = data.get("opportunities", {})
        history_store = data.get("stage_history", {})
        planned = []
        changed = False
        for opportunity_id, stage, on in entries:
            opportunity = opportunities.get(opportunity_id)
            if opportunity is None:
                raise ValueError("unknown opportunity")
            current = opportunity["stage"]
            if stage != current and stage not in STAGE_TRANSITIONS.get(current, ()):
                raise ValueError("invalid stage transition")
            # Every provided date is validated, even on a same-stage no-op: it must
            # not precede the latest non-null date already recorded for this deal.
            # Null records never move that lower bound. Each deal appears once, so
            # the pre-call history is the only history its date is checked against.
            if on is not None:
                dated = [entry["on"] for entry in history_store.get(opportunity_id, [])
                         if entry["on"] is not None]
                if dated and on < dated[-1]:
                    raise ValueError("on must not be earlier than the latest recorded stage date")
            if stage != current:
                changed = True
            planned.append((opportunity_id, opportunity, current, stage, on))
        # Results are the complete opportunities in input order, unchanged on a no-op batch.
        results = []
        if not changed:
            # Every item repeated its current stage: report the originals
            # without rewriting the file.
            for _, opportunity, _, _, _ in planned:
                results.append(dict(opportunity))
            return results
        for opportunity_id, opportunity, current, stage, on in planned:
            if stage != current:
                opportunity["stage"] = stage
                # The history lives outside the opportunity object itself;
                # deals without a history collection start from an empty one.
                history_store.setdefault(opportunity_id, []).append(
                    {"from_stage": current, "to_stage": stage, "on": on})
            results.append(dict(opportunity))
        data.setdefault("stage_history", history_store)
        self._write(data)
        return results

    def stage_history(self, opportunity_id):
        opportunity_id = text(opportunity_id, "opportunity_id")
        data = self._read()
        if opportunity_id not in data.get("opportunities", {}):
            raise ValueError("unknown opportunity")
        # Records stay in successful-change order, never re-sorted by date;
        # deals without any recorded change read as an empty history.
        return [dict(entry) for entry in data.get("stage_history", {}).get(opportunity_id, [])]

    def correct_stage_dates(self, corrections):
        # Back-fill or fix the business dates of already saved stage-history
        # records. The whole batch validates and commits together; a rejected
        # batch never touches the file.
        if not isinstance(corrections, list):
            raise ValueError("corrections must be a list")
        if not corrections:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        correction_keys = {"opportunity_id", "index", "on"}
        entries = []
        seen = set()
        for item in corrections:
            if not isinstance(item, dict) or set(item) != correction_keys:
                raise ValueError(
                    "each correction must be an object with exactly opportunity_id, index and on")
            opportunity_id = text(item["opportunity_id"], "opportunity_id")
            # type(...) is int rejects bools, which are ints in Python but never an index.
            index = item["index"]
            if type(index) is not int or index < 0:
                raise ValueError("index must be a nonnegative integer")
            # Dates reuse the real-calendar rule (trimmed YYYY-MM-DD, past,
            # future and leap days allowed, null rejected, no system clock).
            on = calendar_day(item["on"], "on")
            # Normalized ids are case-sensitive; the same position of one deal
            # may appear only once, even when both items are identical.
            if (opportunity_id, index) in seen:
                raise ValueError("duplicate correction for the same opportunity and index")
            seen.add((opportunity_id, index))
            entries.append((opportunity_id, index, on))
        data = self._read()
        opportunities = data.get("opportunities", {})
        history_store = data.get("stage_history", {})
        involved = {}
        for opportunity_id, index, _ in entries:
            if opportunity_id not in opportunities:
                raise ValueError("unknown opportunity")
            # Deals without a history collection (including old data) read as empty.
            history = history_store.get(opportunity_id, [])
            if index >= len(history):
                raise ValueError("index out of range for stage history")
            involved[opportunity_id] = history
        # Apply on copies so final monotonicity is judged on the post-correction
        # history while storage stays untouched until everything validates.
        corrected = {opportunity_id: [dict(entry) for entry in history]
                     for opportunity_id, history in involved.items()}
        for opportunity_id, index, on in entries:
            corrected[opportunity_id][index]["on"] = on
        for history in corrected.values():
            # Non-null dates in save order must not go backwards; same day is
            # allowed and untouched null records never participate.
            previous = None
            for entry in history:
                if entry["on"] is not None:
                    if previous is not None and entry["on"] < previous:
                        raise ValueError("stage dates must not go backwards")
                    previous = entry["on"]
        # Results are the corrected records in input order, shaped like stage-history.
        results = [dict(corrected[opportunity_id][index])
                   for opportunity_id, index, _ in entries]
        if all(corrected[opportunity_id] == involved[opportunity_id]
               for opportunity_id in involved):
            # Every normalized date already matched the stored one: report the
            # records without rewriting the file.
            return results
        store = data.setdefault("stage_history", {})
        for opportunity_id, history in corrected.items():
            store[opportunity_id] = history
        self._write(data)
        return results

    def set_opportunity_amount(self, opportunity_id, amount):
        opportunity_id = text(opportunity_id, "opportunity_id")
        amount = amount_string(amount, "amount")
        data = self._read()
        opportunity = data.get("opportunities", {}).get(opportunity_id)
        if opportunity is None:
            raise ValueError("unknown opportunity")
        # Terminal stages accept amounts too; an unset amount reads as 0.00, so
        # setting zero on a never-priced opportunity is a no-op like any equal value.
        current = opportunity.get("amount", "0.00")
        if current != amount:
            opportunity["amount"] = amount
            self._write(data)
        return {"opportunity_id": opportunity_id, "amount": amount}

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

    def transfer_opportunities(self, transfers):
        # The whole batch validates before any ownership changes, so a rejected
        # transfer never leaves an opportunity on a half-updated contact.
        if not isinstance(transfers, list):
            raise ValueError("transfers must be a list")
        if not transfers:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        TRANSFER_KEYS = {"opportunity_id", "source_contact_id", "target_contact_id"}
        entries = []
        seen = set()
        for item in transfers:
            if not isinstance(item, dict) or set(item) != TRANSFER_KEYS:
                raise ValueError(
                    "each transfer must be an object with exactly opportunity_id, "
                    "source_contact_id and target_contact_id")
            opportunity_id = text(item["opportunity_id"], "opportunity_id")
            source_contact_id = text(item["source_contact_id"], "source_contact_id")
            target_contact_id = text(item["target_contact_id"], "target_contact_id")
            # Normalized ids are case-sensitive; a repeated opportunity id (even an
            # identical item, or handing the same deal along twice) rejects it all.
            if opportunity_id in seen:
                raise ValueError("duplicate opportunity id in transfers")
            seen.add(opportunity_id)
            entries.append((opportunity_id, source_contact_id, target_contact_id))
        data = self._read()
        contacts = data.get("contacts", {})
        opportunities = data.get("opportunities", {})
        results = []
        changed = False
        for opportunity_id, source_contact_id, target_contact_id in entries:
            if source_contact_id not in contacts or target_contact_id not in contacts:
                raise ValueError("unknown contact")
            opportunity = opportunities.get(opportunity_id)
            if opportunity is None:
                raise ValueError("unknown opportunity")
            # The claimed source must match the opportunity's actual current owner.
            if opportunity["contact_id"] != source_contact_id:
                raise ValueError("opportunity is not owned by source contact")
            if opportunity["contact_id"] != target_contact_id:
                changed = True
            results.append(dict(opportunity))
            results[-1]["contact_id"] = target_contact_id
        if not changed:
            # Source and target identical for every item: report the originals
            # without rewriting the file.
            return results
        for opportunity_id, _, target_contact_id in entries:
            opportunities[opportunity_id]["contact_id"] = target_contact_id
        self._write(data)
        return results

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

    def batch_merge_contacts(self, merges):
        # The whole batch validates against the pre-call state before any
        # contact, followup, opportunity, tag or reminder changes, so a
        # rejected item never leaves part of the batch applied; every change
        # commits in a single write.
        if not isinstance(merges, list):
            raise ValueError("merges must be a list")
        if not merges:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        entries = []
        mapping = {}
        for item in merges:
            if not isinstance(item, dict) or set(item) != {"source_id", "target_id"}:
                raise ValueError("each merge must be an object with exactly source_id and target_id")
            source_id = text(item["source_id"], "source_id")
            target_id = text(item["target_id"], "target_id")
            if source_id == target_id:
                raise ValueError("source and target must differ")
            # Normalized ids are case-sensitive; a repeated source id (even an
            # identical item) rejects the whole batch. Several sources may point
            # at the same target, and a target may itself be a source.
            if source_id in mapping:
                raise ValueError("duplicate source id in merges")
            mapping[source_id] = target_id
            entries.append((source_id, target_id))
        data = self._read()
        contacts = data.get("contacts", {})
        for source_id, target_id in entries:
            if source_id not in contacts or target_id not in contacts:
                raise ValueError("unknown contact")
        # Chains collapse to the one target that is not itself a source; any
        # cycle in the mapping rejects the whole batch.
        finals = {}
        for source_id in mapping:
            seen = set()
            node = source_id
            while node in mapping:
                if node in seen:
                    raise ValueError("merges must not form a cycle")
                seen.add(node)
                node = mapping[node]
            finals[source_id] = node
        # Group membership is order-independent: sources sharing a final target
        # merge together with it, so item order only changes the result order.
        groups = {}
        for source_id, final_id in finals.items():
            groups.setdefault(final_id, []).append(source_id)
        followups = data.get("followups", [])
        # moved_followups counts only records directly owned by each source
        # before the call; records of other sources in the chain never
        # double-count.
        moved = {}
        for entry in followups:
            owner = entry["contact_id"]
            if owner in finals:
                moved[owner] = moved.get(owner, 0) + 1
        # Followups and opportunities (terminal stages included) only change
        # ownership; save order, duplicates, ids, titles, stages, amounts and
        # stage history stay as they were.
        for entry in followups:
            final_id = finals.get(entry["contact_id"])
            if final_id is not None:
                entry["contact_id"] = final_id
        for opportunity in data.get("opportunities", {}).values():
            final_id = finals.get(opportunity["contact_id"])
            if final_id is not None:
                opportunity["contact_id"] = final_id
        tag_store = data.get("tags", {})
        reminder_store = data.get("reminders", {})
        for final_id, sources in groups.items():
            members = [final_id] + sources
            # Tags merge as the deduplicated union of the whole group, sorted
            # like set-tags normalizes them.
            merged_tags = sorted({tag for member in members for tag in tag_store.get(member, [])})
            for source_id in sources:
                tag_store.pop(source_id, None)
            if merged_tags:
                tag_store[final_id] = merged_tags
            else:
                tag_store.pop(final_id, None)
            # One reminder per group: the earliest due date wins; a same-day
            # tie keeps the final target's original reminder, otherwise the
            # original owner id smallest by Unicode code point. The chosen
            # reminder keeps every field except its contact_id.
            chosen = None
            for member in members:
                reminder = reminder_store.get(member)
                if reminder is None:
                    continue
                key = (reminder["due_on"], 0 if member == final_id else 1, member)
                if chosen is None or key < chosen[0]:
                    chosen = (key, reminder)
            for source_id in sources:
                reminder_store.pop(source_id, None)
            if chosen is not None:
                reminder = dict(chosen[1])
                reminder["contact_id"] = final_id
                reminder_store[final_id] = reminder
            else:
                reminder_store.pop(final_id, None)
        for source_id in mapping:
            del contacts[source_id]
        if tag_store:
            data["tags"] = tag_store
        else:
            data.pop("tags", None)
        if reminder_store:
            data["reminders"] = reminder_store
        else:
            data.pop("reminders", None)
        self._write(data)
        # Results follow input order: the normalized source id, the final
        # target's complete contact and that source's own moved followup count.
        return [{"source_id": source_id,
                 "contact": contacts[finals[source_id]],
                 "moved_followups": moved.get(source_id, 0)}
                for source_id, _ in entries]

    def find(self, organization=None, tags=None, tag_mode="all", tag_expression=None):
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        # An omitted or None tag_expression keeps the legacy behavior; a given
        # one parses and validates in full before any data is read, then
        # intersects with the organization and tags/tag_mode conditions.
        predicate = None if tag_expression is None else _tag_expression_predicate(tag_expression)
        data = self._read()
        contacts = data.get("contacts", {}).values()
        tag_store = data.get("tags", {})

        def matches(contact):
            if organization is not None and contact["organization"].casefold() != organization.strip().casefold():
                return False
            have = None
            if wanted:
                have = set(tag_store.get(contact["contact_id"], []))
                if tag_mode == "all" and not set(wanted) <= have:
                    return False
                if tag_mode == "any" and not (set(wanted) & have):
                    return False
            if predicate is not None:
                # Contacts without a tag set (or old data without the tags
                # collection) evaluate against the empty set, so a pure
                # exclusion expression can still select them.
                if have is None:
                    have = set(tag_store.get(contact["contact_id"], []))
                if not predicate(have):
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

    @staticmethod
    def _substring_distance(keyword, name):
        # Minimum Levenshtein distance between the keyword and any nonempty
        # contiguous slice of the name, counted per Unicode code point with
        # insert/delete/substitute only (a transposition is two operations).
        # The top row is the standard 0..len(keyword); the first cell of every
        # later row is 0, so the alignment may start at any name position for
        # free. The answer is the bottom row's minimum, so the matched fragment
        # can also end anywhere; that fragment is never empty.
        previous = list(range(len(keyword) + 1))
        best = None
        for char in name:
            current = [0]
            for index in range(1, len(keyword) + 1):
                cost = 0 if keyword[index - 1] == char else 1
                current.append(min(previous[index] + 1, current[index - 1] + 1,
                                   previous[index - 1] + cost))
            previous = current
            if best is None or current[-1] < best:
                best = current[-1]
        return best

    def search_contacts(self, query, max_distance=1, organization=None, tags=None,
                        tag_mode="all", limit=20, offset=0):
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        # type(...) is int rejects bools, which are ints in Python but never a
        # distance or a page parameter.
        if type(max_distance) is not int or max_distance not in (0, 1, 2):
            raise ValueError("max_distance must be 0, 1 or 2")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        keyword = self._comparison_value(query)
        if not keyword:
            raise ValueError("query must not be empty")
        # Filtering (and its validation) is exactly find's, intersected with the
        # name condition, so a contact failing either side is excluded.
        contacts = self.find(organization=organization, tags=tags, tag_mode=tag_mode)
        matches = []
        for contact in contacts:
            name = self._comparison_value(contact["name"])
            if not name:
                continue
            distance = self._substring_distance(keyword, name)
            if distance <= max_distance:
                matches.append({"contact": dict(contact), "distance": distance})
        # Ascending distance, then contact id code point; pagination comes after.
        matches.sort(key=lambda match: (match["distance"], match["contact"]["contact_id"]))
        total = len(matches)
        return {"total": total, "matches": matches[offset:offset + limit]}

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

    def opportunity_amount_report(self, organization=None, tags=None, tag_mode="all"):
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

        # Sums accumulate in integer cents, so totals are exact to the cent.
        def empty_counts():
            return {"new": 0, "qualified": 0, "won": 0, "lost": 0}

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

        for opportunity in data.get("opportunities", {}).values():
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            # An opportunity without a price contributes 0.00 to its current stage.
            cents = amount_cents(opportunity["amount"]) if "amount" in opportunity else 0
            counts = groups[contact["organization"].casefold()]["counts"]
            counts[opportunity["stage"]] += cents
            totals[opportunity["stage"]] += cents

        def money_row(counts):
            row = {stage: format_cents(counts[stage]) for stage in STAGES}
            row["amount"] = format_cents(sum(counts.values()))
            return row

        organizations = []
        for key in sorted(groups):
            group = groups[key]
            row = {"organization": group["display"]}
            row.update(money_row(group["counts"]))
            organizations.append(row)

        totals_row = money_row(totals)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("organization", "new", "qualified", "won", "lost", "amount"))
        for row in organizations:
            writer.writerow((row["organization"], row["new"], row["qualified"],
                             row["won"], row["lost"], row["amount"]))

        return {"total": totals_row, "organizations": organizations, "csv": buffer.getvalue()}

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

    def correct_followups(self, corrections):
        # Revise the date and/or note of already saved followup records. The
        # whole batch validates against the pre-call timelines before any
        # record changes, so a rejected batch never touches the file; every
        # change commits in a single write.
        if not isinstance(corrections, list):
            raise ValueError("corrections must be a list")
        if not corrections:
            # An empty batch succeeds with an empty result and never touches storage.
            return []
        correction_keys = {"contact_id", "index", "expected_on", "expected_note", "changes"}
        entries = []
        seen = set()
        for item in corrections:
            if not isinstance(item, dict) or set(item) != correction_keys:
                raise ValueError(
                    "each correction must be an object with exactly contact_id, index, "
                    "expected_on, expected_note and changes")
            contact_id = text(item["contact_id"], "contact_id")
            # type(...) is int rejects bools, which are ints in Python but never an index.
            index = item["index"]
            if type(index) is not int or index < 0:
                raise ValueError("index must be a nonnegative integer")
            # The expected values pin the selected record verbatim: they must be
            # strings and are compared exactly, never normalized.
            expected_on = item["expected_on"]
            if not isinstance(expected_on, str):
                raise ValueError("expected_on must be a string")
            expected_note = item["expected_note"]
            if not isinstance(expected_note, str):
                raise ValueError("expected_note must be a string")
            changes = item["changes"]
            if not isinstance(changes, dict) or not changes:
                raise ValueError("changes must be a nonempty object")
            normalized = {}
            for key, value in changes.items():
                if key not in ("on", "note"):
                    raise ValueError("changes contains an unsupported field: " + str(key))
                # Dates reuse the real-calendar rule (trimmed YYYY-MM-DD, past,
                # future and leap days allowed); notes trim but keep inner
                # whitespace and newlines. Omitted fields keep their values.
                normalized[key] = calendar_day(value, "on") if key == "on" else text(value, "note")
            # Normalized ids are case-sensitive; the same timeline position of
            # one contact may appear only once, even when both items are identical.
            if (contact_id, index) in seen:
                raise ValueError("duplicate correction for the same contact and index")
            seen.add((contact_id, index))
            entries.append((contact_id, index, expected_on, expected_note, normalized))
        data = self._read()
        contacts = data.get("contacts", {})
        followups = data.get("followups", [])
        # Indices address the timeline as it was when the call started: date
        # ascending, save order on ties. Resolving every record before mutating
        # keeps later positions unaffected by earlier date changes.
        timelines = {}
        planned = []
        for contact_id, index, expected_on, expected_note, normalized in entries:
            if contact_id not in contacts:
                raise ValueError("unknown contact")
            timeline = timelines.get(contact_id)
            if timeline is None:
                # Contacts without any followups (including old data) read as empty.
                timeline = sorted((entry for entry in followups if entry["contact_id"] == contact_id),
                                  key=lambda entry: entry["on"])
                timelines[contact_id] = timeline
            if index >= len(timeline):
                raise ValueError("index out of range for followup timeline")
            record = timeline[index]
            if record["on"] != expected_on or record["note"] != expected_note:
                raise ValueError("expected values do not match the followup record")
            planned.append((record, normalized))
        # Results are the complete corrected records in input order, shaped like
        # the stored followup entries.
        results = []
        changed = False
        for record, normalized in planned:
            corrected = dict(record)
            corrected.update(normalized)
            if corrected != record:
                changed = True
            results.append(corrected)
        if not changed:
            # Every normalized change already matched the stored values: report
            # the records without rewriting the file.
            return results
        for record, normalized in planned:
            record.update(normalized)
        self._write(data)
        return results

    def stage_change_report(self, start_on, end_on, organization=None, tags=None, tag_mode="all"):
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

        history_store = data.get("stage_history", {})
        records = []
        for opportunity in data.get("opportunities", {}).values():
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            for entry in history_store.get(opportunity["opportunity_id"], []):
                # Only saved history with a non-null date inside the inclusive
                # range counts; nothing is back-filled for deals without records.
                if entry["on"] is None or not start_on <= entry["on"] <= end_on:
                    continue
                records.append({"opportunity_id": opportunity["opportunity_id"],
                                "contact_id": contact["contact_id"],
                                "title": opportunity["title"],
                                "organization": contact["organization"],
                                "from_stage": entry["from_stage"],
                                "to_stage": entry["to_stage"],
                                "on": entry["on"]})
        # Ascending date, then opportunity id code point; the stable sort keeps
        # save order among entries sharing the same day and opportunity.
        records.sort(key=lambda record: (record["on"], record["opportunity_id"]))

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("opportunity_id", "contact_id", "title", "organization",
                         "from_stage", "to_stage", "on"))
        for record in records:
            writer.writerow((record["opportunity_id"], record["contact_id"], record["title"],
                             record["organization"], record["from_stage"], record["to_stage"],
                             record["on"]))
        return {"records": records, "csv": buffer.getvalue()}

    def win_cycle_report(self, start_on, end_on, organization=None, tags=None, tag_mode="all"):
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

        def empty_counts():
            return {"won": 0, "measured": 0, "unmeasured": 0, "days": 0}

        history_store = data.get("stage_history", {})
        totals = empty_counts()
        # Only organizations that own at least one qualifying win get a group.
        groups = {}
        for opportunity in data.get("opportunities", {}).values():
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            history = history_store.get(opportunity["opportunity_id"], [])
            # Exactly one winning event per opportunity: the last saved record
            # entering won with a non-null date inside the range. Imported/legacy
            # deals already at won have no such record, and a null-date win entry
            # on its own never qualifies.
            won_index = None
            for index, entry in enumerate(history):
                if (entry["to_stage"] == "won" and entry["on"] is not None
                        and start_on <= entry["on"] <= end_on):
                    won_index = index
            if won_index is None:
                continue
            won_on = history[won_index]["on"]
            # The duration starts at the most recent (in save order) dated entry
            # into qualified saved before the winning event; the start may be
            # earlier than the query range. A null on never serves as a start.
            qualified_on = None
            for entry in history[:won_index]:
                if entry["to_stage"] == "qualified" and entry["on"] is not None:
                    qualified_on = entry["on"]
            key = contact["organization"].casefold()
            group = groups.get(key)
            if group is None:
                groups[key] = {"display": contact["organization"], "counts": empty_counts()}
            elif contact["organization"] < group["display"]:
                # Display name is the code-point-smallest original value among owning contacts.
                group["display"] = contact["organization"]
            counts = groups[key]["counts"]
            counts["won"] += 1
            totals["won"] += 1
            if qualified_on is None:
                counts["unmeasured"] += 1
                totals["unmeasured"] += 1
            else:
                span = (date.fromisoformat(won_on) - date.fromisoformat(qualified_on)).days
                counts["measured"] += 1
                counts["days"] += span
                totals["measured"] += 1
                totals["days"] += span

        def stats_row(counts):
            row = dict(counts)
            if counts["measured"]:
                average = (Decimal(counts["days"]) / Decimal(counts["measured"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                row["average"] = format(average, "f")
            else:
                row["average"] = None
            return row

        organizations = []
        for key in sorted(groups):
            group = groups[key]
            row = {"organization": group["display"]}
            row.update(stats_row(group["counts"]))
            organizations.append(row)

        total_row = stats_row(totals)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("organization", "won", "measured", "unmeasured", "days", "average"))
        for row in organizations:
            writer.writerow((row["organization"], row["won"], row["measured"],
                             row["unmeasured"], row["days"],
                             row["average"] if row["average"] is not None else ""))

        return {"total": total_row, "organizations": organizations, "csv": buffer.getvalue()}

    def conversion_report(self, start_on, end_on, as_of, organization=None, tags=None, tag_mode="all"):
        start_on = calendar_day(start_on, "start_on")
        end_on = calendar_day(end_on, "end_on")
        as_of = calendar_day(as_of, "as_of")
        if start_on > end_on:
            raise ValueError("start_on must not be later than end_on")
        if as_of < end_on:
            raise ValueError("as_of must not be earlier than end_on")
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {})
        tag_store = data.get("tags", {})
        history_store = data.get("stage_history", {})
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
            return {"entered": 0, "won": 0, "lost": 0, "open": 0}

        totals = empty_counts()
        # Only organizations owning at least one cohort member get a group.
        groups = {}
        for opportunity in data.get("opportunities", {}).values():
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            history = history_store.get(opportunity["opportunity_id"], [])
            # Each deal enters the cohort at most once: the last saved record
            # (save order) entering qualified with a non-null date inside the
            # inclusive entry window. Imported/legacy deals already qualified
            # and null-date entries never qualify.
            entry_index = None
            for index, entry in enumerate(history):
                if (entry["to_stage"] == "qualified" and entry["on"] is not None
                        and start_on <= entry["on"] <= end_on):
                    entry_index = index
            if entry_index is None:
                continue
            entered_on = history[entry_index]["on"]
            # The outcome comes from the last saved record from the entry on
            # whose non-null date is no later than the observation day; won/lost
            # settle it and anything else stays open. Null-date records never
            # decide, and the current stage is never used as a back-fill.
            outcome = "open"
            for entry in history[entry_index:]:
                if entry["on"] is None or not entered_on <= entry["on"] <= as_of:
                    continue
                if entry["to_stage"] == "won":
                    outcome = "won"
                elif entry["to_stage"] == "lost":
                    outcome = "lost"
                else:
                    outcome = "open"
            key = contact["organization"].casefold()
            group = groups.get(key)
            if group is None:
                groups[key] = {"display": contact["organization"], "counts": empty_counts()}
            elif contact["organization"] < group["display"]:
                # Display name is the code-point-smallest original value among owning contacts.
                group["display"] = contact["organization"]
            counts = groups[key]["counts"]
            counts["entered"] += 1
            counts[outcome] += 1
            totals["entered"] += 1
            totals[outcome] += 1

        def stats_row(counts):
            row = dict(counts)
            if counts["entered"]:
                # 四舍五入到两位小数; exact integer arithmetic avoids binary float drift.
                rate = (Decimal(counts["won"]) * Decimal(100) / Decimal(counts["entered"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                row["win_rate"] = format(rate, "f")
            else:
                row["win_rate"] = None
            return row

        organizations = []
        for key in sorted(groups):
            group = groups[key]
            row = {"organization": group["display"]}
            row.update(stats_row(group["counts"]))
            organizations.append(row)

        total_row = stats_row(totals)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("organization", "entered", "won", "lost", "open", "win_rate"))
        for row in organizations:
            writer.writerow((row["organization"], row["entered"], row["won"], row["lost"],
                             row["open"], row["win_rate"] if row["win_rate"] is not None else ""))

        return {"total": total_row, "organizations": organizations, "csv": buffer.getvalue()}

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

    def stalled_opportunities(self, as_of, stalled_days, organization=None, tags=None, tag_mode="all"):
        as_of = calendar_day(as_of, "as_of")
        # type(...) is int rejects bools, which are ints in Python but never a day count.
        stalled_days = positive(stalled_days, "stalled_days")
        if organization is not None and not isinstance(organization, str):
            raise ValueError("organization must be a string")
        wanted = [] if tags is None else normalize_tags(tags)
        if tag_mode not in ("all", "any"):
            raise ValueError("tag_mode must be 'all' or 'any'")
        data = self._read()
        contacts = data.get("contacts", {})
        opportunities = data.get("opportunities", {})
        history_store = data.get("stage_history", {})
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
        unknown, aged = [], []
        for opportunity in opportunities.values():
            # Only deals currently qualified are judged; no history is rebuilt for as_of.
            if opportunity["stage"] != "qualified":
                continue
            contact = contacts.get(opportunity["contact_id"])
            if contact is None or not matches(contact):
                continue
            # The start is the last saved record (save order) entering qualified:
            # typically the first entry on a new->qualified path, but it also
            # covers legacy/re-entered histories. Its raw on decides what we know.
            entered_on = None
            has_entry = False
            for entry in history_store.get(opportunity["opportunity_id"], []):
                if entry["to_stage"] == "qualified":
                    has_entry = True
                    entered_on = entry["on"]
            if not has_entry or entered_on is None:
                # No such history at all, or that record carries a null date: the
                # deal still counts as stalled, with no date borrowed as back-fill.
                unknown.append({"opportunity": dict(opportunity), "contact": dict(contact),
                                "entered_on": None, "age_days": None})
            elif entered_on > as_of:
                # Entered qualified only after the cutoff: excluded, even though it
                # is qualified today; history is never reconstructed to as_of.
                continue
            else:
                age_days = (cutoff - date.fromisoformat(entered_on)).days
                if age_days >= stalled_days:
                    aged.append({"opportunity": dict(opportunity), "contact": dict(contact),
                                 "entered_on": entered_on, "age_days": age_days})
        # Unknown starts first by opportunity id code point, the rest by age
        # descending then opportunity id; each opportunity appears at most once.
        unknown.sort(key=lambda row: row["opportunity"]["opportunity_id"])
        aged.sort(key=lambda row: (-row["age_days"], row["opportunity"]["opportunity_id"]))
        return unknown + aged
