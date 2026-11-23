"""Persistencia local para evitar pagos duplicados y pérdidas tras un reinicio."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import JobState, OutboxItem, PaymentJob, PaymentResult, ResultKind, StoredJob


class JobConflict(RuntimeError):
    """El mismo identificador llegó con datos incompatibles o la orden ya tiene trabajo."""


class InvalidTransition(RuntimeError):
    """La transición solicitada no está permitida para el estado persistido."""


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    binance_order_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    state TEXT NOT NULL,
    note TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    capture_sha256 TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs(state, accepted_at);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(delivered_at, item_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Almacén SQLite de un solo agente local.

    El agente guarda el trabajo antes de aceptar. Cada evento se refleja en una outbox
    que un transporte futuro podrá entregar a ADM y confirmar sin volver a pagar.
    """

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def accept(self, job: PaymentJob) -> tuple[StoredJob, bool]:
        """Guarda y acepta una orden una sola vez.

        Devuelve ``created=False`` cuando el controlador reintenta exactamente el
        mismo trabajo. Nunca crea un segundo trabajo para la misma orden Binance.
        """

        self._validate_job(job)
        with self._conn:
            known = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job.job_id,)
            ).fetchone()
            if known is not None:
                if known["payload_hash"] != job.payload_hash:
                    raise JobConflict("job_id recibido con una huella distinta")
                return self._to_stored(known), False

            same_order = self._conn.execute(
                "SELECT job_id FROM jobs WHERE binance_order_id = ?",
                (job.binance_order_id,),
            ).fetchone()
            if same_order is not None:
                raise JobConflict("la orden Binance ya tiene un trabajo local")

            now = _now()
            self._conn.execute(
                """
                INSERT INTO jobs (
                    job_id, binance_order_id, agent_id, payload_hash, payload_json,
                    state, accepted_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.binance_order_id,
                    job.agent_id,
                    job.payload_hash,
                    json.dumps(job.to_dict(), sort_keys=True, separators=(",", ":")),
                    JobState.ACCEPTED.value,
                    now,
                    now,
                ),
            )
            for state, note in (
                (JobState.RECEIVED, "Trabajo recibido desde ADM"),
                (JobState.VALIDATED, "Contrato local validado"),
                (JobState.ASSIGNED, "Trabajo ya venía asignado a este agente"),
                (JobState.ACCEPTED, "Trabajo persistido y aceptado"),
            ):
                self._append_event(job.job_id, state, note, now)

            return self.get(job.job_id), True

    def next_accepted(self) -> StoredJob | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY accepted_at, job_id LIMIT 1",
            (JobState.ACCEPTED.value,),
        ).fetchone()
        return self._to_stored(row) if row is not None else None

    def get(self, job_id: str) -> StoredJob:
        row = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._to_stored(row)

    def state_of(self, job_id: str) -> JobState:
        return self.get(job_id).state

    def start(self, job_id: str) -> StoredJob:
        return self._transition(
            job_id,
            expected={JobState.ACCEPTED},
            target=JobState.RUNNING,
            note="Ejecución local iniciada",
        )

    def complete(self, job_id: str, result: PaymentResult) -> StoredJob:
        """Persiste el único resultado de una ejecución; nunca agenda reintentos."""

        current = self.get(job_id)
        if current.state is not JobState.RUNNING:
            raise InvalidTransition(f"no se puede completar desde {current.state.value}")

        if result.kind is ResultKind.CLEAN_FAILURE:
            return self._transition(
                job_id,
                expected={JobState.RUNNING},
                target=JobState.CLEAN_FAILURE,
                note=result.message,
            )

        if result.kind is ResultKind.UNCERTAIN:
            return self._transition(
                job_id,
                expected={JobState.RUNNING},
                target=JobState.UNCERTAIN,
                note=result.message,
            )

        capture_path = Path(result.capture_path or "")
        if not result.capture_path or not capture_path.is_file():
            return self._transition(
                job_id,
                expected={JobState.RUNNING},
                target=JobState.UNCERTAIN,
                note="El adaptador informó éxito sin una captura verificable",
            )

        with self._conn:
            self._transition(
                job_id,
                expected={JobState.RUNNING},
                target=JobState.PAYMENT_VERIFIED,
                note=result.message,
            )
            updated = self._transition(
                job_id,
                expected={JobState.PAYMENT_VERIFIED},
                target=JobState.CAPTURE_RECEIVED,
                note="Captura local disponible para entregar al controlador",
            )
            self._enqueue_capture(job_id, capture_path, result.reference)
            return updated

    def recover_interrupted(self) -> list[StoredJob]:
        """Convierte ejecuciones interrumpidas en inciertas en vez de reanudarlas."""

        rows = self._conn.execute(
            "SELECT job_id FROM jobs WHERE state = ? ORDER BY accepted_at",
            (JobState.RUNNING.value,),
        ).fetchall()
        return [
            self._transition(
                row["job_id"],
                expected={JobState.RUNNING},
                target=JobState.UNCERTAIN,
                note="El agente se reinició durante la ejecución; requiere revisión humana",
            )
            for row in rows
        ]

    def events_for(self, job_id: str) -> list[tuple[JobState, str]]:
        rows = self._conn.execute(
            "SELECT state, note FROM events WHERE job_id = ? ORDER BY event_id", (job_id,)
        ).fetchall()
        return [(JobState(row["state"]), str(row["note"])) for row in rows]

    def pending_outbox(self) -> list[OutboxItem]:
        rows = self._conn.execute(
            """
            SELECT item_id, job_id, kind, payload_json, capture_sha256
            FROM outbox
            WHERE delivered_at IS NULL
            ORDER BY item_id
            """
        ).fetchall()
        return [
            OutboxItem(
                item_id=int(row["item_id"]),
                job_id=str(row["job_id"]),
                kind=str(row["kind"]),
                payload=json.loads(row["payload_json"]),
                capture_sha256=row["capture_sha256"],
            )
            for row in rows
        ]

    def acknowledge_outbox(self, item_ids: Iterable[int]) -> None:
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        with self._conn:
            self._conn.execute(
                f"UPDATE outbox SET delivered_at = ? WHERE item_id IN ({marks})",
                [_now(), *ids],
            )

    def _transition(
        self,
        job_id: str,
        *,
        expected: set[JobState],
        target: JobState,
        note: str,
    ) -> StoredJob:
        current = self.get(job_id)
        if current.state not in expected:
            allowed = ", ".join(state.value for state in sorted(expected, key=lambda value: value.value))
            raise InvalidTransition(f"{current.state.value} no permite pasar a {target.value}; esperado: {allowed}")

        now = _now()
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (target.value, now, job_id),
            )
            self._append_event(job_id, target, note, now)
        return self.get(job_id)

    def _append_event(self, job_id: str, state: JobState, note: str, occurred_at: str) -> None:
        self._conn.execute(
            "INSERT INTO events (job_id, state, note, occurred_at) VALUES (?, ?, ?, ?)",
            (job_id, state.value, note, occurred_at),
        )
        payload = {
            "job_id": job_id,
            "state": state.value,
            "note": note,
            "occurred_at": occurred_at,
        }
        self._conn.execute(
            """
            INSERT INTO outbox (job_id, kind, payload_json, capture_sha256, created_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (job_id, "event", json.dumps(payload, sort_keys=True), occurred_at),
        )

    def _enqueue_capture(self, job_id: str, capture_path: Path, reference: str | None) -> None:
        digest = hashlib.sha256(capture_path.read_bytes()).hexdigest()
        payload = {
            "job_id": job_id,
            "reference": reference,
            "local_path": str(capture_path),
        }
        self._conn.execute(
            """
            INSERT INTO outbox (job_id, kind, payload_json, capture_sha256, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, "capture", json.dumps(payload, sort_keys=True), digest, _now()),
        )

    @staticmethod
    def _validate_job(job: PaymentJob) -> None:
        required = {
            "job_id": job.job_id,
            "binance_order_id": job.binance_order_id,
            "agent_id": job.agent_id,
            "payload_hash": job.payload_hash,
            "amount": job.amount,
            "currency": job.currency,
            "payee_fingerprint": job.payee_fingerprint,
            "issued_at": job.issued_at,
            "expires_at": job.expires_at,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"faltan campos obligatorios: {', '.join(missing)}")

    @staticmethod
    def _to_stored(row: sqlite3.Row) -> StoredJob:
        return StoredJob(
            job=PaymentJob.from_dict(json.loads(row["payload_json"])),
            state=JobState(row["state"]),
            accepted_at=str(row["accepted_at"]),
            updated_at=str(row["updated_at"]),
        )
