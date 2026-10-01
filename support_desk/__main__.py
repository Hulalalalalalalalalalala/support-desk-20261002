import argparse
import json
from pathlib import Path
import sys
import tempfile
from . import SupportDesk

ACTIONS = {'open': 'open_ticket', 'get': 'get', 'assign': 'assign', 'note': 'note', 'close': 'close', 'list': 'list_tickets', 'respond': 'respond', 'response-stats': 'response_stats', 'response-queue': 'response_queue', 'knowledge-publish': 'publish_knowledge', 'knowledge-search': 'search_knowledge'}

def samples(name):
    return json.loads((Path(__file__).resolve().parent.parent / "examples" / name).read_text(encoding="utf-8"))

def demo(app):
    for ticket in samples("tickets.json"):
        app.open_ticket(**ticket)
    app.assign(**samples("assignment.json"))
    app.note("T-001", "已确认订单存在")
    return app.close(**samples("resolution.json"))

def main(argv=None):
    parser = argparse.ArgumentParser(description="客服工单工作台")
    parser.add_argument("--root", required=True, help="local data directory")
    parser.add_argument("action", choices=[*ACTIONS, "demo"])
    parser.add_argument("input", nargs="?", help="UTF-8 JSON object, or array of objects, containing API arguments")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            # Sample operations run in a fresh child directory and never overwrite user's data.
            Path(args.root).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sample-", dir=args.root) as location:
                value = demo(SupportDesk(location))
        else:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else {}
            app = SupportDesk(args.root)
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
