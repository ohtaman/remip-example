import asyncio
import json
import os
from queue import Empty, Queue
import re
import threading
import uuid

from google.adk.agents import RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from google.genai.types import Content, Part

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit_autorefresh import st_autorefresh

from remip_example.agent import build_agent
from remip_example.config import APP_NAME, AVATARS, USAGE
from remip_example.utils import load_examples


class BackgroundAgentRunner:
    def __init__(self, user_id: str, api_key: str):
        self._user_id = user_id
        self._api_key = api_key
        self._session_service = None
        self._event_history = []
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._input_queue = Queue[Content]()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="worker",
        )
        add_script_run_ctx(self._thread)

    def run(self, initial_message: Content):
        self._input_queue.put(initial_message)
        self._thread.start()

    def _run(self):
        asyncio.run(self._run_loop())

    async def _run_loop(self):
        self._agent = build_agent(is_agent_mode=True, api_key=self._api_key)
        self._session_service = InMemorySessionService()
        self._session = await self._session_service.create_session(
            app_name=APP_NAME,
            user_id=self._user_id,
        )
        self._event_history = self._session.events.copy()
        self._runner = Runner(
            app_name=APP_NAME,
            agent=self._agent,
            session_service=self._session_service,
        )
        self._run_config = RunConfig(
            streaming_mode=StreamingMode.SSE, max_llm_calls=100
        )
        while not self._stop.is_set():
            try:
                message = self._input_queue.get(timeout=1)
                invocation_id = None
                agen = self._runner.run_async(
                    user_id=self._user_id,
                    session_id=self._session.id,
                    new_message=message,
                    run_config=self._run_config,
                )
                self._interrupt.clear()
                async for event in agen:
                    # Append new message manualy
                    if invocation_id is None:
                        invocation_id = event.invocation_id
                        self._event_history.append(
                            Event(
                                content=message,
                                author="user",
                                invocation_id=invocation_id,
                            )
                        )
                    self._event_history.append(event)
                    if self._stop.is_set():
                        break
                    if self._interrupt.is_set():
                        self._interrupt.clear()
                        break
                self._input_queue.task_done()
            except Empty:
                continue

    def add_message(self, message: Content):
        self._input_queue.put(message)
        self._interrupt.set()

    def get_event_history(self) -> list[Event]:
        return list[Event](self._event_history)

    def stop(self):
        self._stop.set()


def create_conversation_session(initial_prompt: str) -> dict[any]:
    worker = BackgroundAgentRunner(
        user_id=st.session_state.user_id, api_key=st.session_state.api_key
    )
    worker.run(Content(role="user", parts=[Part(text=initial_prompt)]))

    session = {
        "initial_prompt": initial_prompt,
        "worker": worker,
    }
    return session


def process_event(event: Event) -> tuple[str | None, str | None, str | None]:
    author = event.author
    if event.content is None:
        return author, None, None

    # Skip final response since it overlaps with the previous event
    if author != "user" and event.is_final_response():
        return author, None, None

    response_markdown = ""
    thoughts_markdown = ""

    for part in event.content.parts:
        if part.thought:
            thoughts_markdown += part.text
        elif part.function_call:
            tool_name = part.function_call.name
            if tool_name == "exit_loop":
                response_markdown += "\n\n Task completed.\n"
            elif tool_name == "ask":
                response_markdown += "\n\n Ask user\n\n"
            else:
                tool_args = json.dumps(
                    part.function_call.args, indent=2, ensure_ascii=False
                )
                response_markdown += (
                    f"\n\n<details>\n<summary>Tool Call: {tool_name}</summary>\n\n"
                    f"```json\n{tool_args}\n```\n\n</details>\n\n"
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
                    lang = ""
            response_markdown += (
                f"\n\n<details>\n<summary>Tool Response: {tool_name}</summary>\n\n"
                f"```{lang}\n{tool_response}\n```\n\n</details>\n\n"
            )
        elif part.text:
            response_markdown += part.text

    return author, response_markdown or None, thoughts_markdown or None


def group_events(events: list[Event]) -> list[tuple[str, str, str, bool]]:
    grouped = []
    current_author = None
    current_response = ""
    current_thoughts = ""
    is_thinking = False

    for event in events:
        author, response, thoughts = process_event(event)
        if author is None:
            continue

        if current_author is None:
            current_author = author

        if author != current_author:
            grouped.append(
                (current_author, current_response, current_thoughts, is_thinking)
            )
            current_author = author
            current_response = ""
            current_thoughts = ""

        if response:
            current_response += response
            is_thinking = False
        if thoughts:
            current_thoughts += thoughts + "\n\n"
            is_thinking = True

    if current_author is not None:
        grouped.append(
            (current_author, current_response, current_thoughts, is_thinking)
        )
    return grouped


def init():
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    if "api_key" not in st.session_state:
        st.session_state.api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
            "GEMINI_API_KEY"
        )

    if "initial_prompt" not in st.session_state:
        st.session_state.initial_prompt = None

    if "conversation_session" not in st.session_state:
        st.session_state.conversation_session = {}


@st.fragment
def select_example():
    examples = load_examples()
    selected_title = st.selectbox(
        "Choose an example",
        sorted(list(examples.keys())) + ["<自分で問題を記入する>"],
    )

    if st.button("Use this Example Prompt", use_container_width=True):
        st.session_state.initial_prompt = examples.get(selected_title)
        if "worker" in st.session_state.conversation_session:
            st.session_state.conversation_session["worker"].stop()
        st.session_state.conversation_session = {}
        st.rerun()

    if selected_title and selected_title in examples.keys():
        example_content = examples[selected_title]
        with st.expander("Preview", expanded=True):
            st.markdown(example_content)


def main():
    init()

    st.set_page_config(page_title="ReMIP Example", page_icon="🎓")

    with st.sidebar:
        with st.expander("このデモについて", expanded=True):
            st.markdown(USAGE)

        if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get(
            "GEMINI_API_KEY"
        ):
            st.session_state.api_key = st.text_input(
                "[Gemini API Key](https://aistudio.google.com/app/api-keys)を取得して設定してください",
                type="password",
            )

        select_example()

    if not st.session_state.conversation_session:
        with st.form("Query"):
            prompt = st.text_area(
                "Query", value=st.session_state.initial_prompt, height=240
            )
            if st.form_submit_button("Submit") and prompt:
                # create conversation_session
                st.session_state.conversation_session = create_conversation_session(
                    prompt
                )
                st.rerun()
    else:
        conversation_session = st.session_state.conversation_session

        user_input = st.chat_input("Input your request")
        if user_input:
            conversation_session["worker"].add_message(
                Content(role="user", parts=[Part(text=user_input)])
            )

        for author, response, thoughts, is_thinking in group_events(
            conversation_session["worker"].get_event_history()
        ):
            with st.chat_message(name=author, avatar=AVATARS.get(author)):
                if thoughts and not is_thinking:
                    with st.expander("Thoughts", expanded=False):
                        st.markdown(thoughts, unsafe_allow_html=True)
                st.markdown(response, unsafe_allow_html=True)
                if is_thinking:
                    matches = re.findall(r"(\*\*.*?\*\*)", thoughts, flags=re.DOTALL)
                    thought_title = matches[-1] if matches else "Thinking..."
                    with st.expander(thought_title, expanded=False):
                        st.markdown(thoughts, unsafe_allow_html=True)

        st_autorefresh(interval=1000)


if __name__ == "__main__":
    main()
