"""Tool registry with per-role access control.

Two speeds:
  * fast tools run inline (bounded by `timeout`) and their result goes straight back to the
    model, which then speaks;
  * background tools (`background=True`) return "started" immediately so the conversation
    keeps flowing, and their result is injected later when the caller can hear it.

Roles: "owner" (a verified owner number), "guest" (anyone else) and "mission" (the callee
leg of an outbound call the agent placed). The model only ever *sees* the tools of the
current role, and `get()` re-checks the role, so a hallucinated tool name can't escalate.

`confirm=True` marks a tool that acts on someone's behalf (texting, calling, paying). The
call session then treats the first call as a proposal and runs it only when the model
repeats it, with identical arguments, after the caller has answered the read-back. That
check lives in code, so a model talked into something by a third party can't skip it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Settings
    from .missions import Mission
    from .store import CallStore

Handler = Callable[..., Awaitable[Any]]
EMPTY_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class ToolResult:
    """Return this from a handler to control whether the model should respond afterwards."""
    output: Any
    respond: bool = True


@dataclass
class ToolContext:
    call_sid: str
    caller: str
    role: str
    settings: Settings
    store: CallStore
    services: dict[str, Any]
    hangup: Callable[[], None]
    mission: Mission | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    roles: frozenset[str]
    background: bool = False
    timeout: float = 10.0
    label: Callable[[dict[str, Any]], str] | None = field(default=None, compare=False)
    confirm: bool = False

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters or EMPTY_PARAMETERS}

    def describe(self, args: dict[str, Any]) -> str:
        if self.label is None:
            return self.name
        try:
            return self.label(args)
        except Exception:  # a bad label must never break a call
            return self.name


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def tool(self, *, description: str, parameters: dict[str, Any] | None = None,
             roles: Iterable[str] = ("owner",), background: bool = False,
             timeout: float = 10.0, label: Callable[[dict[str, Any]], str] | None = None,
             name: str | None = None, confirm: bool = False) -> Callable[[Handler], Handler]:
        def register(fn: Handler) -> Handler:
            self.add(Tool(name=name or fn.__name__, description=description,
                          parameters=parameters or dict(EMPTY_PARAMETERS), handler=fn,
                          roles=frozenset(roles), background=background, timeout=timeout,
                          label=label, confirm=confirm))
            return fn
        return register

    def for_role(self, role: str) -> list[Tool]:
        return [t for t in self._tools.values() if role in t.roles]

    def names(self, role: str) -> list[str]:
        return [t.name for t in self.for_role(role)]

    def schemas(self, role: str) -> list[dict[str, Any]]:
        return [t.schema() for t in self.for_role(role)]

    def get(self, name: str, role: str) -> Tool | None:
        tool = self._tools.get(name)
        return tool if tool is not None and role in tool.roles else None
