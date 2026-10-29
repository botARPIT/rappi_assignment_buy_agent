"""SQLite persistence for drafts, executed mock orders, and audit events."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def create_draft(self, case_id, decision):
        draft_id = "DRAFT-" + uuid.uuid4().hex[:8].upper()
        draft = {
            "id": draft_id, "case_id": case_id, "status": "awaiting_approval",
            "supplier": decision["supplier"], "quantity": decision["quantity"],
            "estimated_cost": decision["estimated_cost"],
        }
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, ?)",
                       (draft_id, case_id, draft["status"], json.dumps(draft), self.now()))
            self._event(db, case_id, "draft_created", draft)
        return draft

    def get_draft(self, draft_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def execute_draft(self, draft_id, simulate_failure=False):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT case_id, status, payload FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "draft_not_found"}
            if row["status"] != "awaiting_approval":
                return {"ok": False, "error": "draft_not_awaiting_approval", "status": row["status"]}
            draft = json.loads(row["payload"])
            draft["status"] = "supplier_rejected" if simulate_failure else "supplier_confirmed"
            draft["purchase_order_id"] = "PO-" + uuid.uuid4().hex[:6].upper()
            db.execute("UPDATE drafts SET status=?, payload=? WHERE id=?",
                       (draft["status"], json.dumps(draft), draft_id))
            self._event(db, row["case_id"], "order_execution", draft)
        return {"ok": True, **draft}

    def validate_draft(self, draft_id):
        draft = self.get_draft(draft_id)
        if not draft:
            return {"valid": False, "status": "missing", "message": "Draft not found."}
        if draft["status"] == "supplier_confirmed":
            result = {"valid": True, "status": "confirmed", "message": "Supplier confirmed the supplemental order."}
        elif draft["status"] == "supplier_rejected":
            result = {"valid": False, "status": "supplier_rejected", "message": "Supplier did not accept the order. Escalate and reassess coverage."}
        else:
            result = {"valid": False, "status": draft["status"], "message": "Order is not yet confirmed."}
        with self.lock, self.connect() as db:
            self._event(db, draft["case_id"], "order_validation", {"draft_id": draft_id, **result})
        return result

    def _event(self, db, case_id, event_type, payload):
        db.execute("INSERT INTO events (case_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                   (case_id, event_type, json.dumps(payload), self.now()))

    def events(self, case_id=None):
        with self.connect() as db:
            if case_id:
                rows = db.execute("SELECT * FROM events WHERE case_id=? ORDER BY id DESC LIMIT 50", (case_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 50").fetchall()
        return [{"id": r["id"], "case_id": r["case_id"], "type": r["event_type"],
                 "payload": json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows]
