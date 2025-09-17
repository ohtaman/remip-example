import os
import uuid
import asyncio
from typing import Optional, List

import dotenv
import streamlit as st

from google.genai import types
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, BaseSessionService
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from remip_sample.config import (
    APP_NAME,
    DEFAULT_INSTRUCTION,
    NORMAL_MAX_CALLS,
    AUTON_MAX_CALLS,
    SESSION_DB_URL,
)
from remip_sample.utils import start_remip_mcp, finalize_response

dotenv.load_dotenv(override=True)


@st.cache_resource
def get_session_service() -> BaseSessionService:
    """Create and cache a singleton instance of the session service."""
    return DatabaseSessionService(SESSION_DB_URL)

@st.cache_resource
def get_mcp_toolset() -> McpToolset:
    """Start the MCP server and create and cache a singleton instance of the toolset."""
    port = start_remip_mcp()
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://localhost:{port}/mcp/",
            timeout=30,
            terminate_on_close=False,
        )
    )


def build_agent(api_key: Optional[str], autonomous: bool, max_iterations_hint: int) -> Agent:
    """Builds the agent with the specified configuration."""
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    instruction_autonomous = (
        "You are a decisive, autonomous agent. Do NOT ask for confirmation unless safety is at risk. "
        "Plan briefly, then act with small, reversible steps. If an attempt fails, analyze the error and try a fix. "
        f"Stop when the objective is satisfied and output '<DONE/>' at the end. "
        f"You may take up to roughly {max_iterations_hint} steps if necessary."
    )

    return Agent(
        name="remip_agent",
        model="gemini-2.5-pro",
        description="Agent for mathematical optimization",
        instruction=instruction_autonomous if autonomous else DEFAULT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[get_mcp_toolset()],
    )

def render_past_transcript(adk_session: object) -> None:
    """Renders the past events from a session into the chat history."""
    if not adk_session:
        return
    for event in adk_session.events:
        if not event.content:
            continue
        role = event.content.role
        parts = getattr(event.content, "parts", None) or []
        visible_chunks: List[str] = []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                visible_chunks.append(text)
        if visible_chunks:
            with st.chat_message(role):
                st.markdown("\n\n".join(visible_chunks))


@st.dialog("GEMINI API KEY")
def get_api_key():
    return st.text_input("Gemini API Key", type="password")


async def main() -> None:
    """The main entry point for the Streamlit application."""
    st.set_page_config(page_title="remip-sample", layout="wide")

    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or get_api_key()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())


    with st.sidebar:
        with st.form("settings",  border=False):
            with st.expander("Settings", expanded=True):
                language = st.selectbox("Language", ["English", "Chinese", "Japanese"])
                autonomous_mode = st.toggle("Agent mode", value=False)
                if st.form_submit_button("New Session", use_container_width=True):
                    st.session_state.session_id = str(uuid.uuid4())
                    await get_session_service().create_session(
                        app_name=APP_NAME,
                        user_id=st.session_state.user_id,
                        session_id=st.session_state.session_id,
                    )
                    st.session_state.agent = build_agent(
                        api_key=st.session_state.api_key,
                        autonomous=autonomous_mode,
                        max_iterations_hint=1000, # AUTON_MAX_CALLS if autonomous_mode else NORMAL_MAX_CALLS,
                    )
                    st.rerun()

    if "session_id" not in st.session_state:
        st.info("Click **New Session** in the sidebar to start.")
        st.stop()

    # --- Main chat interface ---
    adk_session = await get_session_service().get_session(
        app_name=APP_NAME,
        user_id=st.session_state.user_id,
        session_id=st.session_state.session_id,
    )
    render_past_transcript(adk_session)

    agent = st.session_state.agent
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=get_session_service())

    if prompt := st.chat_input("Type your message"):
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            answer_placeholder = st.empty()
            full_response = ""
            content = types.Content(role="user", parts=[types.Part(text=prompt)])

            async for event in runner.run_async(
                user_id=adk_session.user_id,
                session_id=adk_session.id,
                new_message=content,
                run_config=RunConfig(
                    streaming_mode=StreamingMode.SSE,
                    max_llm_calls=NORMAL_MAX_CALLS,
                ),
            ):
                if not event.content:
                    continue
                for part in event.content.parts:
                    if text := getattr(part, "text", None):
                        if not getattr(part, "thought", False):
                            full_response += text
                            answer_placeholder.markdown(finalize_response(full_response) + " ▌")
            
            answer_placeholder.markdown(finalize_response(full_response))

if __name__ == "__main__":
    asyncio.run(main())
