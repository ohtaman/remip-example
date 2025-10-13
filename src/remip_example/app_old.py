# app.py
import os
import pathlib
import uuid
import asyncio
import threading
import queue
import time
from typing import Optional, Tuple, Dict, Any

import dotenv
import streamlit as st

from google.adk.events.event import Event
from google.genai import types
from google.genai import errors as google_errors
from google.adk.agents import Agent, LoopAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, BaseSessionService
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools import ToolContext, exit_loop

from remip_example.utils import start_remip_mcp, ensure_node
from remip_example.config import (
    APP_NAME,
    REMIP_AGENT_INSTRUCTION,
    NORMAL_MAX_CALLS,
    SESSION_DB_URL,
    EXAMPLES_DIR,
)

dotenv.load_dotenv(override=True)

# =========================
# Per-Streamlit-session async worker
# =========================

class AsyncSession:
    """Background asyncio loop + queue per Streamlit session."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.out_queue: "queue.SimpleQueue[Dict[str, Any]]" = queue.SimpleQueue()
        self.futures = set()
        self.closed = False

    def submit(self, coro) -> "asyncio.Future":
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        self.futures.add(fut)
        fut.add_done_callback(lambda _: self.futures.discard(fut))
        return fut

    def cancel_all(self):
        for f in list(self.futures):
            f.cancel()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_all()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)


@st.cache_resource
def get_async_session(session_id: str) -> AsyncSession:
    return AsyncSession()


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
            terminate_on_close=False,
        ),
    )

# ==============
# Agent
# ==============

def build_agent(
    api_key: Optional[str],
    is_agentic: bool = True,
    max_iterations: int = 10
) -> Agent:
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    def ask(tool_context: ToolContext):
        return exit_loop(tool_context)

    remip_agent = Agent(
        name="remip",
        model="gemini-2.5-pro",
        description="Agent for mathematical optimization",
        instruction=(
            "You are a Methematical Optimization Professional. You interact with user and provide "
            "solutions using methematical optimization.\n\n## Best Practice:\n"
        ) + REMIP_AGENT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024 * 5,
            )
        ),
        tools=[get_mcp_toolset()],
        output_key="work_result",
    )

    if not is_agentic:
        return remip_agent

    judge_agent = Agent(
        name="judge",
        model="gemini-2.5-flash",
        description="Agent to judge whether to continue",
        instruction=f"""## Your Task

Check the response of remip_agent and judge whether to continue.

IF the response of remip_agent is just confirming to continue:
  Tell remip_agent to continue
ELSE IF the response of remip_agent is asking to the user:
  IF it is really necessary to ask something to the user:
    Call ask tool
  ELSE
    Tell remip_agent to continue without asking anything to the user
ELSE IF the user request is not related to the mathematical optimization:
  Tell remip_agent to continue
ELSE IF the response of remip_agent satisfies the user's request:
  Tell remip_agent to continue
ELSE IF you need to ask something to the user:
  Call ask tool
ELSE
  Provide specific suggestions to the remip_agent concisely. 

**!!IMPORTANT!!** YOU CAN NOT USE ANY TOOLS EXCEPT exit OR ask TOOL.

## User Input
{{{{user_input?}}}}

## Response
{{{{work_result?}}}}
""",
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
        sub_agents=[remip_agent, judge_agent],
        max_iterations=max_iterations,
    )
    return agent

# =====================
# Event -> markdown
# =====================

def process_event(event: Event) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (author, response_md, thoughts_md)."""
    response = ""
    thoughts = ""

    if event.content is None:
        return None, None, None

    for part in event.content.parts:
        markdown = getattr(part, "text") or ""
        tool_call = getattr(part, "function_call")
        tool_response = getattr(part, "function_response")

        if tool_call is not None and tool_call.name not in ("ask", "exit_loop"):
            markdown += (
                f"\n\n<details>\n<summary>Call {tool_call.name}</summary><code>\n\n"
                f"{tool_call}\n\n</code></details>\n\n"
            )
        if tool_response is not None and tool_response.name not in ("ask", "exit_loop"):
            markdown += (
                f"\n\n<details>\n<summary>Response {tool_response.name}</summary><code>\n\n"
                f"{tool_response}\n\n</code></details>\n\n"
            )
        if getattr(part, "thought", False):
            thoughts += markdown
        elif markdown:
            response += markdown

    return event.author, (response or None), (thoughts or None)

# ===========================
# Background stream (no Streamlit APIs here!)
# ===========================

async def worker_stream(
    sess: AsyncSession,
    session_service: BaseSessionService,  # <-- resolved on main thread
    app_name: str,
    agent: Agent,
    talk_session,
    prompt: str
):
    """Run the async stream; push chunks into sess.out_queue."""
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

    try:
        await asyncio.sleep(0)  # cooperative start
        async for event in runner.run_async(
            user_id=talk_session.user_id,
            session_id=talk_session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=NORMAL_MAX_CALLS),
        ):
            author, resp, thoughts = process_event(event)
            if resp:
                sess.out_queue.put({"author": author, "markdown": resp})
            if thoughts:
                sess.out_queue.put({"author": author, "markdown": f"**Thoughts:**\n{thoughts}"})
            await asyncio.sleep(0)

    except google_errors.ServerError as e:
        sess.out_queue.put({"author": "system", "markdown": f":warning: Server error: {e}"})
    except asyncio.CancelledError:
        # If runner has an explicit close(), you can await it here.
        raise
    except Exception as e:
        sess.out_queue.put({"author": "system", "markdown": f":x: Unexpected error: {e}"})
    finally:
        # Signal completion if you want (optional)
        sess.out_queue.put({"author": "system", "done": True})

# =============
# UI helpers
# =============

def drain_outbox_and_render(sess: AsyncSession, chat_container) -> bool:
    """
    Move pending worker messages into chat_log and render all.
    Returns True if new messages arrived.
    """
    changed = False
    while True:
        try:
            msg = sess.out_queue.get_nowait()
        except queue.Empty:
            break
        if msg.get("done"):
            st.session_state.stream_done = True
        else:
            st.session_state.chat_log.append({
                "author": msg.get("author") or "assistant",
                "markdown": msg.get("markdown") or "",
            })
            changed = True

    with chat_container:
        for item in st.session_state.chat_log:
            with st.chat_message(item["author"]):
                st.markdown(item["markdown"], unsafe_allow_html=True)

    return changed

def schedule_autorerun_if_streaming():
    """Trigger throttled reruns while a stream is active."""
    fut = st.session_state.get("current_future")
    if fut and not fut.done():
        now = time.monotonic()
        if now - st.session_state.get("last_rerun_at", 0.0) >= 0.1:  # ~10 fps
            st.session_state.last_rerun_at = now
            st.rerun()

def submit_stream(sess: AsyncSession, session_service: BaseSessionService, app_name: str, agent: Agent, talk_session, prompt: str):
    """Start streaming and keep its Future in session_state."""
    fut0 = st.session_state.get("current_future")
    if fut0 and not fut0.done():
        fut0.cancel()

    fut = sess.submit(worker_stream(sess, session_service, app_name, agent, talk_session, prompt))
    st.session_state.current_future = fut
    st.session_state.last_rerun_at = 0.0
    st.session_state.stream_done = False

def stop_stream():
    fut = st.session_state.get("current_future")
    if fut and not fut.done():
        fut.cancel()
        st.toast("Stopped current task.", icon="🛑")

# =======================
# Sidebar / simple UI
# =======================

@st.dialog("GEMINI API KEY")
def get_api_key_dialog():
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Submit"):
        st.session_state.api_key = api_key
        st.rerun()

@st.cache_resource
def load_examples(language: str = "ja"):
    examples_dir: pathlib.Path = pathlib.Path(EXAMPLES_DIR) / language
    examples = {}
    for path in examples_dir.glob("*.md"):
        contents = open(path).readlines()
        if contents:
            examples[contents[0]] = "".join(contents)
    return examples

def render_sidebar(is_in_talk_session: bool):
    with st.sidebar:
        language = st.selectbox(
            "Language",
            [("English", "en"), ("Japanese", "ja")],
            format_func=lambda m: m[0],
            disabled=is_in_talk_session,
        )
        examples = load_examples(language[1])
        example = st.selectbox(
            "Use Examples",
            options=["Do not use example"] + list(examples.keys()),
            disabled=is_in_talk_session,
        )

        col1, col2 = st.columns(2)
        with col1:
            if is_in_talk_session and st.button("New Session", use_container_width=True):
                try:
                    get_async_session(st.session_state.session_id).close()
                except Exception:
                    pass
                st.session_state.pop("session_id", None)
                st.session_state.pop("current_future", None)
                st.session_state.pop("chat_log", None)
                st.rerun()
        with col2:
            if st.button("Stop", use_container_width=True):
                stop_stream()

    return example, examples

# =======================
# Views
# =======================

def render_new_session_view(example: str, examples: dict) -> None:
    with st.container():
        user_request = st.text_area(
            label="Input your request", value=examples.get(example, ""), height=280
        )
        if st.button("Submit", disabled=(not user_request.strip()), use_container_width=True):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.user_request = user_request
            user_id_val = st.session_state.get("user_id") or str(uuid.uuid4())
            st.session_state.user_id = user_id_val

            sess = get_async_session(st.session_state.session_id)
            svc = get_session_service()  # resolve on main thread

            async def _create_session_with(svc_, app_name: str, user_id: str, session_id: str, state: dict):
                return await svc_.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    state=state,
                )

            fut = sess.submit(_create_session_with(
                svc, APP_NAME, user_id_val, st.session_state.session_id, {"user_request": user_request}
            ))
            _ = fut.result(timeout=10)

            st.session_state.agent = build_agent(
                api_key=st.session_state.api_key, max_iterations=10
            )
            st.rerun()
    st.stop()

def render_chat_interface(sess: AsyncSession, session_service: BaseSessionService, talk_session) -> None:
    user_request_container = st.container(height=120)
    chat_container = st.container()

    user_request = talk_session.state.get("user_request", st.session_state.get("user_request", ""))

    with user_request_container:
        st.markdown(user_request or "")

    # Past events (non-final) once — optional; keep simple
    with chat_container:
        author = None
        for event in talk_session.events[1:]:
            a, response, _ = process_event(event)
            if response and not event.is_final_response():
                if author != a:
                    author = a
                    container = st.chat_message(author)
                container.markdown(response, unsafe_allow_html=True)

    # New input
    prompt = st.chat_input("Type your message")
    if prompt:
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)
        agent = st.session_state.agent
        submit_stream(sess, session_service, APP_NAME, agent, talk_session, prompt)

    # Drain + render everything accumulated so far
    changed = drain_outbox_and_render(sess, chat_container)
    # Keep UI ticking while stream is alive
    schedule_autorerun_if_streaming()

def render_app() -> None:
    st.set_page_config(page_title="remip-example", layout="wide")

    # Persistent render state for streaming
    if "chat_log" not in st.session_state:
        st.session_state.chat_log = []
    if "last_rerun_at" not in st.session_state:
        st.session_state.last_rerun_at = 0.0
    if "stream_done" not in st.session_state:
        st.session_state.stream_done = True

    # Init
    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or get_api_key_dialog()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    import platform
    if platform.processor() == "":
        NODE_BIN_DIR = ensure_node()
        if str(NODE_BIN_DIR) not in os.environ["PATH"]:
            os.environ["PATH"] = os.pathsep.join(
                (str(NODE_BIN_DIR), os.environ.get("PATH", ""))
            )

    is_in_talk_session = "session_id" in st.session_state
    example, examples = render_sidebar(is_in_talk_session)

    # Per-session async worker
    sess = get_async_session(st.session_state.get("session_id", "global"))

    if not is_in_talk_session:
        render_new_session_view(example, examples)
    else:
        user_id_val = st.session_state.user_id
        session_id_val = st.session_state.session_id

        svc = get_session_service()  # resolve on main thread

        async def _load_session_with(svc_, app_name: str, user_id: str, session_id: str):
            return await svc_.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        fut = sess.submit(_load_session_with(svc, APP_NAME, user_id_val, session_id_val))
        talk_session = fut.result(timeout=10)

        if talk_session is None:
            async def _create_then_load_with(svc_, app_name: str, user_id: str, session_id: str, state: dict):
                await svc_.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    state=state,
                )
                return await svc_.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
            fut2 = sess.submit(_create_then_load_with(
                svc, APP_NAME, user_id_val, session_id_val, {"user_request": st.session_state.get("user_request", "")}
            ))
            talk_session = fut2.result(timeout=10)

        if talk_session is None:
            st.error("Failed to initialize talk session. Please try again.")
            st.stop()

        # First visit after creation: kick off with the initial request, if any
        if len(talk_session.events) == 0 and st.session_state.get("user_request"):
            agent = st.session_state.agent
            submit_stream(sess, svc, APP_NAME, agent, talk_session, st.session_state.user_request)

        render_chat_interface(sess, svc, talk_session)

render_app()
