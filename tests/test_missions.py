from callagent.missions import MissionRegistry


async def test_finishing_a_mission_resolves_its_outcome():
    reg = MissionRegistry()
    mission = reg.create(to="+15550003333", goal="Confirm Friday's delivery window.",
                         requested_by="+15550001111")

    assert reg.get(mission.id) is mission
    assert mission.status == "dialing"
    reg.finish(mission.id, "They confirmed 9-11am.")

    assert mission.status == "completed"
    assert await mission.outcome() == "They confirmed 9-11am."


async def test_finishing_twice_keeps_the_first_outcome():
    reg = MissionRegistry()
    mission = reg.create(to="+15550003333", goal="g", requested_by="api")
    reg.finish(mission.id, "first")
    reg.finish(mission.id, "second")
    assert await mission.outcome() == "first"


async def test_failed_mission_reports_failure():
    reg = MissionRegistry()
    mission = reg.create(to="+15550003333", goal="g", requested_by="api")
    reg.fail(mission.id, "The call could not be placed.")
    assert mission.status == "failed"
    assert await mission.outcome() == "The call could not be placed."


def test_unknown_mission_is_ignored():
    reg = MissionRegistry()
    reg.finish("nope", "x")
    assert reg.get("nope") is None
