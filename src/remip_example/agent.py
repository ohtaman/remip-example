"""Agent-building logic for the remip-example application."""

import os
from collections.abc import Mapping
from typing import Any

from google.adk.agents import Agent, LlmAgent, LoopAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.planners import BuiltInPlanner
from google.adk.tools import BaseTool, ToolContext, exit_loop
from google.genai import types
from mcp.types import CallToolResult

import streamlit as st

from remip_example.config import (
    MENTOR_AGENT_INSTRUCTION,
    REMIP_AGENT_INSTRUCTION,
    REMIP_AGENT_MODEL,
)
from remip_example.utils import get_mcp_toolset


def clear_tool_calling_track(callback_context: CallbackContext) -> None:
    callback_context.state["tools_used"] = []


def track_tool_calling(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: CallToolResult | Mapping[str, Any] | None,
) -> None:
    if "tools_used" not in tool_context.state:
        tool_context.state["tools_used"] = []

    truncated_args: dict[str, str] = {}
    for k, v in args.items():
        value = str(v)
        truncated_args[k] = value[:128] + "..." if len(value) > 128 else value

    success = True
    if tool_response is None:
        success = True
    elif isinstance(tool_response, Mapping):
        if "isError" in tool_response:
            success = not bool(tool_response.get("isError", False))
        elif tool_response.get("error") or tool_response.get("error_message"):
            success = False
        else:
            success = True
    else:
        success = not bool(getattr(tool_response, "isError", False))

    tool_context.state["tools_used"].append(
        {
            "agent": tool_context.agent_name,
            "tool": tool.name,
            "args": truncated_args,
            "success": success,
        }
    )


@st.cache_resource
def build_agent(
    is_agent_mode: bool = True,
    max_iterations: int = 50,
    thinking_budget: int = 2048,
    api_key: str | None = None,
) -> Agent:
    """Builds the appropriate agent based on the selected mode."""
    if api_key is not None:
        os.environ["GEMINI_API_KEY"] = api_key

    def ask(tool_context: ToolContext):
        """Ask the user for additional information or confirmation."""
        return exit_loop(tool_context)

    remip_agent = LlmAgent(
        name="remip_agent",
        model=REMIP_AGENT_MODEL,
        description="Agent for mathematical optimization",
        instruction=REMIP_AGENT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=thinking_budget,
            )
        ),
        tools=[get_mcp_toolset()],
        output_key="work_result",
        before_agent_callback=clear_tool_calling_track,
        after_tool_callback=track_tool_calling,
    )

    if not is_agent_mode:
        return remip_agent

    mentor_agent = LlmAgent(
        name="mentor_agent",
        model=REMIP_AGENT_MODEL,
        description="Agent to judge whether to continue",
        instruction=MENTOR_AGENT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[exit_loop, ask],  # , get_mcp_toolset()],
        output_key="mentor_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[remip_agent, mentor_agent],
        max_iterations=max_iterations,
    )
    print("BUILD_AGENT", id(remip_agent.tools[0]), id(remip_agent), id(mentor_agent))
    return agent
