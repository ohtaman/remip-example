import os
import uuid
import asyncio
from typing import Optional, List

import nest_asyncio

nest_asyncio.apply()

import dotenv
import streamlit as st

from google.genai import types
from google.adk.agents import Agent, LoopAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, BaseSessionService
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools import ToolContext, exit_loop

from remip_sample.config import (
    APP_NAME,
    DEFAULT_INSTRUCTION,
    NORMAL_MAX_CALLS,
    AUTON_MAX_CALLS,
    SESSION_DB_URL,
)
from remip_sample.utils import start_remip_mcp, finalize_response, ensure_node

dotenv.load_dotenv(override=True)

@st.cache_resource
def get_event_loop() -> asyncio.EventLoop:
    return asyncio.get_event_loop()

@st.cache_resource
def get_session_service() -> BaseSessionService:
    """Create and cache a singleton instance of the session service."""
    return DatabaseSessionService(SESSION_DB_URL)

@st.cache_resource
def get_mcp_toolset() -> McpToolset:
    """Start the MCP server and create and cache a singleton instance of the toolset."""
    port = start_remip_mcp()
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://localhost:{port}/mcp/",
            timeout=30,
            terminate_on_close=False,
        )
    )

def build_default_agent(api_key: Optional[str], autonomous: bool, max_iterations_hint: int) -> Agent:
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key


def build_agent(api_key: Optional[str], autonomous: bool, max_iterations_hint: int) -> Agent:
    """Builds the agent with the specified configuration."""
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    instruction_autonomous = (
        "You are a decisive, autonomous agent. Do NOT ask for confirmation unless safety is at risk. "
        "Plan briefly, then act with small, reversible steps. If an attempt fails, analyze the error and try a fix. "
        f"Stop when the objective is satisfied and output '<DONE/>' at the end. "
        f"You may take up to roughly {max_iterations_hint} steps if necessary."
    )

    return Agent(
        name="remip_agent",
        model="gemini-2.5-pro",
        description="Agent for mathematical optimization",
        instruction=instruction_autonomous, # if autonomous else DEFAULT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[get_mcp_toolset()],
    )

def build_loop_agent(api_key: Optional[str], autonomous: bool, max_iterations_hint: int) -> Agent:
    """Builds the agent with the specified configuration."""
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key
    
    def ask(tool_context: ToolContext):
        """Call this function when you need to ask to the user."""
        return exit_loop(tool_context)
    
    worker_agent = Agent(
        name="remip_agent",
        model="gemini-2.5-pro",
        description="Agent for mathematical optimization",
        instruction="""You are a Methematical Optimization Professional. You interact with user and provide solutions using methematical optimization.
        You can ask user ONLY by using ask tool.

        ## User request:

        ````
        {{user_input?}}
        ````

        ## Best Practice:
        """
        + DEFAULT_INSTRUCTION + 
        f"""

        ## Review comment for implovement (If exists.)

        {{review_result?}}
        """,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[get_mcp_toolset(), ask],
        output_key="work_result" 
    )

    judge_agent = Agent(
        name="judge",
        model="gemini-2.5-flash",
        description="Agent to judge whether to re-execute",
        instruction="""## Your Task

    Check the response of remip_agent and  judge whether to re-execute.

    IF the user request is not related to the mathematical optimization:
      Call exit_loop tool
    ELSE IF the result satisfies the user's request:
      Call exit_loop tool
    ELSE IF the result is asking to the user:
      Call ask tool
    ELSE IF :  
      Provide specific suggestions concisely. 

    ## User Input

    ````
    {{user_input?}}
    ````
    """,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[exit_loop, ask],
        output_key="review_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[worker_agent, judge_agent],  
        max_iterations=10
    )

    return agent


def render_past_transcript(adk_session: object) -> None:
    """Renders the past events from a session into the chat history."""
    if not adk_session:
        return
    for event in adk_session.events:
        if not event.content:
            continue
        role = event.content.role
        parts = getattr(event.content, "parts", None) or []
        visible_chunks: List[str] = []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                visible_chunks.append(text)
        if visible_chunks:
            with st.chat_message(role):
                st.markdown("\n\n".join(visible_chunks), unsafe_allow_html=True)


@st.dialog("GEMINI API KEY")
def get_api_key():
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Submit"):
        st.session_state.api_key = api_key
        st.rerun()


async def main() -> None:
    """The main entry point for the Streamlit application."""
    st.set_page_config(page_title="remip-sample", layout="wide")

    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or get_api_key()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    st.write(os.getenv("STREAMLIT_RUNTIME"))
    if os.getenv("STREAMLIT_RUNTIME") == "1":
        NODE_BIN_DIR = ensure_node()
        os.environ["PATH"] = NODE_BIN_DIR + os.pathsep + os.environ.get("PATH", "")

    import subprocess
    subprocess.run("node --version", shell=True)
    subprocess.run("npm --version", shell=True)
    subprocess.run("npx --version", shell=True)


    with st.sidebar:
        with st.form("settings",  border=False):
            with st.expander("Settings", expanded=True):
                language = st.selectbox("Language", ["English", "Chinese", "Japanese"])
                autonomous_mode = st.toggle("Agent mode", value=False)
                if st.form_submit_button("New Session", use_container_width=True):
                    st.session_state.session_id = str(uuid.uuid4())
                    await get_session_service().create_session(
                        app_name=APP_NAME,
                        user_id=st.session_state.user_id,
                        session_id=st.session_state.session_id,
                    )
                    st.session_state.agent = build_loop_agent(
                        api_key=st.session_state.api_key,
                        autonomous=autonomous_mode,
                        max_iterations_hint=1000, # AUTON_MAX_CALLS if autonomous_mode else NORMAL_MAX_CALLS,
                    )
                    st.rerun()

    if "session_id" not in st.session_state:
        st.info("Click **New Session** in the sidebar to start.")
        st.stop()

    # --- Main chat interface ---
    adk_session = await get_session_service().get_session(
        app_name=APP_NAME,
        user_id=st.session_state.user_id,
        session_id=st.session_state.session_id,
    )
    adk_session.state["review_result"] = ""

    st.write(adk_session.events)
    render_past_transcript(adk_session)

    agent = st.session_state.agent
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=get_session_service())

    prompt = st.chat_input("Type your message")
    if not prompt:
        return

    with st.chat_message("user"):
        st.markdown(prompt)

    adk_session.state["user_input"] = prompt

    with st.chat_message("model"):

        thought_pf = st.empty()
        answer_pf = st.empty()
        thought_buffer: list[str] = []
        answer_buffer: list[str] = []
        final_answer_buffer: list[str] = []
        content = types.Content(role="user", parts=[types.Part(text=prompt)])

        async for event in runner.run_async(
            user_id=adk_session.user_id,
            session_id=adk_session.id,
            new_message=content,
            run_config=RunConfig(
                streaming_mode=StreamingMode.SSE,
                max_llm_calls=NORMAL_MAX_CALLS,
            ),
        ):
            if event.content is None:
                continue

            for part in event.content.parts:
                text = getattr(part, "text") or ""
                func_call = getattr(part, "function_call")
                func_response = getattr(part, "function_response")
                if func_call is not None:
                    text += f"\n\n<details>\n<summary>Call {func_call.name}</summary><code>\n\n{func_call}\n\n</code></details>\n\n"
                if func_response is not None:
                    text += f"\n\n<details>\n<summary>Response {func_response.name}</summary><code>\n\n{func_response}\n\n</code></details>\n\n"
                if getattr(part, "thought", True):
                    thought_buffer.append(part.text)
                    with thought_pf.expander("Thought"):
                        st.markdown("".join(thought_buffer), unsafe_allow_html=True)
                elif text:
                    if event.is_final_response():
                        final_answer_buffer.append(text)
                        answer_buffer = final_answer_buffer.copy()
                        answer_pf.markdown("".join(final_answer_buffer), unsafe_allow_html=True)
                    else:
                        answer_buffer.append(text)
                        answer_pf.markdown("".join(answer_buffer), unsafe_allow_html=True)


if __name__ == "__main__":
    loop = get_event_loop()
    loop.run_until_complete(main())

