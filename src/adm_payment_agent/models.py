"""Contratos puros: no incluyen credenciales, teléfono, banco ni red."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class JobState(str, Enum):
    RECEIVED = "RECIBIDA"
    VALIDATED = "VALIDADA"
    ASSIGNED = "ASIGNADA"
    ACCEPTED = "ACEPTADA"
    RUNNING = "EJECUTANDO"
    PAYMENT_VERIFIED = "PAGO_VERIFICADO"
    CAPTURE_RECEIVED = "CAPTURA_RECIBIDA"
    CLEAN_FAILURE = "FALLO_LIMPIO"
    UNCERTAIN = "INCIERTO"


class ResultKind(str, Enum):
    SUCCESS = "SUCCESS"
    CLEAN_FAILURE = "CLEAN_FAILURE"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class PaymentJob:
    """Trabajo ya validado por el controlador y asignado a un agente concreto."""

    job_id: str
    binance_order_id: str
    agent_id: str
    payload_hash: str
    amount: str
    currency: str
    payee_fingerprint: str
    issued_at: str
    expires_at: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PaymentJob":
        return cls(
            job_id=str(raw["job_id"]),
            binance_order_id=str(raw["binance_order_id"]),
            agent_id=str(raw["agent_id"]),
            payload_hash=str(raw["payload_hash"]),
            amount=str(raw["amount"]),
            currency=str(raw["currency"]),
            payee_fingerprint=str(raw["payee_fingerprint"]),
            issued_at=str(raw["issued_at"]),
            expires_at=str(raw["expires_at"]),
            metadata=dict(raw.get("metadata", {})),
        )


@dataclass(frozen=True)
class PaymentResult:
    """Resultado devuelto por un adaptador; nunca ordena un reintento."""

    kind: ResultKind
    message: str
    reference: str | None = None
    capture_path: str | None = None


@dataclass(frozen=True)
class StoredJob:
    job: PaymentJob
    state: JobState
    accepted_at: str
    updated_at: str


@dataclass(frozen=True)
class OutboxItem:
    item_id: int
    job_id: str
    kind: str
    payload: dict[str, Any]
    capture_sha256: str | None
