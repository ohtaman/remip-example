"""Core services for the asynchronous agent architecture."""

import asyncio
import uuid

from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Thread
from typing import Dict, List

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from remip_example.agent import build_agent
from remip_example.config import APP_NAME


@dataclass
class Message:
    """Represents a single message in a talk session."""

    content: str
    sender: str  # "user" or "agent"


@dataclass
class TalkSession:
    """Represents a single conversation, including its message history."""

    id: str
    user_id: str
    messages: List[Message] = field(default_factory=list)
    agent_mode: bool = True


class AgentService:
    """Manages talk sessions and orchestrates agent processing using ADK components."""

    def __init__(self):
        self._sessions: Dict[str, Dict[str, TalkSession]] = {}
        self._input_queues: Dict[str, Queue] = {}
        self._output_queues: Dict[str, Queue] = {}
        self._worker_thread = Thread(target=self._worker, daemon=True)
        self._is_running = True

        # Use ADK components for session management
        self.session_service = InMemorySessionService()

        self._worker_thread.start()

    async def _async_worker(self):
        while self._is_running:
            task_found = False
            for session_id, queue in list(self._input_queues.items()):
                try:
                    task = queue.get_nowait()
                    task_found = True
                    task_type = task[0]

                    if task_type == "create_session":
                        _, user_id, task_session_id, is_agent_mode = task
                        await self.session_service.create_session(
                            app_name=APP_NAME,
                            user_id=user_id,
                            session_id=task_session_id,
                            state={"agent_mode": is_agent_mode},
                        )
                    elif task_type == "add_message":
                        _, user_id, task_session_id, message = task
                        session = self.get_session(user_id, task_session_id)
                        agent = build_agent(is_agent_mode=session.agent_mode)

                        runner = Runner(
                            session_service=self.session_service,
                            app_name=APP_NAME,
                            agent=agent,
                        )

                        user_content = Content(
                            parts=[Part(text=message.content)], role="user"
                        )
                        response_generator = runner.run_async(
                            new_message=user_content,
                            session_id=task_session_id,
                            user_id=user_id,
                        )

                        response_parts = []
                        async for event in response_generator:
                            if event.content and event.content.parts:
                                for part in event.content.parts:
                                    if part.text:
                                        response_parts.append(part.text)
                            # The generator can hang, so break manually on STOP
                            if (
                                event.finish_reason
                                and event.finish_reason.name == "STOP"
                            ):
                                break

                        response_content = "".join(response_parts)

                        if response_content:
                            response_message = Message(
                                content=response_content, sender="agent"
                            )
                            self._output_queues[task_session_id].put(response_message)

                except Empty:
                    continue
                except Exception as e:
                    print(f"WORKER ERROR: {e}")
                    error_message = Message(content=f"Error: {e}", sender="agent")
                    self._output_queues[session_id].put(error_message)

            if not task_found:
                await asyncio.sleep(0.1)

    def _worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_worker())
        finally:
            loop.close()

    def stop(self):
        self._is_running = False

    def create_talk_session(self, user_id: str, is_agent_mode: bool = True) -> str:
        session_id = str(uuid.uuid4())
        if user_id not in self._sessions:
            self._sessions[user_id] = {}

        # Keep our local representation for the UI for now
        self._sessions[user_id][session_id] = TalkSession(
            id=session_id, user_id=user_id, agent_mode=is_agent_mode
        )
        input_queue = Queue()
        self._input_queues[session_id] = input_queue
        self._output_queues[session_id] = Queue()

        # Delegate the async session creation to the worker thread
        input_queue.put(("create_session", user_id, session_id, is_agent_mode))

        return session_id

    def list_sessions(self, user_id: str) -> List[str]:
        return list(self._sessions.get(user_id, {}).keys())

    def get_session(self, user_id: str, session_id: str) -> TalkSession:
        return self._sessions[user_id][session_id]

    def add_message(self, user_id: str, session_id: str, message_content: str):
        session = self.get_session(user_id, session_id)
        message = Message(content=message_content, sender="user")
        session.messages.append(message)

        input_queue = self._input_queues[session_id]
        while not input_queue.empty():
            try:
                input_queue.get_nowait()
            except Empty:
                break

        # Create a specific task for adding a message
        input_queue.put(("add_message", user_id, session_id, message))

    def get_messages(self, user_id: str, session_id: str) -> List[Message]:
        session = self.get_session(user_id, session_id)
        output_queue = self._output_queues.get(session_id)

        if output_queue:
            while not output_queue.empty():
                try:
                    new_message = output_queue.get_nowait()
                    session.messages.append(new_message)
                except Empty:
                    break

        return session.messages
