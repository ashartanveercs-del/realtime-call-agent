from callagent.prompts import build_instructions


def test_owner_prompt_names_the_owner_and_explains_background_work():
    text = build_instructions("owner", agent_name="Robin", owner_name="Sam")
    assert "Sam" in text
    assert "background" in text.lower()


def test_guest_prompt_takes_messages_and_protects_the_owner():
    text = build_instructions("guest", agent_name="Robin", owner_name="Sam",
                              caller="+15550002222")
    lower = text.lower()
    assert "message" in lower
    assert "never share" in lower
    assert "only after the tool" in lower


def test_mission_prompt_states_the_goal_and_discloses_an_ai_caller():
    text = build_instructions("mission", agent_name="Robin", owner_name="Sam",
                              goal="Confirm Friday's delivery window.")
    assert "Confirm Friday's delivery window." in text
    assert "AI assistant" in text
    assert "Sam" in text
    assert "end_call" in text


def test_every_prompt_uses_phone_style():
    for role in ("owner", "guest", "mission"):
        text = build_instructions(role, agent_name="Robin", owner_name="Sam", goal="g")
        assert "phone" in text.lower()
        assert "no lists" in text.lower()
