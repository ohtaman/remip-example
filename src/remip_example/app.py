"""The main Streamlit application for the remip-example."""

import json
import time
import uuid

from pydantic import Json, TypeAdapter
import streamlit as st
from google.adk.events.event import Event

from remip_example.services import AgentService


AVATARS = {"remip_agent": "🦸", "mentor_agent": "🧚", "user": None}


def get_agent_service() -> AgentService:
    """Gets the AgentService instance for the current streamlit session."""
    if "agent_service" not in st.session_state:
        st.session_state.agent_service = AgentService()
    return st.session_state.agent_service


def main():
    """Main function to run the Streamlit app."""
    st.title("Remip Agent Example")

    agent_service = get_agent_service()

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    user_id = st.session_state.user_id

    # --- Sidebar ---
    st.sidebar.title("Conversations")
    st.sidebar.write(f"User ID: `{user_id}`")

    is_agent_mode = st.sidebar.checkbox("Use Agent Mode (LoopAgent)", value=True)

    if st.sidebar.button("New Talk", type="primary"):
        session_id = agent_service.create_talk_session(
            user_id=user_id, is_agent_mode=is_agent_mode
        )
        st.session_state.selected_session_id = session_id
        st.rerun()

    st.sidebar.write("--- Existing Talks ---")
    session_ids = agent_service.list_talk_sessions(user_id=user_id)
    for session_id in session_ids:
        label = f"Session {session_id[:8]}..."
        if st.sidebar.button(label, key=f"session_btn_{session_id}"):
            st.session_state.selected_session_id = session_id
            st.rerun()

    # --- Chat Interface ---
    if "selected_session_id" not in st.session_state:
        st.info("Select a talk from the sidebar or create a new one.")
        st.stop()

    selected_session_id = st.session_state.selected_session_id
    st.header(f"Session: {selected_session_id}")

    if prompt := st.chat_input("Input your request"):
        agent_service.add_message(
            user_id=user_id, session_id=selected_session_id, message_content=prompt
        )
        time.sleep(1)
        st.rerun()

    def generate_events():
        for event in agent_service.get_historical_events(
            user_id=user_id, session_id=selected_session_id
        ):
            yield event

        if agent_service.is_task_running(selected_session_id):
            for event in agent_service.stream_new_responses(selected_session_id):
                yield event

    for event in generate_events():
        author, text_chunk, _ = process_event(event)
        with st.chat_message(author, avatar=AVATARS.get(author)):
            st.markdown(text_chunk, unsafe_allow_html=True)


def process_event(event: Event) -> tuple[str | None, str | None, str | None]:
    """Processes an agent Event and formats its content for display."""
    author = event.author
    if not event.content:
        return author, None, None

    response_md, thoughts_md = "", ""
    for part in event.content.parts:
        if part.thought:
            thoughts_md += part.text or ""
        elif part.function_call:
            args = json.dumps(part.function_call.args, indent=2, ensure_ascii=False)
            response_md += f"\n\n<details>\n<summary>\nTool Call: {part.function_call.name}\n</summary>\n\n```json\n{args}\n```\n\n</details>\n\n"
        elif part.function_response:
            response_adapter = TypeAdapter(Json)
            try:
                response = response_adapter.validate_python(
                    part.function_response.response
                )
                response_str = json.dumps(response, indent=2)
            except Exception:
                response_str = str(part.function_response.response)
            response_md += f"\n\n<details>\n<summary>\nTool Response: {part.function_response.name}\n</summary>\n\n```json\n{response_str}\n```\n\n</details>\n\n"
        elif part.text:
            response_md += part.text
    return author, response_md or None, thoughts_md or None


if __name__ == "__main__":
    main()
