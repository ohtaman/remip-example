"""Core services for the asynchronous agent architecture."""

import asyncio
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Generator

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
        self._session_message_queues: dict[str, asyncio.Queue] = {}  # New line
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
        self._output_queues[session_id] = (
            queue.Queue()
        )  # Changed to unbounded standard queue
        self._session_message_queues[session_id] = asyncio.Queue()

        # Delegate the async session creation and task start to the worker thread
        self._command_q.put(
            ("create_adk_session", session_id, (user_id, is_agent_mode))
        )
        self._command_q.put(
            ("START_SESSION_TASK", session_id, (user_id, is_agent_mode))  # New command
        )
        return session_id

    def get_talk_session(self, user_id: str, session_id: str) -> TalkSession:
        return self._sessions[user_id][session_id]

    def list_talk_sessions(self, user_id: str) -> list[str]:
        return list(self._sessions.get(user_id, {}).keys())

        def add_message(self, user_id: str, session_id: str, message_content: str):
            user_content = Content(parts=[Part(text=message_content)], role="user")

            with self._task_status_lock:
                self._task_is_running[session_id] = True  # Keep this for UI feedback

            # Put the message into the session's message queue

            self._session_message_queues[session_id].put_nowait(user_content)

    def get_historical_events(self, user_id: str, session_id: str) -> list[Event]:
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
                    print(f"PRODUCER: Creating ADK session for {session_id}")
                    await self.session_service.create_session(
                        app_name=APP_NAME,
                        user_id=user_id,
                        session_id=session_id,
                        state={"agent_mode": is_agent_mode},
                    )
                    print(f"PRODUCER: ADK session created for {session_id}")
                    continue

                if command == "START_SESSION_TASK":  # New command handler
                    user_id, is_agent_mode = data
                    if (
                        session_id not in self._active_tasks
                    ):  # Only start if not already running
                        print(f"PRODUCER: Starting session task for {session_id}")
                        self._active_tasks[session_id] = asyncio.create_task(
                            self._run_agent_task_per_session(
                                session_id, user_id, is_agent_mode
                            )
                        )
                        print(f"PRODUCER: Session task started for {session_id}")
                    else:
                        print(
                            f"PRODUCER: Session task for {session_id} already running."
                        )
                    continue  # Continue to next command
                elif command == "TERMINATE":
                    print("PRODUCER: TERMINATE command received. Exiting loop.")
                    break

            except queue.Empty:
                # print("PRODUCER: Command queue empty. Sleeping.") # Too verbose
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"PRODUCER: Error in producer loop: {e}")

    async def _run_agent_task_per_session(
        self, session_id: str, user_id: str, is_agent_mode: bool
    ):
        """Long-running task to process messages for a single session."""
        task_name = asyncio.current_task().get_name()
        print(f"SESSION TASK [{task_name}]: Starting for session {session_id}.")

        print(f"SESSION TASK [{task_name}]: Building agent for session {session_id}.")
        agent = build_agent(is_agent_mode=is_agent_mode)
        runner = Runner(
            session_service=self.session_service, app_name=APP_NAME, agent=agent
        )
        print(
            f"SESSION TASK [{task_name}]: Agent and runner built for session {session_id}."
        )

        session_message_queue = self._session_message_queues[session_id]
        response_q = self._output_queues[session_id]

        try:
            while True:
                print(
                    f"SESSION TASK [{task_name}]: Waiting for message for session {session_id}."
                )
                user_content = (
                    await session_message_queue.get()
                )  # Wait for new messages
                print(
                    f"SESSION TASK [{task_name}]: Received message for session {session_id}: {user_content.parts[0].text if user_content.parts else 'No content'}."
                )

                with self._task_status_lock:
                    self._task_is_running[session_id] = True
                print(
                    f"SESSION TASK [{task_name}]: Task status set to running for session {session_id}."
                )

                print(
                    f"SESSION TASK [{task_name}]: Running agent for message in session {session_id}."
                )
                async_iterable = runner.run_async(
                    new_message=user_content, session_id=session_id, user_id=user_id
                )
                print(
                    f"SESSION TASK [{task_name}]: Agent run_async started for session {session_id}."
                )

                print(
                    f"SESSION TASK [{task_name}]: Entering async for loop for session {session_id}."
                )
                async for item in async_iterable:
                    response_q.put(item)
                    print(
                        f"SESSION TASK [{task_name}]: Put item to response_q for session {session_id}."
                    )
                print(
                    f"SESSION TASK [{task_name}]: Exited async for loop for session {session_id}."
                )

                with self._task_status_lock:
                    self._task_is_running[session_id] = False
                print(
                    f"SESSION TASK [{task_name}]: Task status set to not running for session {session_id}."
                )
                response_q.put(self.SENTINEL)  # Signal end of response for this message
                print(
                    f"SESSION TASK [{task_name}]: SENTINEL put to response_q for session {session_id}."
                )

        except asyncio.CancelledError:
            print(f"SESSION TASK [{task_name}]: Cancelled for session {session_id}.")
        except Exception as e:
            print(f"SESSION TASK [{task_name}]: Errored for session {session_id}: {e}")
        finally:
            print(f"SESSION TASK [{task_name}]: Cleaning up for session {session_id}.")
            if async_iterable is not None and hasattr(async_iterable, "aclose"):
                try:
                    await async_iterable.aclose()
                    print(
                        f"SESSION TASK [{task_name}]: async_iterable closed for session {session_id}."
                    )
                except Exception as e:
                    print(
                        f"SESSION TASK [{task_name}]: Error closing async_iterable for session {session_id}: {e}"
                    )

            with self._task_status_lock:
                self._task_is_running[session_id] = False
            print(
                f"SESSION TASK [{task_name}]: Task status set to not running in finally for session {session_id}."
            )
            # If the session task itself is cancelled, ensure output queue is also signaled
            if not response_q.empty():  # Clear any pending items
                print(
                    f"SESSION TASK [{task_name}]: Clearing pending items from response_q for session {session_id}."
                )
                try:
                    response_q.get_nowait()
                except queue.Empty:
                    pass
            response_q.put(self.SENTINEL)
            print(
                f"SESSION TASK [{task_name}]: SENTINEL put to response_q in finally for session {session_id}."
            )
