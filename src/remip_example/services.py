"""Service-related functions for session and tool management."""

import asyncio
from dataclasses import dataclass

import streamlit as st
from google.adk.agents import Agent
from google.adk.sessions import BaseSessionService, DatabaseSessionService, Session
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from remip_example.config import APP_NAME, SESSION_DB_URL
from remip_example.utils import start_remip_mcp


@dataclass
class TalkSessionInfo:
    """A simple dataclass to hold session information in st.session_state."""

    session_id: str
    user_id: str
    user_request: str | None = None
    agent_mode: bool | None = None
    agent: Agent | None = None


@st.cache_resource
def get_session_service() -> BaseSessionService:
    """Initializes and returns a cached database session service."""
    return DatabaseSessionService(SESSION_DB_URL)


@st.cache_resource
def get_mcp_toolset() -> McpToolset:
    """Starts the MCP server and returns a cached toolset instance."""
    port = start_remip_mcp()
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://localhost:{port}/mcp/",
            timeout=30,
            terminate_on_close=True,
        ),
    )


def get_talk_session() -> Session | None:
    """Retrieves the current talk session from the database if it exists."""
    if "talk_session_info" in st.session_state:
        talk_session_info = st.session_state.talk_session_info
        return asyncio.run(
            get_session_service().get_session(
                app_name=APP_NAME,
                user_id=talk_session_info.user_id,
                session_id=talk_session_info.session_id,
            )
        )
    return None


def clear_talk_session():
    """Clears the current talk session from the database and session state."""
    if "talk_session_info" in st.session_state:
        # Stop any running agent task before deleting the session
        st.session_state.async_bridge.stop_task()

        talk_session_info = st.session_state.talk_session_info
        asyncio.run(
            get_session_service().delete_session(
                app_name=APP_NAME,
                user_id=talk_session_info.user_id,
                session_id=talk_session_info.session_id,
            )
        )
        del st.session_state.talk_session_info


def create_talk_session(
    user_id: str, session_id: str, state: dict | None = None
) -> Session:
    """Creates a new talk session and stores its info in the session state."""
    state = state or {}

    talk_session = asyncio.run(
        get_session_service().create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state=state,
        )
    )
    st.session_state.talk_session_info = TalkSessionInfo(
        user_id=user_id,
        session_id=session_id,
    )
    return talk_session
