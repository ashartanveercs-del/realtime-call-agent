import asyncio

from callagent.jobs import JobManager


def _collector():
    finished = []

    async def on_finish(job):
        finished.append(job)

    return finished, on_finish


async def test_started_job_reports_its_result_through_the_callback():
    finished, on_finish = _collector()
    jobs = JobManager(max_running=2, on_finish=on_finish)

    async def work():
        return "3 flights found"

    status, job = jobs.start("search:flights", "search flights", work)
    assert status == "started"
    await jobs.wait_idle()

    assert [(j.label, j.status, j.result) for j in finished] == [
        ("search flights", "done", "3 flights found")]


async def test_same_request_while_running_is_deduplicated():
    _, on_finish = _collector()
    jobs = JobManager(max_running=3, on_finish=on_finish)
    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return "ok"

    first = jobs.start("k", "label", work)
    second = jobs.start("k", "label", work)

    assert first[0] == "started"
    assert second == ("duplicate", first[1])
    gate.set()
    await jobs.wait_idle()


async def test_capacity_limit_reports_busy():
    _, on_finish = _collector()
    jobs = JobManager(max_running=1, on_finish=on_finish)
    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return "ok"

    assert jobs.start("a", "a", work)[0] == "started"
    assert jobs.start("b", "b", work) == ("busy", None)
    gate.set()
    await jobs.wait_idle()


async def test_failed_job_reports_a_generic_failure_not_the_exception():
    finished, on_finish = _collector()
    jobs = JobManager(max_running=1, on_finish=on_finish)

    async def work():
        raise RuntimeError("db password=hunter2 at /srv/internal/path")

    jobs.start("k", "check inbox", work)
    await jobs.wait_idle()

    job = finished[0]
    assert job.status == "failed"
    assert "hunter2" not in job.result
    assert "/srv" not in job.result


async def test_cancel_all_stops_running_jobs_without_announcing_them():
    finished, on_finish = _collector()
    jobs = JobManager(max_running=2, on_finish=on_finish)

    async def work():
        await asyncio.sleep(60)
        return "never"

    jobs.start("k", "slow", work)
    await jobs.cancel_all()

    assert finished == []
    assert jobs.running == []


async def test_board_lists_running_jobs():
    _, on_finish = _collector()
    jobs = JobManager(max_running=2, on_finish=on_finish)
    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return "ok"

    jobs.start("k", "book a table", work)
    assert [j.label for j in jobs.running] == ["book a table"]
    gate.set()
    await jobs.wait_idle()
    assert jobs.running == []
