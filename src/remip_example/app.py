"""Streamlit LoopAgent application with enhanced tool visibility."""

from __future__ import annotations

import os
import time
import uuid
import json
import queue
import asyncio
import threading
import subprocess
import atexit
import signal
import socket
import html
from collections.abc import Mapping
from typing import Optional, Any, Callable, Dict

import streamlit as st
import logging

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx as _add_ctx
except Exception:
    _add_ctx = None

from google.adk.agents import Agent, LlmAgent, LoopAgent, RunConfig
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.run_config import StreamingMode
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event
from google.adk.tools import BaseTool, ToolContext, exit_loop
from google.genai import types

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from remip_example.chat_history import build_committed_events_from_partials
from remip_example.ui_components import load_examples


APP_NAME = "agents"  # Align with ADK's default app name
USER_ID = "demo-user-001"

AVATARS = {"remip_agent": "🦸", "mentor_agent": "🧚", "user": None}

LOOP_STATE_LABELS = {
    "starting": "Booting loop",
    "handover_wait": "Finishing previous turn",
    "worker": "Agent iterating",
    "mentor": "Mentor reviewing",
    "planner": "Planning next step",
    "running": "Generating answer",
    "completed": "Completed",
    "interrupted": "Interrupted",
    "error": "Error",
}

LOOP_STATUS_CSS = """
<style>
.loop-status {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    margin-bottom: 0.75rem;
    padding: 0.65rem 0.95rem;
    border-radius: 0.85rem;
    border: 1px solid rgba(94, 105, 155, 0.35);
    background: rgba(76, 110, 245, 0.08);
}
.loop-status__spinner,
.loop-status__icon {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}
.loop-status__spinner {
    border: 2.6px solid rgba(112, 131, 190, 0.25);
    border-top-color: #4c6ef5;
    animation: loop-status-spin 0.85s linear infinite;
}
.loop-status__icon {
    font-size: 0.9rem;
    font-weight: 600;
    color: #ffffff;
    background: #4c6ef5;
}
.loop-status__icon--done {
    background: #2b8a3e;
}
.loop-status__icon--error {
    background: #e03131;
}
.loop-status__icon--paused {
    background: #f08c00;
}
.loop-status__body {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    min-width: 0;
}
.loop-status__stage {
    font-weight: 600;
    font-size: 0.95rem;
    line-height: 1.2;
}
.loop-status__meta {
    font-size: 0.78rem;
    opacity: 0.85;
    line-height: 1.3;
}
.loop-status__meta span + span::before {
    content: "·";
    margin: 0 0.35rem;
    opacity: 0.65;
}
@keyframes loop-status-spin {
    to { transform: rotate(360deg); }
}
</style>
"""

# ------------------- Silence teardown noise (cosmetic only) -------------------
logging.getLogger("mcp.client.stdio").setLevel(logging.ERROR)
logging.getLogger("google.adk.tools.mcp_tool.mcp_session_manager").setLevel(
    logging.ERROR
)
logging.getLogger("google_adk.google.adk.tools.base_authenticated_tool").setLevel(
    logging.ERROR
)

logger = logging.getLogger("remip.loop_worker")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
log_level_name = os.getenv("REMIP_LOOP_LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, log_level_name, logging.INFO))
logger.propagate = False


# ------------------- HTTP helper: spawn + cache MCP server (port 3333) --------------------
def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@st.cache_resource
def ensure_http_server(port: int = 3333) -> int:
    """
    Start the MCP server (HTTP mode) once per Streamlit session, and keep it alive
    across reruns. It will be terminated by the atexit handler when the process exits.
    """
    proc = subprocess.Popen(
        [
            "npx",
            "-y",
            "github:ohtaman/remip-mcp",
            "--http",
            "--start-remip-server",
            "--port",
            str(port),
        ],
        start_new_session=True,
    )

    def _cleanup():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
            except Exception:
                pass

    atexit.register(_cleanup)

    if not _wait_for_port("127.0.0.1", port, timeout=10.0):
        raise RuntimeError(f"MCP HTTP server failed to start on port {port}")
    return port


# ----------------------------- MCP Toolset factory ----------------------------------------
def build_mcp_toolset(
    headers_provider: Optional[Callable[[], Dict[str, str]]] = None,
) -> McpToolset:
    """
    Sticky logical session is carried by headers_provider on every call.
    """
    header_provider = None
    if headers_provider is not None:

        def _header_provider(_context):
            headers = headers_provider() or {}
            return headers

        header_provider = _header_provider

    port = ensure_http_server(3333)
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://127.0.0.1:{port}/mcp/",
            timeout=30,
            terminate_on_close=True,  # HTTP session ends when toolset is closed
        ),
        header_provider=header_provider,
    )


# ---------------------- LoopAgent builder (uses long-lived toolset) -----------------------
def clear_tool_calling_track(callback_context: CallbackContext) -> None:
    callback_context.state["tools_used"] = []


def track_tool_calling(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> None:
    logger.debug(
        "Tool call start agent=%s tool=%s args=%s",
        tool_context.agent_name,
        getattr(tool, "name", type(tool).__name__),
        {
            k: (repr(v)[:120] + "..." if len(repr(v)) > 120 else repr(v))
            for k, v in args.items()
        },
    )
    if "tools_used" not in tool_context.state:
        tool_context.state["tools_used"] = []
    truncated_args = {
        k: (str(v)[:128] + "..." if len(str(v)) > 128 else str(v))
        for k, v in args.items()
    }
    tool_context.state["tools_used"].append(
        {
            "agent": tool_context.agent_name,
            "tool": tool.name,
            "args": truncated_args,
            "success": not getattr(tool_response, "isError", False),
        }
    )
    logger.debug(
        "Tool call done agent=%s tool=%s success=%s",
        tool_context.agent_name,
        getattr(tool, "name", type(tool).__name__),
        not getattr(tool_response, "isError", False),
    )


def build_loop_agent(
    toolset: McpToolset,
    model: str | None = None,
    remip_instruction: str | None = None,
    mentor_instruction: str | None = None,
    thinking_budget: int = 2048,
    max_iterations: int = 50,
) -> Agent:
    model = model or os.getenv("REMIP_AGENT_MODEL", "gemini-2.5-flash")
    remip_instruction = remip_instruction or os.getenv(
        "REMIP_AGENT_INSTRUCTION",
        "You are a mathematical optimization and coding assistant. Use tools when helpful. Be precise.",
    )
    mentor_instruction = mentor_instruction or os.getenv(
        "MENTOR_AGENT_INSTRUCTION",
        (
            "You are a mentor/orchestrator. If the last step seems sufficient, call exit_loop. "
            "Otherwise, briefly justify continuing and hand the work back to the remip_agent. "
            "Never execute heavy tools yourself—do not call define_model, solve_model, or other MCP tools. "
            "If further tool work is required, simply explain what still needs to happen."
        ),
    )

    remip_agent = LlmAgent(
        name="remip_agent",
        model=model,
        description="Agent for mathematical optimization and coding",
        instruction=remip_instruction,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True, thinking_budget=thinking_budget
            )
        ),
        tools=[toolset],  # MCP tool access only for the worker agent
        output_key="work_result",
        before_agent_callback=clear_tool_calling_track,
        after_tool_callback=track_tool_calling,
    )

    def ask(tool_context: ToolContext):
        return exit_loop(tool_context)

    mentor_tools: list[BaseTool] = [exit_loop, ask]
    if toolset is not None:
        mentor_tools.append(toolset)

    mentor_agent = LlmAgent(
        name="mentor_agent",
        model=model,
        description="Agent that judges whether to continue the loop",
        instruction=mentor_instruction,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True, thinking_budget=1024
            )
        ),
        tools=mentor_tools,
        output_key="mentor_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[remip_agent, mentor_agent],
        max_iterations=max_iterations,
    )
    return agent


def _summarize_event(event: Event) -> str:
    parts: list[str] = []
    event_type = getattr(event, "event_type", None) or getattr(event, "type", None)
    if event_type:
        parts.append(f"type={event_type}")
    source = getattr(event, "source", None)
    if source:
        parts.append(f"source={source}")
    author = getattr(event, "author", None)
    if author and author != source:
        parts.append(f"author={author}")
    agent_name = getattr(event, "agent_name", None)
    if agent_name and agent_name not in (source, author):
        parts.append(f"agent={agent_name}")
    if hasattr(event, "is_final_response"):
        try:
            parts.append(f"final={event.is_final_response()}")
        except Exception:
            parts.append("final=?")
    invocation_id = getattr(event, "invocation_id", None)
    if invocation_id:
        parts.append(f"invocation_id={invocation_id}")
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in (
            "loop_action",
            "loop_state",
            "iteration",
            "mentor_decision",
            "reason",
            "status",
        ):
            if key in metadata:
                value = metadata[key]
                if isinstance(value, str) and len(value) > 80:
                    value = value[:77] + "..."
                parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else repr(event)


# ------------------------ Worker that owns async lifecycle ---------------------------------
class StreamWorker:
    """
    One thread, one asyncio loop, one long-lived MCP toolset + Runner + Session + LoopAgent.
    Ensures agent create/iterate/close happens in the SAME task lineage (no cross-task close).
    """

    def __init__(
        self, headers_provider: Optional[Callable[[], Dict[str, str]]] = None
    ) -> None:
        self._log = logger
        self._debug_events = logger.isEnabledFor(logging.DEBUG)
        self._log.debug("StreamWorker initializing")
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="MCPWorker"
        )
        if _add_ctx:
            _add_ctx(self._thread)

        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._toolset: Optional[McpToolset] = None
        self._runner: Optional[Runner] = None
        self._session_id: Optional[str] = None
        self._session_service: Optional[InMemorySessionService] = None

        self.out_q: queue.Queue = queue.Queue()

        self._current_task: Optional[asyncio.Task] = None
        self._current_stream_id: int = 0
        self._interrupt_flag: bool = False
        timeout_str = os.getenv("REMIP_INTERRUPT_FLUSH_TIMEOUT", "0").strip()
        try:
            timeout_val = float(timeout_str)
        except ValueError:
            timeout_val = 0.0
        self._handoff_timeout: float | None = timeout_val if timeout_val > 0 else None

        self._headers_provider = headers_provider

        self._thread.start()
        while self._loop is None:
            time.sleep(0.01)
        self._run_coro_blocking(self._init_adk_objects())
        self._log.info("StreamWorker ready (session_id=%s)", self._session_id)
        atexit.register(self.close)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro_blocking(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    async def _init_adk_objects(self):
        self._toolset = build_mcp_toolset(headers_provider=self._headers_provider)
        agent = build_loop_agent(self._toolset)
        svc = InMemorySessionService()
        self._session_service = svc
        self._runner = Runner(app_name=APP_NAME, agent=agent, session_service=svc)
        created = await svc.create_session(app_name=APP_NAME, user_id=USER_ID)
        self._session_id = created.id
        self._log.debug("ADK objects initialized (session_id=%s)", self._session_id)

    def start_stream(self, prompt: str) -> int:
        active_id = self._current_stream_id
        if self._current_task and not self._current_task.done():
            self._log.info("Interrupting active stream %s with new prompt", active_id)
        self._current_stream_id += 1
        stream_id = self._current_stream_id
        previous_task = self._current_task
        previous_stream_id = active_id if active_id != stream_id else None
        self._interrupt_flag = True
        preview = prompt.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        self._log.info("Starting stream %s (prompt='%s')", stream_id, preview)

        def _start():
            task = asyncio.create_task(
                self._stream_task(
                    stream_id,
                    prompt,
                    previous_task=previous_task,
                    previous_stream_id=previous_stream_id,
                ),
                name=f"stream-{stream_id}",
            )
            self._current_task = task

        self._loop.call_soon_threadsafe(_start)
        return stream_id

    def interrupt(self):
        self._interrupt_flag = True
        self._log.info("Interrupt flag set for stream %s", self._current_stream_id)

    def close(self):
        async def _close():
            try:
                self._log.info("Closing StreamWorker")
                if self._current_task and not self._current_task.done():
                    self._interrupt_flag = True
                    await asyncio.sleep(0)
                if self._toolset is not None:
                    await self._toolset.close()
                await asyncio.sleep(0)
                self._log.info("StreamWorker close completed")
            except Exception as exc:
                self._log.exception("Error while closing StreamWorker: %s", exc)

        self._run_coro_blocking(_close())

    def get_history_messages(self) -> list[Event]:
        return self._run_coro_blocking(self._get_history_messages())

    async def _get_history_messages(self) -> list[Event]:
        if not self._session_service or not self._session_id:
            return []

        session = await self._session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=self._session_id,
        )
        events = getattr(session, "events", None) or []
        return list(events)

    async def _stream_task(
        self,
        stream_id: int,
        prompt: str,
        *,
        previous_task: Optional[asyncio.Task] = None,
        previous_stream_id: Optional[int] = None,
    ):
        if previous_task and not previous_task.done():
            self.out_q.put(
                (
                    "status",
                    stream_id,
                    {
                        "state": "handover_wait",
                        "waiting_for": previous_stream_id,
                    },
                )
            )
        await self._await_previous_task(previous_task, previous_stream_id, stream_id)
        assert self._runner is not None and self._session_id is not None
        self._interrupt_flag = False
        self.out_q.put(
            (
                "status",
                stream_id,
                {
                    "state": "starting",
                    "agent": "remip_agent",
                },
            )
        )
        self.out_q.put(("reset", stream_id, None))
        preview = prompt.replace("\n", " ").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."
        self._log.info("Stream %s task started (prompt='%s')", stream_id, preview)

        agen = None
        carry_events: list[Event] = []
        interrupted = False
        try:
            user_msg = types.Content(role="user", parts=[types.Part(text=prompt)])
            agen = self._runner.run_async(
                user_id=USER_ID,
                session_id=self._session_id,
                new_message=user_msg,
                run_config=RunConfig(
                    streaming_mode=StreamingMode.SSE, max_llm_calls=100
                ),
            )
            self._log.debug("Stream %s run_async issued", stream_id)

            async for event in agen:
                if self._debug_events:
                    self._log.debug(
                        "Stream %s event: %s", stream_id, _summarize_event(event)
                    )
                status_payload = self._build_status_payload(event)
                if status_payload:
                    status_payload["state"] = (
                        status_payload.get("state")
                        or status_payload.get("loop_state")
                        or "running"
                    )
                    self.out_q.put(("status", stream_id, status_payload))
                if self._interrupt_flag or stream_id != self._current_stream_id:
                    self._log.info(
                        "Stream %s interrupted (flag=%s, current=%s)",
                        stream_id,
                        self._interrupt_flag,
                        self._current_stream_id,
                    )
                    try:
                        await agen.aclose()
                    except Exception:
                        pass
                    interrupted = True
                    break

                text_chunk, thought_chunk, tool_calls, tool_responses = (
                    extract_event_fragments(event)
                )
                if tool_calls:
                    for call_payload in tool_calls:
                        self.out_q.put(("function_call", stream_id, call_payload))
                if tool_responses:
                    for response_payload in tool_responses:
                        self.out_q.put(
                            ("function_response", stream_id, response_payload)
                        )
                if text_chunk:
                    kind = "chunk"
                    if event.is_final_response():
                        kind = "final_chunk"
                    if self._debug_events:
                        preview = text_chunk.replace("\n", " ").strip()
                        if len(preview) > 120:
                            preview = preview[:117] + "..."
                        self._log.debug(
                            "Stream %s text chunk len=%s preview='%s' final=%s kind=%s",
                            stream_id,
                            len(text_chunk),
                            preview,
                            event.is_final_response(),
                            kind,
                        )
                    self.out_q.put((kind, stream_id, text_chunk))
                if thought_chunk:
                    kind = "thought"
                    if event.is_final_response():
                        kind = "final_thought"
                    self.out_q.put((kind, stream_id, thought_chunk))
                if not event.is_final_response():
                    carry_events.append(event)
                else:
                    inv_id = getattr(event, "invocation_id", None)
                    if inv_id is None:
                        carry_events.clear()
                    else:
                        carry_events = [
                            ev
                            for ev in carry_events
                            if getattr(ev, "invocation_id", None) != inv_id
                        ]
                    self._log.info(
                        "Stream %s final response received: %s",
                        stream_id,
                        _summarize_event(event),
                    )

            if not interrupted:
                self._log.info("Stream %s completed normally", stream_id)
                self.out_q.put(
                    (
                        "status",
                        stream_id,
                        {
                            "state": "completed",
                            "agent": "remip_agent",
                        },
                    )
                )
                self.out_q.put(("done", stream_id, None))

        except Exception as e:
            self._log.exception("Stream %s encountered error: %s", stream_id, e)
            self.out_q.put(
                (
                    "status",
                    stream_id,
                    {
                        "state": "error",
                        "agent": "remip_agent",
                        "error": str(e),
                    },
                )
            )
            self.out_q.put(("error", stream_id, f"{type(e).__name__}: {e}"))
        finally:
            if agen is not None:
                try:
                    await agen.aclose()
                except Exception:
                    pass
            if carry_events:
                self._log.debug(
                    "Stream %s committing %d carry events", stream_id, len(carry_events)
                )
                await self._commit_carry_events(carry_events)
            if interrupted:
                self._log.info("Stream %s marked as interrupted", stream_id)
                self.out_q.put(
                    (
                        "status",
                        stream_id,
                        {
                            "state": "interrupted",
                            "agent": "remip_agent",
                            "waiting_for": self._current_stream_id,
                        },
                    )
                )
                self.out_q.put(("interrupted", stream_id, None))
            current_task = asyncio.current_task()
            if current_task and self._current_task is current_task:
                self._current_task = None

    async def _await_previous_task(
        self,
        previous_task: Optional[asyncio.Task],
        previous_stream_id: Optional[int],
        new_stream_id: int,
    ) -> None:
        if not previous_task or previous_task is asyncio.current_task():
            return

        if previous_task.done():
            try:
                await asyncio.shield(previous_task)
            except Exception:
                pass
            return

        description = (
            f"stream {previous_stream_id}" if previous_stream_id else "previous stream"
        )
        if self._handoff_timeout:
            self._log.debug(
                "Waiting up to %.1fs for %s to finish before starting stream %s",
                self._handoff_timeout,
                description,
                new_stream_id,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(previous_task),
                    timeout=self._handoff_timeout,
                )
            except asyncio.TimeoutError:
                self._log.warning(
                    "%s did not finish within %.1fs; continuing with stream %s",
                    description.capitalize(),
                    self._handoff_timeout,
                    new_stream_id,
                )
            except Exception as exc:
                self._log.exception(
                    "Error while awaiting %s completion before stream %s: %s",
                    description,
                    new_stream_id,
                    exc,
                )
        else:
            self._log.debug(
                "Waiting for %s to finish before starting stream %s",
                description,
                new_stream_id,
            )
            try:
                await asyncio.shield(previous_task)
            except Exception as exc:
                self._log.exception(
                    "Error while awaiting %s completion before stream %s: %s",
                    description,
                    new_stream_id,
                    exc,
                )

    def _build_status_payload(self, event: Event) -> Optional[dict[str, Any]]:
        metadata = getattr(event, "metadata", None)
        payload: dict[str, Any] = {}
        if isinstance(metadata, Mapping):
            for key in (
                "loop_state",
                "loop_action",
                "iteration",
                "mentor_decision",
                "reason",
                "status",
            ):
                value = metadata.get(key)
                if value is not None:
                    payload[key] = value
        agent_name = getattr(event, "agent_name", None) or getattr(
            event, "author", None
        )
        if agent_name:
            payload["agent"] = agent_name
        role = getattr(getattr(event, "content", None), "role", None)
        if role:
            payload.setdefault("role", role)
        timestamp = getattr(event, "timestamp", None)
        if timestamp is not None:
            payload["timestamp"] = timestamp

        payload["partial"] = bool(getattr(event, "partial", False))
        try:
            payload["final"] = bool(event.is_final_response())
        except Exception:
            payload["final"] = False

        loop_state = payload.get("loop_state")
        if loop_state:
            payload["state"] = loop_state
        elif agent_name == "mentor_agent":
            payload["state"] = "mentor"
        elif agent_name == "remip_agent":
            payload["state"] = "worker"
        else:
            payload.setdefault("state", "running")

        return payload if payload else None

    async def _commit_carry_events(self, carry_events: list[Event]) -> None:
        if not carry_events or not self._session_service or not self._session_id:
            return

        self._log.debug(
            "Committing %d events to session %s",
            len(carry_events),
            self._session_id,
        )
        session = await self._session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=self._session_id,
        )

        committed_events = build_committed_events_from_partials(carry_events)
        for ev in committed_events:
            await self._session_service.append_event(session=session, event=ev)
        self._log.debug(
            "Committed %d events to session %s", len(committed_events), self._session_id
        )


TOOL_SKIP_NAMES = {"exit_loop", "ask"}


def _safe_pretty_json(data: Any) -> str:
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except TypeError:
        return str(data)


def render_tool_block(kind: str, name: str, payload: Any) -> str:
    if not name:
        return ""
    title = "Tool Call" if kind == "call" else "Tool Response"
    pretty = _safe_pretty_json(payload)
    return (
        "\n\n<details>\n"
        f"<summary>\n{title}: {html.escape(name)}\n</summary>\n\n"
        "```json\n"
        f"{pretty}\n"
        "```\n\n"
        "</details>\n\n"
    )


def extract_event_fragments(
    event: Event,
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    content = getattr(event, "content", None)
    if not content:
        return "", "", [], []

    text_parts: list[str] = []
    thought_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_responses: list[dict[str, Any]] = []

    for part in getattr(content, "parts", []) or []:
        if getattr(part, "thought", False):
            if getattr(part, "text", None):
                thought_parts.append(part.text or "")
        elif getattr(part, "function_call", None):
            func = getattr(part, "function_call")
            name = getattr(func, "name", "")
            if name and name not in TOOL_SKIP_NAMES:
                tool_calls.append(
                    {
                        "name": name,
                        "args": getattr(func, "args", None),
                    }
                )
        elif getattr(part, "function_response", None):
            func = getattr(part, "function_response")
            name = getattr(func, "name", "")
            if name and name not in TOOL_SKIP_NAMES:
                tool_responses.append(
                    {
                        "name": name,
                        "response": getattr(func, "response", None),
                    }
                )
        elif getattr(part, "text", None):
            text_parts.append(part.text or "")

    text = "".join(text_parts)
    thought = "\n\n".join(tp.strip() for tp in thought_parts if tp.strip())
    return text, thought, tool_calls, tool_responses


def process_event(event: Event) -> tuple[str | None, str | None, str | None]:
    """Processes an agent Event and formats its content for display."""
    author = getattr(event, "author", None)
    text_chunk, thought_chunk, tool_calls, tool_responses = extract_event_fragments(
        event
    )

    response_parts: list[str] = []
    if text_chunk:
        response_parts.append(text_chunk)
    for tool_call in tool_calls:
        block = render_tool_block(
            "call", tool_call.get("name", ""), tool_call.get("args")
        )
        if block:
            response_parts.append(block)
    for tool_response in tool_responses:
        block = render_tool_block(
            "response",
            tool_response.get("name", ""),
            tool_response.get("response"),
        )
        if block:
            response_parts.append(block)

    response_md = "".join(response_parts) or None
    thoughts_md = thought_chunk or None
    return author, response_md, thoughts_md


@st.cache_resource
def get_worker(_headers_provider: Callable[[], Dict[str, str]]) -> StreamWorker:
    return StreamWorker(headers_provider=_headers_provider)


def main():
    st.set_page_config(page_title="LoopAgent × ADK × Streamlit — Worker", page_icon="♻️")
    st.title("LoopAgent × ADK × Streamlit — worker-loop streaming (sticky session)")

    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        st.error("No API key found. Set GOOGLE_API_KEY or GEMINI_API_KEY.")
        st.stop()

    if "remip_sid" not in st.session_state:
        st.session_state.remip_sid = f"st-{uuid.uuid4()}"

    def _headers_provider():
        return {
            "X-Remip-Session": st.session_state.remip_sid,
            "X-App-Name": APP_NAME,
            "X-User-Id": USER_ID,
        }

    ss = st.session_state
    if not ss.get("loop_status_css_applied"):
        st.markdown(LOOP_STATUS_CSS, unsafe_allow_html=True)
        ss.loop_status_css_applied = True
    if "worker" not in ss:
        ss.worker = get_worker(_headers_provider=_headers_provider)
        ss.live_text = ""
        ss.streaming_active = False
        ss.active_stream_id = None
        ss.thought_text = ""
        ss.loop_status = {}
        ss.tool_markdown = ""

    ss.live_text = ss.get("live_text", "")
    ss.streaming_active = ss.get("streaming_active", False)
    ss.active_stream_id = ss.get("active_stream_id", None)
    ss.thought_text = ss.get("thought_text", "")
    ss.loop_status = ss.get("loop_status", {})
    ss.tool_markdown = ss.get("tool_markdown", "")

    status_placeholder = st.empty()

    def render_loop_status() -> None:
        info = ss.loop_status if isinstance(ss.loop_status, Mapping) else {}
        if not info:
            status_placeholder.empty()
            return

        def _truncate(value: str, limit: int = 180) -> str:
            value = value.strip()
            if len(value) <= limit:
                return value
            return value[: limit - 1] + "…"

        def _icon_for_state(key: str) -> tuple[str, str]:
            base = "loop-status__icon"
            if key in ("completed", "done"):
                return "✓", f"{base} loop-status__icon--done"
            if key in ("error", "failed"):
                return "!", f"{base} loop-status__icon--error"
            if key in ("interrupted", "paused"):
                return "⏸", f"{base} loop-status__icon--paused"
            return "•", base

        state = str(info.get("state") or info.get("loop_state") or "").strip()
        label_key = info.get("loop_state") or state or "running"
        label = LOOP_STATE_LABELS.get(
            label_key, label_key.replace("_", " ").title() if label_key else "Working"
        )
        show_spinner = ss.streaming_active and state not in (
            "completed",
            "error",
            "interrupted",
        )
        if state in ("completed", "error", "interrupted"):
            show_spinner = False

        meta_bits: list[str] = []
        iteration = info.get("iteration")
        if iteration not in (None, ""):
            meta_bits.append(f"Iteration {iteration}")
        agent = info.get("agent") or info.get("role")
        if agent:
            meta_bits.append(str(agent))
        action = info.get("loop_action") or info.get("status")
        if action:
            meta_bits.append(str(action))
        waiting_for = info.get("waiting_for")
        if waiting_for not in (None, ""):
            meta_bits.append(f"Waiting for stream {waiting_for}")

        detail_lines: list[str] = []
        mentor_decision = info.get("mentor_decision")
        if mentor_decision:
            detail_lines.append(f"Mentor: {mentor_decision}")
        for key in ("reason", "message", "error"):
            val = info.get(key)
            if val:
                detail_lines.append(str(val))

        meta_segments: list[str] = []
        if meta_bits:
            meta_segments.append(html.escape(" · ".join(str(bit) for bit in meta_bits)))
        for line in detail_lines:
            trimmed = _truncate(str(line))
            if trimmed:
                meta_segments.append(html.escape(trimmed))

        meta_html = "<br/>".join(meta_segments)
        label_html = html.escape(label or "Working")

        if show_spinner:
            icon_html = '<div class="loop-status__spinner"></div>'
        else:
            icon_symbol, icon_class = _icon_for_state(state or label_key)
            icon_html = f'<div class="{icon_class}">{html.escape(icon_symbol)}</div>'

        meta_block = (
            f'<div class="loop-status__meta">{meta_html}</div>' if meta_html else ""
        )
        status_placeholder.markdown(
            f"""
            <div class="loop-status">
                {icon_html}
                <div class="loop-status__body">
                    <div class="loop-status__stage">{label_html}</div>
                    {meta_block}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    def update_loop_status(data: Any, *, overwrite: bool = False) -> None:
        if overwrite:
            updated: dict[str, Any] = {}
        else:
            updated = (
                dict(ss.loop_status) if isinstance(ss.loop_status, Mapping) else {}
            )
        if data is None:
            updated = {}
        elif isinstance(data, Mapping):
            for key, value in data.items():
                if value is None:
                    updated.pop(key, None)
                else:
                    updated[key] = value
        else:
            updated["message"] = str(data)
        ss.loop_status = updated
        render_loop_status()

    render_loop_status()

    history_events: list[Event] = []
    try:
        history_events = ss.worker.get_history_messages()
    except Exception:
        history_events = []

    for event in history_events:
        text_chunk, thought_chunk, tool_calls, tool_responses = extract_event_fragments(
            event
        )
        if not (text_chunk or thought_chunk or tool_calls or tool_responses):
            continue

        author = getattr(event, "author", None) or getattr(
            getattr(event, "content", None), "role", None
        )
        chat_role = "user" if author == "user" else "assistant"
        with st.chat_message(chat_role, avatar=AVATARS.get(author)):
            if author not in ("user", "assistant", None):
                st.caption(author)
            if text_chunk:
                st.markdown(text_chunk)
            for tool_call in tool_calls:
                block = render_tool_block(
                    "call", tool_call.get("name", ""), tool_call.get("args")
                )
                if block:
                    st.markdown(block, unsafe_allow_html=True)
            for tool_response in tool_responses:
                block = render_tool_block(
                    "response",
                    tool_response.get("name", ""),
                    tool_response.get("response"),
                )
                if block:
                    st.markdown(block, unsafe_allow_html=True)
            if thought_chunk:
                thought_container = st.expander("Thinking", expanded=False)
                thought_container.markdown(thought_chunk)

    live_placeholders: Optional[dict[str, Any]] = None
    if ss.streaming_active:
        with st.chat_message("assistant", avatar=AVATARS.get("remip_agent")):
            text_placeholder = st.empty()
            if ss.live_text:
                text_placeholder.markdown(ss.live_text)
            tool_placeholder = st.empty()
            if ss.tool_markdown:
                tool_placeholder.markdown(ss.tool_markdown, unsafe_allow_html=True)
            thought_container = st.expander("Thinking", expanded=False)
            thought_placeholder = thought_container.empty()
            if ss.thought_text:
                thought_placeholder.markdown(ss.thought_text)
            live_placeholders = {
                "text": text_placeholder,
                "tools": tool_placeholder,
                "thought": thought_placeholder,
            }

    def handle_stream_event(
        kind: str,
        stream_id: Optional[int],
        payload: Any,
        placeholders: Optional[dict[str, Any]],
    ) -> bool:
        text_placeholder = placeholders.get("text") if placeholders else None
        tool_placeholder = placeholders.get("tools") if placeholders else None
        thought_placeholder = placeholders.get("thought") if placeholders else None

        active_id = ss.active_stream_id
        if active_id is not None and stream_id != active_id:
            return False

        if kind == "status":
            update_loop_status(payload)
            return False

        if kind == "reset":
            ss.live_text = ""
            ss.streaming_active = True
            ss.thought_text = ""
            ss.tool_markdown = ""
            update_loop_status({"state": "running"}, overwrite=True)
            if text_placeholder:
                try:
                    text_placeholder.markdown("")
                except Exception:
                    pass
            if thought_placeholder:
                try:
                    thought_placeholder.markdown("")
                except Exception:
                    pass
            if tool_placeholder:
                try:
                    tool_placeholder.markdown("")
                except Exception:
                    pass
            return False

        if kind == "chunk":
            ss.live_text += payload or ""
            if text_placeholder:
                try:
                    text_placeholder.markdown(ss.live_text)
                except Exception:
                    pass
            return False

        if kind == "final_chunk":
            if payload:
                payload_str = payload or ""
                if ss.live_text and payload_str.startswith(ss.live_text):
                    ss.live_text = payload_str
                elif ss.live_text and ss.live_text.endswith(payload_str):
                    pass
                else:
                    ss.live_text += payload_str
            if text_placeholder:
                try:
                    text_placeholder.markdown(ss.live_text)
                except Exception:
                    pass
            return False

        if kind == "thought":
            if payload:
                if ss.thought_text:
                    ss.thought_text += "\n\n" + payload
                else:
                    ss.thought_text = payload
            if thought_placeholder:
                try:
                    thought_placeholder.markdown(ss.thought_text)
                except Exception:
                    pass
            return False

        if kind == "function_call":
            name = ""
            data = None
            if isinstance(payload, Mapping):
                name = str(payload.get("name") or "")
                data = payload.get("args")
            detail = render_tool_block("call", name, data)
            if detail:
                ss.tool_markdown = (
                    f"{ss.tool_markdown}{detail}" if ss.tool_markdown else detail
                )
                if tool_placeholder:
                    try:
                        tool_placeholder.markdown(
                            ss.tool_markdown, unsafe_allow_html=True
                        )
                    except Exception:
                        pass
            return False

        if kind == "function_response":
            name = ""
            data = None
            if isinstance(payload, Mapping):
                name = str(payload.get("name") or "")
                data = payload.get("response")
            detail = render_tool_block("response", name, data)
            if detail:
                ss.tool_markdown = (
                    f"{ss.tool_markdown}{detail}" if ss.tool_markdown else detail
                )
                if tool_placeholder:
                    try:
                        tool_placeholder.markdown(
                            ss.tool_markdown, unsafe_allow_html=True
                        )
                    except Exception:
                        pass
            return False

        if kind == "final_thought":
            if payload:
                payload_str = payload or ""
                if ss.thought_text and payload_str.startswith(ss.thought_text):
                    ss.thought_text = payload_str
                elif ss.thought_text and ss.thought_text.endswith(payload_str):
                    pass
                else:
                    if ss.thought_text:
                        ss.thought_text += "\n\n" + payload_str
                    else:
                        ss.thought_text = payload_str
            if thought_placeholder:
                try:
                    thought_placeholder.markdown(ss.thought_text)
                except Exception:
                    pass
            return False

        if kind == "done":
            if text_placeholder and ss.live_text:
                try:
                    text_placeholder.markdown(ss.live_text)
                except Exception:
                    pass
            if thought_placeholder and ss.thought_text:
                try:
                    thought_placeholder.markdown(ss.thought_text)
                except Exception:
                    pass
            if tool_placeholder and ss.tool_markdown:
                try:
                    tool_placeholder.markdown(ss.tool_markdown, unsafe_allow_html=True)
                except Exception:
                    pass
            ss.streaming_active = False
            ss.active_stream_id = None
            update_loop_status({"state": "completed"}, overwrite=False)
            return True

        if kind == "error":
            ss.streaming_active = False
            ss.active_stream_id = None
            update_loop_status({"state": "error"}, overwrite=False)
            if payload:
                st.warning(payload)
            return True

        if kind == "interrupted":
            ss.streaming_active = False
            ss.active_stream_id = None
            update_loop_status({"state": "interrupted"}, overwrite=False)
            return True

        return False

    def drain_queue(
        placeholders: Optional[dict[str, Any]], blocking: bool = False
    ) -> bool:
        completed = False
        while True:
            try:
                if blocking:
                    item = ss.worker.out_q.get(timeout=0.2)
                else:
                    item = ss.worker.out_q.get_nowait()
            except queue.Empty:
                break

            if len(item) == 3:
                kind, stream_id, payload = item
            else:
                kind = item[0]
                stream_id = ss.active_stream_id
                payload = item[1] if len(item) > 1 else None

            completed = (
                handle_stream_event(kind, stream_id, payload, placeholders) or completed
            )

            if blocking and completed:
                break
        return completed

    def trigger_prompt(prompt_text: str) -> None:
        with st.chat_message("user", avatar=AVATARS.get("user")):
            st.markdown(prompt_text)

        with st.chat_message("assistant", avatar=AVATARS.get("remip_agent")):
            text_placeholder = st.empty()
            tool_placeholder = st.empty()
            thought_container = st.expander("Thinking", expanded=False)
            thought_placeholder = thought_container.empty()
            placeholders = {
                "text": text_placeholder,
                "tools": tool_placeholder,
                "thought": thought_placeholder,
            }

        stream_id = ss.worker.start_stream(prompt_text)
        ss.active_stream_id = stream_id
        ss.live_text = ""
        ss.thought_text = ""
        ss.tool_markdown = ""
        ss.streaming_active = True
        update_loop_status(
            {
                "state": "starting",
                "agent": "remip_agent",
                "message": "Preparing new loop run…",
            },
            overwrite=True,
        )

        timeout_str = os.getenv("REMIP_STREAM_TIMEOUT", "0").strip()
        try:
            timeout_sec = float(timeout_str)
        except ValueError:
            timeout_sec = 0.0
        deadline = time.time() + timeout_sec if timeout_sec > 0 else None
        while True:
            completed = drain_queue(placeholders, blocking=True)
            if completed or not ss.streaming_active:
                break
            if deadline and time.time() > deadline:
                logger.warning(
                    "UI drain loop hit timeout (%.1fs) for stream %s; leaving streaming_active=True",
                    timeout_sec,
                    stream_id,
                )
                break

        drain_queue(placeholders, blocking=False)

    drain_queue(live_placeholders, blocking=False)

    selected_example_prompt = None
    examples = load_examples("ja")
    if examples:
        with st.sidebar:
            st.subheader("Example Problems")
            titles = ["(Manual input)"] + list(examples.keys())
            selected_title = st.selectbox(
                "Choose an example",
                titles,
                key="example_selector",
            )
            if selected_title and selected_title != "(Manual input)":
                example_content = examples[selected_title]
                if st.button(
                    "Use this example",
                    use_container_width=True,
                    key="use_selected_example",
                ):
                    selected_example_prompt = example_content
                with st.expander("Preview", expanded=True):
                    st.markdown(example_content)

    prompt = st.chat_input("Type a message (new input interrupts current generation)")

    if selected_example_prompt:
        trigger_prompt(selected_example_prompt)
    elif prompt:
        trigger_prompt(prompt)


if __name__ == "__main__":
    main()
