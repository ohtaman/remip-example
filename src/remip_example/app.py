"""The main Streamlit application for the remip-example."""

import os
import sys
import time
import uuid

import streamlit as st

# Add the project root to the Python path to ensure modules are found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from remip_example.services import AgentService


@st.cache_resource
def get_agent_service() -> AgentService:
    """Creates and returns a singleton instance of the AgentService."""
    return AgentService()


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

    if st.sidebar.button("New Talk"):
        session_id = agent_service.create_talk_session(
            user_id=user_id, is_agent_mode=is_agent_mode
        )
        st.session_state.selected_session_id = session_id
        st.rerun()

    st.sidebar.write("--- Existing Talks ---")
    session_ids = agent_service.list_sessions(user_id=user_id)
    for session_id in session_ids:
        label = f"Session {session_id[:8]}..."
        if st.sidebar.button(label, key=f"session_btn_{session_id}"):
            st.session_state.selected_session_id = session_id
            st.rerun()

    # --- Chat Interface ---
    if "selected_session_id" in st.session_state:
        selected_session_id = st.session_state.selected_session_id
        st.header(f"Session: {selected_session_id}")

        messages = agent_service.get_messages(
            user_id=user_id, session_id=selected_session_id
        )
        for msg in messages:
            with st.chat_message(msg.sender):
                st.markdown(msg.content)

        if prompt := st.chat_input("What is up?"):
            agent_service.add_message(
                user_id=user_id, session_id=selected_session_id, message_content=prompt
            )
            st.rerun()

        # --- Step 2.4: Streaming Responses (Polling) ---
        # If the last message is from the user, poll for the agent's response.
        if len(messages) > 0 and messages[-1].sender == "user":
            time.sleep(0.5)  # Wait half a second before checking for a response
            st.rerun()

    else:
        st.info("Select a talk from the sidebar or create a new one.")


if __name__ == "__main__":
    main()
