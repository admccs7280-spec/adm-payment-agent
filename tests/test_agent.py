from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from adm_payment_agent import JobConflict, JobState, JobStore, PaymentAgent, PaymentJob, PaymentResult, ResultKind


def sample_job(*, job_id: str = "job-001", order_id: str = "order-001") -> PaymentJob:
    return PaymentJob(
        job_id=job_id,
        binance_order_id=order_id,
        agent_id="agent-lab-01",
        payload_hash=f"sha256:{job_id}",
        amount="100.00",
        currency="VES",
        payee_fingerprint="payee:masked",
        issued_at="2026-11-23T00:00:00+00:00",
        expires_at="2026-11-23T00:15:00+00:00",
        metadata={"test": True},
    )


class SuccessExecutor:
    def __init__(self, capture_path: Path) -> None:
        self.capture_path = capture_path
        self.executed: list[str] = []

    def execute(self, job: PaymentJob) -> PaymentResult:
        self.executed.append(job.job_id)
        self.capture_path.write_bytes(b"not a real payment capture")
        return PaymentResult(
            kind=ResultKind.SUCCESS,
            message="Éxito simulado",
            reference="SIM-001",
            capture_path=str(self.capture_path),
        )


class ExplodingExecutor:
    def execute(self, _: PaymentJob) -> PaymentResult:
        raise RuntimeError("simulador interrumpido")


class PaymentAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = JobStore(self.root / "state" / "agent.sqlite")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_accept_is_idempotent_and_order_is_unique(self) -> None:
        job = sample_job()
        stored, created = self.store.accept(job)
        repeated, repeated_created = self.store.accept(job)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(stored.state, JobState.ACCEPTED)
        self.assertEqual(repeated.job.job_id, job.job_id)
        self.assertEqual(
            [state for state, _ in self.store.events_for(job.job_id)],
            [JobState.RECEIVED, JobState.VALIDATED, JobState.ASSIGNED, JobState.ACCEPTED],
        )

        conflict = replace(job, job_id="job-002", payload_hash="sha256:other")
        with self.assertRaises(JobConflict):
            self.store.accept(conflict)

    def test_success_delivers_capture_to_outbox(self) -> None:
        executor = SuccessExecutor(self.root / "captures" / "proof.png")
        executor.capture_path.parent.mkdir()
        agent = PaymentAgent(self.store, executor)
        job = sample_job()

        agent.accept(job)
        result = agent.process_one()

        self.assertIsNotNone(result)
        self.assertEqual(result.state, JobState.CAPTURE_RECEIVED)
        self.assertEqual(executor.executed, [job.job_id])
        outbox = self.store.pending_outbox()
        self.assertIn("capture", [item.kind for item in outbox])
        capture = next(item for item in outbox if item.kind == "capture")
        self.assertEqual(capture.job_id, job.job_id)
        self.assertIsNotNone(capture.capture_sha256)

        self.store.acknowledge_outbox(item.item_id for item in outbox)
        self.assertEqual(self.store.pending_outbox(), [])

    def test_adapter_failure_becomes_uncertain_and_is_not_retried(self) -> None:
        agent = PaymentAgent(self.store, ExplodingExecutor())
        job = sample_job()

        agent.accept(job)
        result = agent.process_one()

        self.assertEqual(result.state, JobState.UNCERTAIN)
        self.assertIsNone(agent.process_one())
        self.assertEqual(self.store.state_of(job.job_id), JobState.UNCERTAIN)

    def test_restart_during_execution_becomes_uncertain(self) -> None:
        job = sample_job()
        self.store.accept(job)
        self.store.start(job.job_id)

        recovered = self.store.recover_interrupted()

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, JobState.UNCERTAIN)
        self.assertEqual(self.store.state_of(job.job_id), JobState.UNCERTAIN)

    def test_success_without_capture_is_uncertain(self) -> None:
        class MissingCaptureExecutor:
            def execute(self, _: PaymentJob) -> PaymentResult:
                return PaymentResult(kind=ResultKind.SUCCESS, message="Éxito sin evidencia")

        agent = PaymentAgent(self.store, MissingCaptureExecutor())
        job = sample_job()
        agent.accept(job)

        result = agent.process_one()

        self.assertEqual(result.state, JobState.UNCERTAIN)


if __name__ == "__main__":
    unittest.main()
