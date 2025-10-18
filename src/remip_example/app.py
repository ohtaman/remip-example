"""
Main Streamlit application for the remip-example.

This script orchestrates the UI, agent execution, and session management,
relying on helper modules for specific functionalities.
"""

import json
import os
import queue
import threading
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Generator

import asyncio
import streamlit as st
from google.adk.agents import RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.genai import types

from remip_example.agent import build_agent
from remip_example.config import APP_NAME, NORMAL_MAX_CALLS
from remip_example.services import (
    clear_talk_session,
    create_talk_session,
    get_session_service,
    get_talk_session,
)
from remip_example.ui_components import (
    api_key_dialog,
    load_examples,
    new_session_form,
    settings_form,
)

AVATARS = {"remip_agent": "🦸", "mentor_agent": "🧚", "user": ""}


class AsyncIteratorBridge:
    """
    A bridge to allow a background thread running an async generator to communicate
    with the main Streamlit thread.
    """

    def __init__(self):
        self.SENTINEL = object()
        self.q = queue.Queue()
        self._command_q = queue.Queue()
        self.current_generator = None
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
                command, data = self._command_q.get_nowait()

                if command == "START":
                    if agent_task:
                        if self.current_generator is not None:
                            print(self.current_generator)
                            await self.current_generator.aclose()
                        agent_task.cancel()
                        try:
                            await agent_task
                        except (asyncio.CancelledError, StopAsyncIteration):
                            pass  # Expected cancellation

                    # Clear the queue before starting a new task
                    while not self.q.empty():
                        self.q.get_nowait()

                    factory = data
                    agent_task = asyncio.create_task(self._run_agent_task(factory))

                elif command == "STOP":
                    if agent_task:
                        agent_task.cancel()
                        try:
                            await agent_task
                        except (asyncio.CancelledError, StopAsyncIteration):
                            pass  # Expected cancellation
                        agent_task = None

                    while not self.q.empty():
                        self.q.get_nowait()
                    self.q.put(self.SENTINEL)

                elif command == "TERMINATE":
                    if agent_task:
                        agent_task.cancel()
                    break
            except queue.Empty:
                await asyncio.sleep(0.01)

    async def _run_agent_task(self, factory):
        """Runs the agent's async iterator and puts results on the response queue."""
        try:
            async_iterable = await factory()
            self.current_generator = async_iterable
            async for item in async_iterable:
                self.q.put(item)
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        except Exception:
            # In a real app, you'd want to log this.
            pass
        finally:
            self.q.put(self.SENTINEL)

    def start_task(self, factory: Callable[[], Awaitable[AsyncIterator[Any]]]):
        """Public method for the UI thread to start a new agent task."""
        self._command_q.put(("START", factory))

    def stop_task(self):
        """Public method for the UI thread to stop the current agent task."""
        self._command_q.put(("STOP", None))

    def __iter__(self) -> Generator[Any, Any, None]:
        """Allows the UI thread to iterate over results from the response queue."""
        while True:
            item = self.q.get()
            if item is self.SENTINEL:
                break
            yield item

    def __del__(self):
        """Ensure the background thread is terminated when the bridge is garbage collected."""
        self._command_q.put(("TERMINATE", None))


def process_event(event: Event) -> tuple[str | None, str | None, str | None]:
    """
    Processes an agent Event and formats its content for beautiful display.

    Returns:
        A tuple containing (author, response_markdown, thoughts_markdown).
    """
    author = event.author
    if event.content is None:
        return author, None, None

    response_markdown = ""
    thoughts_markdown = ""

    for part in event.content.parts:
        if part.thought:
            thoughts_markdown += part.text
        elif part.function_call:
            tool_name = part.function_call.name
            tool_args = json.dumps(
                part.function_call.args, indent=2, ensure_ascii=False
            )
            response_markdown += (
                f"<details><summary>Tool Call: {tool_name}</summary>\n\n"
                f"```json\n{tool_args}\n```\n\n</details>"
            )
        elif part.function_response:
            tool_name = part.function_response.name
            try:
                # Try to serialize the response to a pretty JSON string.
                tool_response = json.dumps(
                    part.function_response.response, indent=2, ensure_ascii=False
                )
                lang = "json"
            except TypeError:
                # If serialization fails, check for a nested CallToolResult.
                raw_response = part.function_response.response
                extracted_json = None
                if isinstance(raw_response, dict) and "result" in raw_response:
                    result_obj = raw_response["result"]
                    # Check if it looks like a CallToolResult with nested JSON.
                    if (
                        hasattr(result_obj, "content")
                        and result_obj.content
                        and hasattr(result_obj.content[0], "text")
                    ):
                        try:
                            # Extract and format the nested JSON string.
                            parsed_text = json.loads(result_obj.content[0].text)
                            extracted_json = json.dumps(
                                parsed_text, indent=2, ensure_ascii=False
                            )
                        except (json.JSONDecodeError, IndexError):
                            pass  # Not a valid JSON string, proceed to str() fallback

                if extracted_json:
                    tool_response = extracted_json
                    lang = "json"
                else:
                    # The ultimate fallback: convert the object to a plain string.
                    tool_response = str(raw_response)
                    lang = "python"

            response_markdown += (
                f"<details><summary>Tool Response: {tool_name}</summary>\n\n"
                f"```{lang}\n{tool_response}\n```\n\n</details>"
            )
        elif part.text:
            response_markdown += part.text

    return author, response_markdown or None, thoughts_markdown or None


def group_events(events: list[Event]) -> list[tuple[str, str, str]]:
    """Groups a list of events by author for clean display."""
    if not events:
        return []

    grouped = []
    author, response, thoughts = process_event(events[0])
    if not author:
        author = "unknown"

    current_author = author
    current_response = response or ""
    current_thoughts = thoughts or ""

    for event in events[1:]:
        author, response, thoughts = process_event(event)
        if not author:
            continue

        if author == current_author:
            if response:
                current_response += response
            if thoughts:
                current_thoughts += thoughts + "\n\n"
        else:
            grouped.append((current_author, current_response, current_thoughts))
            current_author = author
            current_response = response or ""
            current_thoughts = thoughts or ""

    grouped.append((current_author, current_response, current_thoughts))
    return grouped


def initialize_session():
    """
    Initializes the Streamlit session state, ensuring all required
    keys are present.
    """
    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or api_key_dialog()
        if not api_key:
            st.rerun()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    if "async_bridge" not in st.session_state:
        st.session_state.async_bridge = AsyncIteratorBridge()


def _display_message_content(response: str | None, thoughts: str | None) -> None:
    """Renders the content of a chat message, including response and thoughts."""
    if response:
        st.markdown(response, unsafe_allow_html=True)
    if thoughts:
        with st.expander("Show Thoughts"):
            st.markdown(thoughts)


def render():
    """
    Main rendering function for the Streamlit application.
    """
    with st.sidebar:
        language, is_agent_mode = settings_form()
        examples = load_examples(language)
        example_title = st.selectbox("Example Prompt", [""] + list(examples.keys()))
        example = examples.get(example_title, "")
        if st.button("New Session", use_container_width=True):
            if "async_bridge" in st.session_state:
                del st.session_state.async_bridge
            clear_talk_session()
            st.rerun()

    talk_session = get_talk_session()
    if talk_session is None:
        user_request = new_session_form(example)
        if user_request:
            talk_session = create_talk_session(
                user_id=st.session_state.user_id,
                session_id=str(uuid.uuid4()),
                state={"user_request": user_request},
            )
            st.rerun()
        else:
            st.stop()

    user_request = talk_session.state.get("user_request")

    # Display the initial user request.
    with st.container(height=280):
        st.markdown(user_request)

    # Display historical events, grouped by author.
    # Skip the first event which is the user request.
    for author, response, thoughts in group_events(talk_session.events[1:]):
        with st.chat_message(author, avatar=AVATARS.get(author)):
            _display_message_content(response, thoughts)

    user_input = st.chat_input("Input your request")
    if user_input:
        with st.chat_message("user", avatar=AVATARS.get("user")):
            st.markdown(user_input)
    elif len(talk_session.events) == 0:
        user_input = user_request

    if user_input:
        agent = build_agent(is_agent_mode=is_agent_mode)
        with st.sidebar:
            st.write(agent)
        runner = Runner(
            app_name=APP_NAME, agent=agent, session_service=get_session_service()
        )

        async def _make_iter() -> AsyncIterator[Event]:
            return runner.run_async(
                user_id=talk_session.user_id,
                session_id=talk_session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=user_input)]
                ),
                run_config=RunConfig(
                    streaming_mode=StreamingMode.SSE, max_llm_calls=NORMAL_MAX_CALLS
                ),
            )

        st.session_state.async_bridge.start_task(_make_iter)

        # Live stream the agent's response, grouping messages on the fly.
        last_author = None
        full_response = ""
        full_thoughts = ""
        message_placeholder = None
        thought_placeholder = None

        for event in st.session_state.async_bridge:
            author, response, thoughts = process_event(event)
            if not author:
                continue

            if author != last_author:
                last_author = author
                full_response = ""
                full_thoughts = ""
                with st.chat_message(author, avatar=AVATARS.get(author)):
                    thought_placeholder = st.empty()
                    message_placeholder = st.empty()

            if response and message_placeholder:
                full_response += response
                message_placeholder.markdown(full_response, unsafe_allow_html=True)

            if thoughts and thought_placeholder:
                full_thoughts += thoughts + "\n\n"
                with thought_placeholder.container():
                    with st.expander("Show Thoughts"):
                        st.markdown(full_thoughts)

        st.rerun()

    st.write(talk_session.events)


def main():
    st.set_page_config(page_title="remip-example", layout="wide")
    initialize_session()
    render()


if __name__ == "__main__":
    main()
