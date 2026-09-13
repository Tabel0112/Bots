from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    skill_id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_lookup
                    ON workflows(site_id, operation);
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    skill_id TEXT NOT NULL REFERENCES workflows(skill_id),
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    qualification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, version)
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    skill_id TEXT,
                    version INTEGER,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    operation TEXT NOT NULL,
                    key TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY(operation, key)
                );
                """
            )

    def event(self, stage: str, message: str, data: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO activity_events(timestamp, stage, message, data_json) VALUES (?, ?, ?, ?)",
                (now(), stage, message, json.dumps(data)),
            )

    def activity(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM activity_events ORDER BY sequence DESC LIMIT ?", (limit,)
            ).fetchall()
        events = []
        for row in reversed(rows):
            event = dict(row)
            event["data"] = json.loads(event.pop("data_json"))
            events.append(event)
        return events

    def qualified_candidates(
        self, site_id: str, operation: str
    ) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT w.*, v.version, v.status, v.definition_json
                   FROM workflows w JOIN workflow_versions v USING(skill_id)
                   WHERE w.site_id=? AND w.operation=? AND v.status='qualified'
                   ORDER BY v.version DESC""",
                (site_id, operation),
            ).fetchall()
        candidates = []
        for row in rows:
            candidate = dict(row)
            candidate["definition"] = json.loads(candidate.pop("definition_json"))
            candidates.append(candidate)
        return candidates

    @staticmethod
    def remembered(db, operation, key, body):
        if not key:
            return None
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        row = db.execute(
            "SELECT * FROM idempotency WHERE operation=? AND key=?", (operation, key)
        ).fetchone()
        if row:
            if row["digest"] != digest:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return json.loads(row["result_json"])
        return None

    @staticmethod
    def remember(db, operation, key, body, result):
        if key:
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True).encode()
            ).hexdigest()
            db.execute(
                "INSERT INTO idempotency VALUES (?, ?, ?, ?)",
                (operation, key, digest, json.dumps(result)),
            )

    def save_candidate(self, definition: dict[str, Any], key=None) -> tuple[str, int]:
        skill_id = f"{definition['site_id']}.{definition['operation']}"
        with self._write_lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.remembered(db, "candidate", key, definition)
            if previous:
                return tuple(previous)
            db.execute(
                "INSERT OR IGNORE INTO workflows VALUES (?, ?, ?, ?, ?)",
                (
                    skill_id,
                    definition["site_id"],
                    definition["operation"],
                    definition["description"],
                    now(),
                ),
            )
            version = db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_versions WHERE skill_id=?",
                (skill_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO workflow_versions VALUES (?, ?, 'candidate', ?, '[]', ?)",
                (skill_id, version, json.dumps(definition), now()),
            )
            self.remember(db, "candidate", key, definition, [skill_id, version])
        return skill_id, version

    def workflow(self, skill_id: str, version: int) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT w.*, v.version, v.status, v.definition_json, v.qualification_json
                   FROM workflows w JOIN workflow_versions v USING(skill_id)
                   WHERE w.skill_id=? AND v.version=?""",
                (skill_id, version),
            ).fetchone()
        if not row:
            return None
        workflow = dict(row)
        workflow["definition"] = json.loads(workflow.pop("definition_json"))
        workflow["qualification"] = json.loads(workflow.pop("qualification_json"))
        return workflow

    def workflows(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT w.skill_id, v.version, v.status, w.site_id, w.operation, w.description
                   FROM workflows w JOIN workflow_versions v USING(skill_id)
                   ORDER BY w.skill_id, v.version DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def save_run(
        self, skill_id: str, version: int, report: dict[str, Any], kind: str = "reuse"
    ) -> str:
        run_id = str(uuid4())
        key = report.get("idempotency_key")
        operation = f"run:{skill_id}:{version}"
        with self._write_lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.remembered(db, operation, key, report)
            if previous:
                return previous
            db.execute(
                "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    report["task_id"],
                    report["agent_id"],
                    skill_id,
                    version,
                    kind,
                    report["status"],
                    json.dumps(report),
                    now(),
                ),
            )
            self.remember(db, operation, key, report, run_id)
        return run_id

    def qualify(
        self, skill_id: str, version: int, reports: list[dict[str, Any]]
    ) -> str | None:
        with self._write_lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT definition_json FROM workflow_versions WHERE skill_id=? AND version=?",
                (skill_id, version),
            ).fetchone()
            if not exists:
                return None
            passed = all(report["validation_status"] == "passed" for report in reports)
            distinct = (
                len(
                    {
                        json.dumps(report["parameters"], sort_keys=True)
                        for report in reports
                    }
                )
                >= 3
            )
            has_empty = any(report["empty_result"] for report in reports)
            definition = json.loads(exists["definition_json"])
            if definition.get("schema_version") == "0.2":
                # Qualification must cite stored replays for this exact candidate.
                sessions, executions, evidence = set(), set(), set()
                original = definition["source"]["context"]
                required_checks = {c["check_id"] for c in definition["source"]["validation"]}
                for report in reports:
                    row = db.execute(
                        "SELECT report_json FROM workflow_runs WHERE run_id=? AND skill_id=? AND version=? AND kind='qualification'",
                        (report.get("run_id"), skill_id, version),
                    ).fetchone()
                    if not row:
                        passed = False
                        continue
                    run = json.loads(row["report_json"])
                    context = run.get("context") or {}
                    session, execution = (
                        context.get("session_ref"),
                        context.get("execution_id"),
                    )
                    refs = set(run.get("evidence_refs", []))
                    checks = run.get("validation", [])
                    if {c["check_id"] for c in checks} != required_checks:
                        passed = False
                    if context.get("browser_backend") != original.get("browser_backend"):
                        passed = False
                    passed = passed and (
                        run["status"] == "succeeded"
                        and bool(checks)
                        and all(c["passed"] for c in checks)
                        and run.get("parameters") == report["parameters"]
                        and bool(refs)
                        and refs == set(report["evidence_refs"])
                        and refs.issubset(run.get("artifacts", {}))
                        and not refs.intersection(evidence)
                        and bool(session)
                        and session not in sessions
                        and session != original.get("session_ref")
                        and bool(execution)
                        and execution not in executions
                        and execution != original.get("execution_id")
                        and context.get("session_disposition") == "released"
                        and report["empty_result"] == (run.get("result") == [])
                        and report["parameters"] != definition.get("parameters")
                    )
                    sessions.add(session)
                    executions.add(execution)
                    evidence.update(refs)
            status = "qualified" if passed and distinct and has_empty else "candidate"
            db.execute(
                "UPDATE workflow_versions SET status=?, qualification_json=? WHERE skill_id=? AND version=?",
                (status, json.dumps(reports), skill_id, version),
            )
        return status
