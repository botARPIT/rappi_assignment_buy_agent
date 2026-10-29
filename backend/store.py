"""SQLite transactions fence approval against stale state and duplicate purchases."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.agent.data import get_cases
from backend.agent.policy import ACTIONABLE, assess_purchase, matches_authorization
from backend.supplier import submit_order, validate_response


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS situations (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS purchase_drafts (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(case_id, revision));
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            for case in get_cases().values():
                db.execute("INSERT OR IGNORE INTO situations VALUES (?,?)", (case["id"], json.dumps(case)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def get_case(self, case_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM situations WHERE id=?", (case_id,)).fetchone()
        if not row:
            raise KeyError(case_id)
        return json.loads(row["payload"])

    def cases(self):
        with self.connect() as db:
            return [
                json.loads(r["payload"]) for r in db.execute("SELECT payload FROM situations ORDER BY id")
            ]

    def update_case(self, case):
        """Operational-data adapter, used by fixtures and tests; callers must supply the full case."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM situations WHERE id=?", (case["id"],)).fetchone()
            updated = {**case, "revision": json.loads(old["payload"])["revision"] + 1 if old else 1}
            db.execute("INSERT OR REPLACE INTO situations VALUES (?,?)", (updated["id"], json.dumps(updated)))
            self._event(db, case["id"], "operational_data_updated", {"revision": updated["revision"]})
        return updated

    def create_draft(self, case_id, decision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            case = json.loads(
                db.execute("SELECT payload FROM situations WHERE id=?", (case_id,)).fetchone()["payload"]
            )
            if not matches_authorization(assess_purchase(case), decision):
                raise ConflictError("Purchasing state changed; investigate again before preparing an order.")
            row = db.execute(
                "SELECT payload FROM purchase_drafts WHERE case_id=? AND revision=?",
                (case_id, case["revision"]),
            ).fetchone()
            if row:
                return json.loads(row["payload"])
            draft = {
                "id": "DRAFT-" + uuid.uuid4().hex[:10].upper(),
                "case_id": case_id,
                "case_revision": case["revision"],
                "status": "awaiting_approval",
                "supplier": decision["supplier"],
                "quantity": decision["quantity"],
                "estimated_cost": decision["estimated_cost"],
                "authorization": decision,
                "created_at": self.now(),
            }
            db.execute(
                "INSERT INTO purchase_drafts VALUES (?,?,?,?,?)",
                (draft["id"], case_id, case["revision"], draft["status"], json.dumps(draft)),
            )
            self._event(
                db, case_id, "draft_created", {"draft_id": draft["id"], "quantity": draft["quantity"]}
            )
        return draft

    def get_draft(self, draft_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM purchase_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise KeyError(draft_id)
        return json.loads(row["payload"])

    def approve(self, draft_id, *, approved, outcome="confirmed", actor="demo-buyer"):
        if approved is not True:
            raise ConflictError("Explicit buyer approval is required.")
        if outcome not in {"confirmed", "rejected", "partial"}:
            raise ValueError("Unknown mock supplier outcome.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM purchase_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            draft = json.loads(row["payload"])
            if draft["status"] == "executed":
                return {**draft["result"], "idempotent_replay": True}
            if draft["status"] != "awaiting_approval":
                raise ConflictError("This draft is no longer awaiting approval.")
            case = json.loads(
                db.execute("SELECT payload FROM situations WHERE id=?", (draft["case_id"],)).fetchone()[
                    "payload"
                ]
            )
            authorization = draft["authorization"]
            fresh = assess_purchase(case)
            if fresh["decision"] not in ACTIONABLE or not matches_authorization(fresh, authorization):
                raise ConflictError(
                    "Policy or purchasing state changed; the draft is stale. Investigate again."
                )
            order = submit_order(authorization, draft_id, outcome)
            validation = validate_response(authorization, order)
            if validation["commit_allowed"] and order["confirmed_quantity"]:
                case["budget_cents"] -= order["total_cost_cents"]
                case["additional_incoming"].append(
                    {
                        "id": order["id"],
                        "quantity": order["confirmed_quantity"],
                        "eta_days": order["eta_days"],
                    }
                )
            case["revision"] += 1
            follow_up = None
            if not validation["valid"]:
                reassessed = assess_purchase(case)
                follow_up = {
                    "status": "needs_buyer_review",
                    "decision": reassessed["decision"],
                    "remaining_gap": reassessed["evidence"].get("uncovered_units"),
                    "message": "Supplier outcome differed from the approved order. Reassess the remaining coverage gap before approving another action.",
                }
            result = {
                "action": order,
                "validation": validation,
                "follow_up": follow_up,
                "idempotent_replay": False,
                "approved_by": actor,
            }
            draft.update(status="executed", result=result)
            db.execute(
                "UPDATE purchase_drafts SET status=?,payload=? WHERE id=?",
                (draft["status"], json.dumps(draft), draft_id),
            )
            db.execute("UPDATE situations SET payload=? WHERE id=?", (json.dumps(case), case["id"]))
            self._event(db, case["id"], "buyer_approved", {"draft_id": draft_id, "actor": actor})
            self._event(db, case["id"], "supplier_response", order)
            self._event(db, case["id"], "order_validated", validation)
            if follow_up:
                self._event(db, case["id"], "buyer_review_required", follow_up)
        return result

    def reject(self, draft_id, actor="demo-buyer"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM purchase_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            draft = json.loads(row["payload"])
            if draft["status"] != "awaiting_approval":
                raise ConflictError("Only an awaiting draft can be rejected.")
            draft["status"] = "buyer_rejected"
            db.execute(
                "UPDATE purchase_drafts SET status=?,payload=? WHERE id=?",
                (draft["status"], json.dumps(draft), draft_id),
            )
            self._event(db, draft["case_id"], "buyer_rejected", {"draft_id": draft_id, "actor": actor})
        return draft

    def record_run(self, output):
        with self.connect() as db:
            db.execute("INSERT INTO agent_runs VALUES (?,?)", (output["run_id"], json.dumps(output)))
            self._event(
                db,
                output["case"]["id"],
                "investigation_completed",
                {"run_id": output["run_id"], "decision": output["decision"]["decision"]},
            )

    def _event(self, db, case_id, event_type, payload):
        db.execute(
            "INSERT INTO audit_events (case_id,event_type,payload,created_at) VALUES (?,?,?,?)",
            (case_id, event_type, json.dumps(payload), self.now()),
        )

    def events(self, case_id=None):
        with self.connect() as db:
            sql = (
                "SELECT * FROM audit_events"
                + (" WHERE case_id=?" if case_id else "")
                + " ORDER BY id DESC LIMIT 50"
            )
            rows = db.execute(sql, (case_id,) if case_id else ()).fetchall()
        return [
            {
                "id": r["id"],
                "case_id": r["case_id"],
                "type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
