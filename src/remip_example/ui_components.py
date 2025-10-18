"""UI component helper functions for the Streamlit app."""

import pathlib
import streamlit as st
from remip_example.config import EXAMPLES_DIR


@st.cache_resource
def load_examples(language: str = "ja"):
    """Loads example prompts from the specified language directory."""
    examples_dir: pathlib.Path = pathlib.Path(EXAMPLES_DIR) / language
    examples = {}
    for path in examples_dir.glob("*.md"):
        with open(path, "r", encoding="utf-8") as f:
            contents = f.readlines()
        if contents:
            # Use the first line (title) as the key
            examples[contents[0].strip()] = "".join(contents)
    return examples


def settings_form():
    """Renders the settings form in the sidebar."""
    with st.form("Settings", border=False):
        with st.expander("Settings"):
            language = st.selectbox("Language", ["ja", "en"])
            is_agent_mode = st.toggle("Agent Mode", value=True)
            st.form_submit_button("Submit", use_container_width=True)
    return language, is_agent_mode


@st.dialog("GEMINI API KEY")
def api_key_dialog() -> str | None:
    """Renders a dialog to input the Gemini API Key."""
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Submit"):
        return api_key
    return None


def new_session_form(example: str | None):
    """Renders the form to start a new session with a user request."""
    with st.form("new_session"):
        user_request = st.text_area(
            label="Input your request", value=example, height=280
        )
        if st.form_submit_button("Submit", use_container_width=True):
            return user_request
    return None
