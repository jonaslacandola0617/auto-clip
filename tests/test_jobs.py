import tempfile
import threading
import time
import unittest
from pathlib import Path

from autoclip.jobs import JobManager


def wait_for(manager: JobManager, job_id: str, states: set[str], timeout: float = 2) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.get(job_id).state
        if state in states:
            return state
        time.sleep(0.01)
    return manager.get(job_id).state


class JobTests(unittest.TestCase):
    def test_interrupted_job_is_recovered_as_failed_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.json"
            manager = JobManager(path)
            started = threading.Event()
            release = threading.Event()

            def runner(_id, _event, update):
                update(.25, "Transcribing locally", True)
                started.set()
                release.wait(1)

            job = manager.start("transcribe", runner)
            self.assertTrue(started.wait(1))
            recovered = JobManager(path).get(job.id)
            self.assertEqual(recovered.state, "failed")
            self.assertEqual(recovered.error["code"], "interrupted")
            release.set()
            self.assertEqual(wait_for(manager, job.id, {"completed"}), "completed")

    def test_job_completes_with_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(Path(directory) / "jobs.json")
            job = manager.start("doctor", lambda _id, _event, update: update(.8, "Checking", True))
            self.assertEqual(wait_for(manager, job.id, {"completed"}), "completed")
            completed = manager.get(job.id)
            self.assertEqual(completed.progress, 1.0)
            self.assertFalse(completed.cancellable)

    def test_cooperative_cancellation_does_not_claim_early_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(Path(directory) / "jobs.json")
            started = threading.Event()

            def runner(_id, event, update):
                update(.2, "Waiting", True)
                started.set()
                event.wait(1)

            job = manager.start("cooperative", runner)
            self.assertTrue(started.wait(1))
            cancelling = manager.cancel(job.id)
            self.assertEqual(cancelling.state, "running")
            self.assertEqual(wait_for(manager, job.id, {"cancelled"}), "cancelled")

    def test_non_cancellable_stage_rejects_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(Path(directory) / "jobs.json")
            started = threading.Event()
            release = threading.Event()

            def runner(_id, _event, update):
                update(.5, "Committed work", False)
                started.set()
                release.wait(1)

            job = manager.start("protected", runner)
            self.assertTrue(started.wait(1))
            with self.assertRaises(RuntimeError):
                manager.cancel(job.id)
            release.set()
            self.assertEqual(wait_for(manager, job.id, {"completed"}), "completed")


if __name__ == "__main__":
    unittest.main()
