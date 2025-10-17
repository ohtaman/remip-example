"""Agent-building logic for the remip-example application."""

import os

from google.adk.agents import Agent, LoopAgent
from google.adk.planners import BuiltInPlanner
from google.adk.tools import ToolContext, exit_loop
from google.genai import types

from remip_example.config import (
    MENTOR_AGENT_INSTRUCTION,
    REMIP_AGENT_INSTRUCTION,
    REMIP_AGENT_MODEL,
)
from remip_example.services import get_mcp_toolset


def build_agent(
    is_agent_mode: bool = True,
    max_iterations: int = 10,
    thinking_budget: int = 2048,
    api_key: str | None = None,
) -> Agent:
    """Builds the appropriate agent based on the selected mode."""
    if api_key is not None:
        os.environ["GEMINI_API_KEY"] = api_key

    def ask(tool_context: ToolContext):
        """Ask the user for additional information or confirmation."""
        return exit_loop(tool_context)

    remip_agent = Agent(
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
    )

    if not is_agent_mode:
        return remip_agent

    mentor_agent = Agent(
        name="mentor_agent",
        model="gemini-2.5-flash-latest",
        description="Agent to judge whether to continue",
        instruction=MENTOR_AGENT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[exit_loop, ask],
        output_key="mentor_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[remip_agent, mentor_agent],
        max_iterations=max_iterations,
    )
    return agent
