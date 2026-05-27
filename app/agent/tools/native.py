from __future__ import annotations

from typing import Annotated, Any

from langgraph.prebuilt.tool_node import InjectedState

RuntimeContext = Annotated[dict[str, Any], InjectedState("runtime_context")]
