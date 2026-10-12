import pytest

from callagent.tools import Tool, ToolRegistry


async def _noop(ctx, **kwargs):
    return "ok"


def test_schema_matches_realtime_function_format():
    tool = Tool(name="lookup", description="Look something up.",
                parameters={"type": "object", "properties": {"q": {"type": "string"}},
                            "required": ["q"]},
                handler=_noop, roles=frozenset({"owner"}))
    assert tool.schema() == {
        "type": "function", "name": "lookup", "description": "Look something up.",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                       "required": ["q"]},
    }


def test_tools_are_filtered_by_caller_role():
    reg = ToolRegistry()
    reg.add(Tool("owner_only", "d", {}, _noop, frozenset({"owner"})))
    reg.add(Tool("everyone", "d", {}, _noop, frozenset({"owner", "guest", "mission"})))

    assert reg.names("owner") == ["owner_only", "everyone"]
    assert reg.names("guest") == ["everyone"]
    assert [s["name"] for s in reg.schemas("guest")] == ["everyone"]


def test_get_refuses_a_tool_outside_the_callers_role():
    reg = ToolRegistry()
    reg.add(Tool("send_sms", "d", {}, _noop, frozenset({"owner"})))

    assert reg.get("send_sms", "owner") is not None
    assert reg.get("send_sms", "guest") is None
    assert reg.get("does_not_exist", "owner") is None


def test_duplicate_tool_names_are_rejected():
    reg = ToolRegistry()
    reg.add(Tool("x", "d", {}, _noop, frozenset({"owner"})))
    with pytest.raises(ValueError):
        reg.add(Tool("x", "d", {}, _noop, frozenset({"owner"})))


def test_decorator_registers_handler_with_sensible_defaults():
    reg = ToolRegistry()

    @reg.tool(description="Tell the time.", roles=("owner", "guest"))
    async def current_time(ctx):
        return "noon"

    tool = reg.get("current_time", "guest")
    assert tool is not None
    assert tool.parameters == {"type": "object", "properties": {}}
    assert tool.background is False
    assert tool.describe({}) == "current_time"


def test_label_function_describes_a_job_for_progress_updates():
    reg = ToolRegistry()

    @reg.tool(description="Research.", background=True,
              label=lambda args: f"research: {args['topic']}")
    async def research(ctx, topic):
        return "done"

    assert reg.get("research", "owner").describe({"topic": "flights"}) == "research: flights"
