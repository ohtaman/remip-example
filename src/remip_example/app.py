from dataclasses import dataclass
import os
import pathlib
import threading
import queue
from typing import Any, Generator, Iterable, Callable, Awaitable, AsyncIterator
import uuid
import json

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
        self.q = queue.Queue()
        self._command_q = queue.Queue()
        self.thread = threading.Thread(target=self._runner, daemon=True)
        self.thread.start()

    def _runner(self):
        """The main entry point for the background thread."""
        try:
            asyncio.run(self._producer_loop())
        except Exception:
            # In a real app, you'd want to log this.
            pass

    async def _producer_loop(self):
        """The core async event loop for the background thread."""
        agent_task = None
        while True:
            try:
                # Check for commands without blocking the event loop.
                command, data = self._command_q.get_nowait()

                # If there's an existing agent task, cancel it gracefully.
                if agent_task:
                    agent_task.cancel()
                    try:
                        await agent_task
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass # Expected cancellation
                
                if command == 'START':
                    factory = data
                    # Start the new agent task.
                    agent_task = asyncio.create_task(self._run_agent_task(factory))
                elif command == 'STOP':
                    # Task is already stopped, just clear the queue and signal completion.
                    while not self.q.empty():
                        self.q.get_nowait()
                    self.q.put(self.SENTINEL)
                    agent_task = None
                elif command == 'TERMINATE':
                    break # Exit the producer loop

            except queue.Empty:
                # No command, just let the event loop sleep briefly.
                await asyncio.sleep(0.01)

    async def _run_agent_task(self, factory):
        """Runs the agent's async iterator and puts results on the response queue."""
        try:
            async_iterable = await factory()
            async for item in async_iterable:
                self.q.put(item)
        except (asyncio.CancelledError, StopAsyncIteration):
            # This is a normal, graceful exit.
            pass
        except Exception:
            # In a real app, you'd want to log this.
            pass
        finally:
            # Signal that this specific task is done.
            self.q.put(self.SENTINEL)

    def start_task(self, factory: Callable[[], Awaitable[AsyncIterator[Any]]]):
        """Public method for the UI thread to start a new agent task."""
        # Clear any stale items from the queue before starting a new task.
        while not self.q.empty():
            self.q.get_nowait()
        self._command_q.put(('START', factory))

    def stop_task(self):
        """Public method for the UI thread to stop the current agent task."""
        self._command_q.put(('STOP', None))

    def __iter__(self) -> Generator[Any, Any, None]:
        """Allows the UI thread to iterate over results from the response queue."""
        while True:
            item = self.q.get()
            if item is self.SENTINEL:
                break
            yield item
    
    def __del__(self):
        """Ensure the background thread is terminated when the bridge is garbage collected."""
        self._command_q.put(('TERMINATE', None))



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


def process_event(event: Event) -> tuple[str | None, str | None, str | None]:
    author = event.author
    if event.content and event.content.role:
        author = event.content.role

    if event.content is None:
        return author, None, None

    response_markdown = ""
    thoughts_markdown = ""

    for part in event.content.parts:
        if part.thought:
            thoughts_markdown += part.text
        elif part.function_call:
            tool_name = part.function_call.name
            tool_args = json.dumps(part.function_call.args, indent=2, ensure_ascii=False)
            response_markdown += (
                f'<details><summary>Tool Call: {tool_name}</summary>\n\n'
                f'```json\n{tool_args}\n```\n\n</details>'
            )
        elif part.function_response:
            tool_name = part.function_response.name
            try:
                tool_response = json.dumps(part.function_response.response, indent=2, ensure_ascii=False)
                response_markdown += (
                    f'<details><summary>Tool Response: {tool_name}</summary>\n\n'
                    f'```json\n{tool_response}\n```\n\n</details>'
                )
            except TypeError:
                # Fallback for non-serializable objects like CallToolResult
                raw_response = part.function_response.response
                extracted_json = None
                if isinstance(raw_response, dict) and 'result' in raw_response:
                    result_obj = raw_response['result']
                    if hasattr(result_obj, 'content') and result_obj.content and hasattr(result_obj.content[0], 'text'):
                        try:
                            # Extract and format the nested JSON string
                            parsed_text = json.loads(result_obj.content[0].text)
                            extracted_json = json.dumps(parsed_text, indent=2, ensure_ascii=False)
                        except (json.JSONDecodeError, IndexError):
                            pass  # Not a valid JSON string, proceed to str() fallback
                
                if extracted_json:
                    tool_response_str = extracted_json
                    lang = "json"
                else:
                    # The ultimate fallback
                    tool_response_str = str(raw_response)
                    lang = ""

                response_markdown += (
                    f'<details><summary>Tool Response: {tool_name}</summary>\n\n'
                    f'```{lang}\n{tool_response_str}\n```\n\n</details>'
                )
        elif part.text:
            response_markdown += part.text
        
    return author, response_markdown or None, thoughts_markdown or None


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
        # Send a command to the background thread to stop the current task.
        st.session_state.async_bridge.stop_task()
        
        talk_session_info = st.session_state.talk_session_info
        asyncio.run(get_session_service().delete_session(
            app_name=APP_NAME,
            user_id=talk_session_info.user_id,
            session_id=talk_session_info.session_id,
        ))
        del(st.session_state.talk_session_info)


def create_talk_session(user_id: str, session_id: str, state: dict|None=None) -> Session:
    state = state or {}

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


def render_messages(events: Iterable[Event]):
    """Renders a list of events, grouping consecutive messages from the same author."""
    last_author = None
    full_response = ""
    full_thoughts = ""
    message_placeholder = None
    thought_placeholder = None

    for event in events:
        author, response, thoughts = process_event(event)

        if not author:
            continue

        if author != last_author:
            # Start a new message block for the new author
            full_response = ""
            full_thoughts = ""
            with st.chat_message(author):
                message_placeholder = st.empty()
                thought_placeholder = st.empty()
            last_author = author

        if response and message_placeholder:
            full_response += response
            message_placeholder.markdown(full_response, unsafe_allow_html=True)
        
        if thoughts and thought_placeholder:
            full_thoughts += thoughts + "\n\n"
            with thought_placeholder.container():
                with st.expander("Show Thoughts"):
                    st.markdown(full_thoughts)


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
        st.rerun()
    
    talk_session = get_talk_session()
    if talk_session is None:
        user_request = new_session_form(example)
        if user_request:
            talk_session = create_talk_session(
                user_id=st.session_state.user_id,
                session_id=str(uuid.uuid4()),
                state={"user_request": user_request}
            )
        else:
            st.stop()
    else:
        user_request = None

    # Display historical events from the session.
    render_messages(talk_session.events)

    user_input = st.chat_input("Input your request") or user_request
    if user_input:
        # Display the user's immediate input
        with st.chat_message("user"):
            st.markdown(user_input)

        agent = build_agent(is_agent_mode=is_agent_mode)
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=get_session_service())

        async def _make_iter() -> AsyncIterator[Event]:
            return runner.run_async(
                user_id=talk_session.user_id,
                session_id=talk_session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=user_input)]),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=NORMAL_MAX_CALLS),
            )

        st.session_state.async_bridge.start_task(_make_iter)
        
        # Stream the live response
        render_messages(st.session_state.async_bridge)
        st.rerun()

def main():
    st.set_page_config(page_title="remip-example", layout="wide")
    init()
    render()


if __name__ == "__main__":
    main()