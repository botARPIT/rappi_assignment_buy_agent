"""Zero-extra-dependency HTTP API and static frontend server."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from backend.agent.agent import PurchasingAgent
from backend.agent.data import get_cases
from backend.agent.policy import assess_shortfall
from backend.agent.tools import PurchasingTools
from backend.store import Store


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"
DATA_DIR = ROOT / ".data"
CASES = get_cases()
STORE = Store(DATA_DIR / "purchasing.sqlite3")
AGENT = PurchasingAgent(CASES, STORE)


class Handler(BaseHTTPRequestHandler):
    server_version = "BuyerAgent/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/cases":
            self.send_json([{
                "id": c["id"], "title": c["title"], "scenario": c["scenario"],
                "product": c["product"], "sku": c["sku"], "node": c["node"],
                "purchase_order": c["purchase_order"], "inventory": c["inventory"],
                "supplier_lead_days": c["supplier_lead_days"],
                "budget_remaining": c["budget_remaining"],
                "storage_capacity": c["storage_capacity"],
            } for c in CASES.values()])
            return
        if path == "/api/events":
            case_id = urlparse(self.path).query.split("case_id=", 1)[1].split("&", 1)[0] if "case_id=" in self.path else None
            self.send_json(STORE.events(case_id))
            return
        if path == "/api/evals":
            from evaluations.run_eval import run
            self.send_json(run())
            return
        if path == "/" or path == "/index.html":
            self.serve_file(WEB / "index.html")
            return
        if path.startswith("/static/"):
            candidate = (WEB / path.removeprefix("/static/")).resolve()
            if candidate.parent != WEB.resolve() or not candidate.is_file():
                self.send_error(404)
                return
            self.serve_file(candidate)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_json()
        if path == "/api/agent/run":
            case_id = body.get("case_id")
            try:
                result = AGENT.investigate_shortfall(case_id)
            except KeyError:
                self.send_json({"error": "unknown_case"}, 404)
                return
            self.send_json(result)
            return

        match = re.fullmatch(r"/api/drafts/([^/]+)/approve", path)
        if match:
            draft_id = match.group(1)
            draft = STORE.get_draft(draft_id)
            if not draft:
                self.send_json({"error": "draft_not_found"}, 404)
                return
            case = CASES.get(draft["case_id"])
            if not case:
                self.send_json({"error": "case_not_found"}, 404)
                return
            tools = PurchasingTools(CASES, STORE)
            po = tools.inspect_open_purchase_order(case)
            inventory = tools.get_inventory_and_demand(case)
            alternate = tools.find_alternate_supplier(case)
            constraints = tools.check_constraints(case)
            current_decision = assess_shortfall(case, po, inventory, alternate, constraints)
            if current_decision["decision"] != "supplemental_purchase" or current_decision["quantity"] != draft["quantity"] or current_decision["supplier"] != draft["supplier"]:
                self.send_json({"error": "decision_changed_reanalyze", "decision": current_decision}, 409)
                return
            action = tools.execute_order(draft_id, bool(body.get("simulate_failure")))
            validation = tools.validate_order(draft_id) if action.get("ok") else None
            self.send_json({"action": action, "validation": validation, "trace": tools.trace})
            return
        self.send_error(404)

    def serve_file(self, path):
        content = path.read_bytes()
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    port = 8000
    print(f"Buyer Agent running at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
