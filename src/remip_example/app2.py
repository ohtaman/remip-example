from dataclasses import dataclass
import os
import pathlib
import threading
import queue
import time
from typing import Any, Generator, Iterable, Callable, Awaitable, AsyncIterator
import uuid

import asyncio
from google.adk.agents import Agent, LoopAgent, RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.events.event import Event
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, DatabaseSessionService, Session
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools import ToolContext, exit_loop
from google.genai import types
import streamlit as st
from remip_example.utils import start_remip_mcp
from remip_example.config import (
    APP_NAME,
    MENTOR_AGENT_INSTRUCTION,
    REMIP_AGENT_INSTRUCTION,
    NORMAL_MAX_CALLS,
    REMIP_AGENT_MODEL,
    SESSION_DB_URL,
    EXAMPLES_DIR,
)


@dataclass
class TalkSessionInfo:
    session_id: str
    user_id: str
    user_request: str|None = None
    agent_mode: bool|None = None
    agent: Agent|None = None


class AsyncIteratorBridge:
    def __init__(self):
        self.SENTINEL = object()
        self._stop = threading.Event()
        self.thread = None
        self.q = None
        self._factory: Callable[[], Awaitable[AsyncIterator[Any]]] | None = None
        self._aiter: AsyncIterator[Any] | None = None

    async def _produce(self):
        try:
            # Create the async iterator *inside this thread's loop*
            self._aiter = await self._factory()
            async for item in self._aiter:
                if self._stop.is_set():
                    # Graceful close on stop
                    await self._aiter.aclose()
                    break
                self.q.put(item)
        except Exception:
            # TODO: logging
            pass
        finally:
            try:
                # Ensure we close in this loop if not closed yet
                if self._aiter is not None:
                    await self._aiter.aclose()
            except Exception:
                pass
            self.q.put(self.SENTINEL)

    def _runner(self):
        # one loop per thread; creation+consumption happen here
        asyncio.run(self._produce())

    def run(self, factory: Callable[[], Awaitable[AsyncIterator[Any]]]) -> Generator[Any, Any, None]:
        self.stop()
        self._stop.clear()
        self._factory = factory
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._runner, daemon=True)
        self.thread.start()
        try:
            while True:
                item = self.q.get()
                if item is self.SENTINEL:
                    break
                yield item
        finally:
            self.stop()

    def stop(self):
        if self.thread is not None and self.thread.is_alive():
            self._stop.set()
            self.thread.join(timeout=2.0)
        self.thread = None
        self.q = None
        self._factory = None
        self._aiter = None



@st.cache_resource
def get_session_service() -> BaseSessionService:
    return DatabaseSessionService(SESSION_DB_URL)


@st.cache_resource
def get_mcp_toolset() -> McpToolset:
    port = start_remip_mcp()
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://localhost:{port}/mcp/",
            timeout=30,
            terminate_on_close=True,
        ),
    )


@st.cache_resource
def load_examples(language: str = "ja"):
    examples_dir: pathlib.Path = pathlib.Path(EXAMPLES_DIR) / language
    examples = {}
    for path in examples_dir.glob("*.md"):
        contents = open(path).readlines()
        if contents:
            examples[contents[0]] = "".join(contents)
    return examples


def build_agent(
    is_agent_mode: bool = True,
    max_iterations: int = 10,
    thinking_budget: int = 2048,
    api_key: str|None = None,
) -> Agent:
    if api_key is not None:
        os.environ["GEMINI_API_KEY"] = api_key


    def ask(tool_context: ToolContext):
        """Ask the user for additional information or confirmation based on the previous response.

        Use this function whenever you need to request further details or confirmation from the user.
        """
        return exit_loop(tool_context)

    remip_agent = Agent(
        name="remip",
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
        name="judge",
        model="gemini-2.5-flash",
        description="Agent to judge whether to continue",
        instruction=MENTOR_AGENT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[exit_loop, ask],
        output_key="judge_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[remip_agent, mentor_agent],
        max_iterations=max_iterations,
    )
    return agent


def settings_form():
    with st.form("Settings", border=False):
        with st.expander("Settings"):
            language = st.selectbox("Language", ["ja", "en"])
            is_agent_mode = st.toggle("Agent Mode")
            is_submit = st.form_submit_button("Submit", use_container_width=True)

    return language, is_agent_mode, is_submit


@st.dialog("GEMINI API KEY")
def api_key_dialog() -> str|None:
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Submit"):
        return api_key
    else:
        return


def new_session_form(example: str|None):
    with st.form("new_session"):
        user_request = st.text_area(
            label="Input your request", value=example, height=280
        )
        if st.form_submit_button("Submit", use_container_width=True):
            return user_request
        else:
            return


def chat_messages(events: Iterable[Event]):
    for event in events:
        st.write(event)


def get_talk_session() -> Session|None:
    if "talk_session_info" in st.session_state:
        talk_session_info = st.session_state.talk_session_info
        return asyncio.run(get_session_service().get_session(
            app_name=APP_NAME,
            user_id=talk_session_info.user_id,
            session_id=talk_session_info.session_id,
        ))
    return None


def clear_talk_session():
    if "talk_session_info" in st.session_state:
        st.session_state.async_bridge.stop()
        talk_session_info = st.session_state.talk_session_info
        asyncio.run(get_session_service().delete_session(
            app_name=APP_NAME,
            user_id=talk_session_info.user_id,
            session_id=talk_session_info.session_id,
        ))
        del(st.session_state.talk_session_info)


def create_talk_session(user_id: str, session_id: str, state: dict|None=None) -> Session:
    state = state or {}

    clear_talk_session()
    talk_session = asyncio.run(get_session_service().create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state=state,
    ))
    st.session_state.talk_session_info = TalkSessionInfo(
        user_id=user_id,
        session_id=session_id,
    )
    return talk_session


def init():
    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or api_key_dialog()
        if not api_key:
            st.rerun()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    if "async_bridge" not in st.session_state:
        st.session_state.async_bridge = AsyncIteratorBridge()


def render():
    with st.sidebar:
        st.title(APP_NAME)
        pf = st.container()
        st.divider()
        language, is_agent_mode, is_submit = settings_form()
        with pf:
            examples = load_examples(language)
            example_title = st.selectbox("Example Prompt", ["write by hand"] + list(examples.keys()))
            example = examples.get(example_title, "")
            create_new_session = st.button("New Session", use_container_width=True)

    if create_new_session:
        clear_talk_session()
    
    talk_session = get_talk_session()
    if talk_session is None:
        user_request = new_session_form(example)
        if user_request:
            talk_session = create_talk_session(
                user_id=st.session_state.user_id,
                session_id=str(uuid.uuid4()),
                state={
                    "user_request": user_request
                }
            )
        else:
            st.stop()
    else:
        user_request = None


    chat_messages(talk_session.events)

    user_input = st.chat_input("Input your request") or user_request
    agent = build_agent(is_agent_mode=is_agent_mode)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=get_session_service())

    async def _make_iter() -> AsyncIterator[Event]:
        return runner.run_async(
            user_id=talk_session.user_id,
            session_id=talk_session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=user_input)]),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=NORMAL_MAX_CALLS),
        )

    chat_messages(st.session_state.async_bridge.run(_make_iter))


def main():
    st.set_page_config(page_title="remip-example", layout="wide")
    init()
    render()


if __name__ == "__main__":
    main()