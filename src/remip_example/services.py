"""Core services for the asynchronous agent architecture."""

import asyncio
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Generator

from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from remip_example.agent import build_agent
from remip_example.config import APP_NAME


@dataclass
class TalkSession:
    """Represents a single conversation, including its message history."""

    id: str
    user_id: str
    agent_mode: bool = True


class AgentService:
    """Manages talk sessions and orchestrates agent processing."""

    def __init__(self):
        self.SENTINEL = object()
        self._sessions: dict[str, dict[str, TalkSession]] = {}
        self._command_q = queue.Queue()
        self._output_queues: dict[str, queue.Queue] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._task_status_lock = threading.Lock()
        self._task_is_running: dict[str, bool] = {}

        self.session_service = InMemorySessionService()

        self._worker_thread = threading.Thread(target=self._runner, daemon=True)
        self._is_running = True
        self._worker_thread.start()

    def create_talk_session(self, user_id: str, is_agent_mode: bool = True) -> str:
        session_id = str(uuid.uuid4())
        if user_id not in self._sessions:
            self._sessions[user_id] = {}

        self._sessions[user_id][session_id] = TalkSession(
            id=session_id, user_id=user_id, agent_mode=is_agent_mode
        )
        self._output_queues[session_id] = queue.Queue(maxsize=1)

        # Delegate the async session creation to the worker thread
        self._command_q.put(
            ("create_adk_session", session_id, (user_id, is_agent_mode))
        )
        return session_id

    def get_talk_session(self, user_id: str, session_id: str) -> TalkSession:
        return self._sessions[user_id][session_id]

    def list_talk_sessions(self, user_id: str) -> list[str]:
        return list(self._sessions.get(user_id, {}).keys())

    def add_message(self, user_id: str, session_id: str, message_content: str):
        talk_session = self.get_talk_session(user_id, session_id)
        user_content = Content(parts=[Part(text=message_content)], role="user")

        # Create the agent and runner here, in the main thread.
        agent = build_agent(is_agent_mode=talk_session.agent_mode)
        runner = Runner(
            session_service=self.session_service, app_name=APP_NAME, agent=agent
        )

        async def _make_iter() -> AsyncIterator[Any]:
            return runner.run_async(
                new_message=user_content, session_id=session_id, user_id=user_id
            )

        with self._task_status_lock:
            self._task_is_running[session_id] = True
        self._command_q.put(("START", session_id, _make_iter))

    def get_historical_messages(self, user_id: str, session_id: str) -> list[Event]:
        """Returns the list of historical messages for a session."""
        adk_session = asyncio.run(
            self.session_service.get_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
        )
        if adk_session is not None:
            return adk_session.events
        else:
            return []

    def is_task_running(self, session_id: str) -> bool:
        """Checks if a task is currently running for the given session."""
        with self._task_status_lock:
            return self._task_is_running.get(session_id, False)

    def stream_new_responses(self, session_id: str) -> Generator[Any, None, None]:
        """Yields new response events for a session. THIS IS A BLOCKING GENERATOR."""
        response_q = self._output_queues[session_id]

        while True:
            event = response_q.get()  # Blocks until an item is available
            if event is self.SENTINEL:
                break
            yield event

    def stop(self):
        self._is_running = False
        self._command_q.put(("TERMINATE", None, None))

    def _runner(self):
        """The main entry point for the background thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._producer_loop())
        finally:
            loop.close()

    async def _producer_loop(self):
        """The core async event loop for the background thread."""
        while self._is_running:
            try:
                command, session_id, data = self._command_q.get_nowait()
                print(f"PRODUCER: Got command '{command}' for session {session_id}")

                if command == "create_adk_session":
                    user_id, is_agent_mode = data
                    await self.session_service.create_session(
                        app_name=APP_NAME,
                        user_id=user_id,
                        session_id=session_id,
                        state={"agent_mode": is_agent_mode},
                    )
                    continue

                if session_id in self._active_tasks:
                    self._active_tasks[session_id].cancel()
                    try:
                        await self._active_tasks[session_id]
                    except asyncio.CancelledError:
                        pass  # Cancellation is expected

                if command == "START":
                    print(f"PRODUCER: Creating new task for {session_id}")
                    factory = data
                    response_q = self._output_queues[session_id]

                    # Clear the queue of any stale items from the cancelled task
                    while not response_q.empty():
                        try:
                            response_q.get_nowait()
                        except queue.Empty:
                            break

                    self._active_tasks[session_id] = asyncio.create_task(
                        self._run_agent_task(factory, response_q, session_id)
                    )
                elif command == "TERMINATE":
                    break

            except queue.Empty:
                await asyncio.sleep(0.01)

    async def _run_agent_task(self, factory, response_q, session_id):
        """Runs the agent's async iterator and puts results on the response queue."""
        task_name = asyncio.current_task().get_name()
        print(f"TASK [{task_name}]: Starting for session {session_id}.")
        try:
            async_iterable = await factory()
            async for item in async_iterable:
                response_q.put(item)
        except (asyncio.CancelledError, StopAsyncIteration):
            print(
                f"TASK [{task_name}]: Cancelled or finished for session {session_id}."
            )
        except Exception as e:
            print(f"TASK [{task_name}]: Errored for session {session_id}: {e}")
        finally:
            print(f"TASK [{task_name}]: Cleaning up for session {session_id}.")
            with self._task_status_lock:
                self._task_is_running[session_id] = False
            response_q.put(self.SENTINEL)
