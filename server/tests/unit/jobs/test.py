from jobs.worker import job_owner


def test_job_owner_is_stable_per_run() -> None:
    identity = "rotation-org_one-run_one"
    assert job_owner(identity) == "worker_" + identity
    assert job_owner(identity) == job_owner(identity)
