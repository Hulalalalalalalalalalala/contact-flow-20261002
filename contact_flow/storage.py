from pathlib import Path
import json
import os
import re
import tempfile
from datetime import date

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

class JsonStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "data.json"

    def _read(self):
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("stored document must be an object")
        return value

    def _write(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".data-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonempty string")
    return value.strip()

def positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer")
    return value

def calendar_day(value, label):
    # Accept only a trimmed YYYY-MM-DD string naming a real calendar date
    # (past dates and legal leap days allowed); normalize via round-trip.
    if not isinstance(value, str):
        raise ValueError(label + " must be a YYYY-MM-DD string")
    clean = value.strip()
    if not DATE_RE.fullmatch(clean):
        raise ValueError(label + " must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(clean).isoformat()
    except ValueError as error:
        raise ValueError(label + " must be a real calendar date") from error
