import argparse
import json
from pathlib import Path
import sys
import tempfile
from . import ContactFlow

ACTIONS = {'add': 'add_contact', 'update-contact': 'update_contact', 'update-contacts': 'update_contacts', 'follow-up': 'follow_up', 'find': 'find', 'duplicate-candidates': 'duplicate_candidates', 'timeline': 'timeline', 'merge': 'merge_contacts', 'set-tags': 'set_tags', 'get-tags': 'get_tags', 'import-contacts': 'import_contacts', 'import-followups': 'import_followups', 'import-opportunities': 'import_opportunities', 'add-opportunity': 'add_opportunity', 'set-stage': 'set_stage', 'set-stages': 'set_stages', 'stage-history': 'stage_history', 'stage-change-report': 'stage_change_report', 'win-cycle-report': 'win_cycle_report', 'find-opportunities': 'find_opportunities', 'transfer-opportunities': 'transfer_opportunities', 'funnel-report': 'funnel_report', 'set-opportunity-amount': 'set_opportunity_amount', 'opportunity-amount-report': 'opportunity_amount_report', 'set-reminder': 'set_reminder', 'clear-reminder': 'clear_reminder', 'complete-reminder': 'complete_reminder', 'complete-reminders': 'complete_reminders', 'due-reminders': 'due_reminders', 'followup-report': 'followup_report', 'inactive-contacts': 'inactive_contacts', 'stalled-opportunities': 'stalled_opportunities'}

def samples(name):
    return json.loads((Path(__file__).resolve().parent.parent / "examples" / name).read_text(encoding="utf-8"))

def demo(app):
    for contact in samples("contacts.json"):
        app.add_contact(**contact)
    for entry in samples("followups.json"):
        app.follow_up(**entry)
    return app.timeline("C-001")

def main(argv=None):
    parser = argparse.ArgumentParser(description="客户跟进工作台")
    parser.add_argument("--root", required=True, help="local data directory")
    parser.add_argument("action", choices=[*ACTIONS, "demo"])
    parser.add_argument("input", nargs="?", help="UTF-8 JSON object, or array of objects, containing API arguments")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            # Sample operations run in a fresh child directory and never overwrite user's data.
            Path(args.root).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sample-", dir=args.root) as location:
                value = demo(ContactFlow(location))
        else:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else {}
            app = ContactFlow(args.root)
            method = getattr(app, ACTIONS[args.action])
            if isinstance(payload, list):
                value = []
                for row in payload:
                    if not isinstance(row, dict):
                        raise ValueError("each input must be an object")
                    value.append(method(**row))
            elif isinstance(payload, dict):
                value = method(**payload)
            else:
                raise ValueError("input must be an object or array")
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
