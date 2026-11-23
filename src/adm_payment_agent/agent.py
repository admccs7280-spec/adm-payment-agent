"""Orquestación sin red: un trabajo local a la vez y sin reintentos de pago."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import PaymentJob, PaymentResult, ResultKind, StoredJob
from .store import JobStore


class PaymentExecutor(Protocol):
    """Interfaz que implementará cada adaptador de teléfono/banco validado."""

    def execute(self, job: PaymentJob) -> PaymentResult:
        """Ejecuta una sola vez y devuelve el resultado observado."""


class PaymentAgent:
    """Núcleo local que delega una sola orden a un ejecutor por vez."""

    def __init__(self, store: JobStore, executor: PaymentExecutor) -> None:
        self._store = store
        self._executor = executor

    def accept(self, job: PaymentJob) -> tuple[StoredJob, bool]:
        return self._store.accept(job)

    def process_one(self) -> StoredJob | None:
        """Procesa el siguiente trabajo aceptado; nunca reintenta uno incierto."""

        next_job = self._store.next_accepted()
        if next_job is None:
            return None

        running = self._store.start(next_job.job.job_id)
        try:
            result = self._executor.execute(running.job)
        except Exception as exc:  # Un error del adaptador tras iniciar no demuestra que no pagó.
            return self._store.complete(
                running.job.job_id,
                PaymentResult(
                    kind=ResultKind.UNCERTAIN,
                    message=f"El adaptador falló durante la ejecución: {type(exc).__name__}",
                ),
            )

        if result.kind is ResultKind.SUCCESS:
            capture = Path(result.capture_path or "")
            if not result.capture_path or not capture.is_file():
                result = PaymentResult(
                    kind=ResultKind.UNCERTAIN,
                    message="El adaptador informó éxito sin una captura local verificable",
                    reference=result.reference,
                )
        return self._store.complete(running.job.job_id, result)

    def recover_after_restart(self) -> list[StoredJob]:
        return self._store.recover_interrupted()
