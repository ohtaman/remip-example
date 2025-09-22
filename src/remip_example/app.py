import os
import pathlib
import uuid
import asyncio
import logging

import dotenv
from google.adk.events.event import Event
import streamlit as st

from google.genai import types
from google.genai import errors as google_errors
from google.adk.agents import Agent, LoopAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, BaseSessionService
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools import ToolContext, exit_loop
from remip_example.utils import start_remip_mcp, ensure_node
from remip_example.config import (
    APP_NAME,
    DEFAULT_INSTRUCTION,
    NORMAL_MAX_CALLS,
    SESSION_DB_URL,
    EXAMPLES_DIR,
)

dotenv.load_dotenv(override=True)


@st.cache_resource
def get_event_loop() -> asyncio.EventLoop:
    """Gets the asyncio event loop for the current session, creating it if necessary."""
    try:
        # Try to get the existing event loop for the main thread.
        return asyncio.get_event_loop()
    except RuntimeError:
        # If no loop exists for the main thread, create and set one.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


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
        ),
    )



def build_agent(
    api_key: str|None, is_agentic: bool = True, max_iterations: int = 10
) -> Agent:
    """Builds the agent with the specified configuration."""
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    def ask(tool_context: ToolContext):
        """Call this function when you need to ask to the user."""
        return exit_loop(tool_context)

    remip_agent = Agent(
        name="remip",
        model="gemini-2.5-pro",
        description="Agent for mathematical optimization",
        instruction="""You are a Methematical Optimization Professional. You interact with user and provide solutions using methematical optimization.

        ## Best Practice:
        """
        + DEFAULT_INSTRUCTION,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024*5,
            )
        ),
        tools=[get_mcp_toolset()],
        output_key="work_result",
    )

    if not is_agentic:
        return remip_agent

    judge_agent = Agent(
        name="judge",
        model="gemini-2.5-flash",
        description="Agent to judge whether to continue",
        instruction=f"""## Your Task

    Check the response of remip_agent and judge whether to continue.

    IF the response of remip_agent is just confirming to continue:
      Tell remip_agent to continue
    ELSE IF the response of remip_agent is asking to the user:
      IF it is really necessary to ask something to the user:
        Call ask tool
      ELSE
        Tell remip_agent to continue without asking anything to the user
    ELSE IF the user request is not related to the mathematical optimization:
      Tell remip_agent to continue
    ELSE IF the response of remip_agent satisfies the user's request:
      Tell remip_agent to continue
    ELSE IF you need to ask something to the user:
      Call ask tool
    ELSE  
      Provide specific suggestions to the remip_agent concisely. 
    
    **!!IMPORTANT!!** YOU CAN NOT USE ANY TOOLS EXCEPT exit OR ask TOOL.

    ## User Input

    ````
    {{{{user_input?}}}}
    ````

    ## Response


    ````
    {{{{work_result?}}}}
    ````
    """,
        planner=BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=1024,
            )
        ),
        tools=[exit_loop, ask],
        output_key="judge_result",
    )

    agent = LoopAgent(
        name="orchestrator",
        sub_agents=[remip_agent, judge_agent],
        max_iterations=max_iterations,
    )

    return agent


@st.dialog("GEMINI API KEY")
def get_api_key():
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Submit"):
        st.session_state.api_key = api_key
        st.rerun()

@st.cache_resource
def load_examples(language:str ="ja"):
    examples_dir: pathlib.Path = pathlib.Path(EXAMPLES_DIR) / language
    examples = {}
    for path in examples_dir.glob("*.md"):
        contents = open(path).readlines()
        if contents:
            examples[contents[0]] = "".join(contents)
    return examples

def process_event(event: Event) -> (str, str):
    response = ""
    thoughts = ""

    if event.content is None:
        return None, None

    for part in event.content.parts:
        markdown = getattr(part, "text") or ""
        tool_call = getattr(part, "function_call")
        tool_response = getattr(part, "function_response")

        if tool_call is not None and tool_call.name not in ("ask", "exit_loop"):
            markdown += f"\n\n<details>\n<summary>Call {tool_call.name}</summary><code>\n\n{tool_call}\n\n</code></details>\n\n"
        if tool_response is not None and tool_response.name not in ("ask", "exit_loop"):
            markdown += f"\n\n<details>\n<summary>Response {tool_response.name}</summary><code>\n\n{tool_response}\n\n</code></details>\n\n"
        if getattr(part, "thought", False):
            thoughts += markdown
        elif markdown:
            response += markdown
    return response, thoughts


async def stream_and_display_response(runner, talk_session, prompt, chat_container):
    """Streams the agent's response and displays it in the UI."""
    author = None
    container = None

    max_retries = 3
    retry_delay_seconds = 5

    for attempt in range(max_retries):
        try:
            with st.spinner("Thinking..."):
                with chat_container:
                    async for event in runner.run_async(
                        user_id=talk_session.user_id,
                        session_id=talk_session.id,
                        new_message=types.Content(
                            role="user", parts=[types.Part(text=prompt)]
                        ),
                        run_config=RunConfig(
                            streaming_mode=StreamingMode.SSE,
                            max_llm_calls=NORMAL_MAX_CALLS,
                        ),
                    ):
                        response, thoughts = process_event(event)

                        if author != event.author:
                            author = event.author
                            container = None
                            tmp_responses = []
                            final_responses = []
                        if not response:
                            continue

                        if container is None:
                            container = st.chat_message(author)
                            with container:
                                tmp_responses_pf = st.empty()
                                final_responses_pf = st.empty()

                        if response:
                            if event.is_final_response():
                                final_responses.append(response)
                                # final_responses_pf.markdown(
                                #     "".join(final_responses), unsafe_allow_html=True
                                # )
                            else:
                                if thoughts:
                                    tmp_responses.append(f"**Thoughts:**\n{thoughts}")
                                tmp_responses.append(response)
                                tmp_responses_pf.markdown(
                                    "".join(tmp_responses), unsafe_allow_html=True
                                )
            # If the loop completes without errors, break out of the retry loop
            break
        except google_errors.ServerError as e:
            if attempt < max_retries - 1:
                st.warning(
                    "Server error occurred. Retrying in"
                    f" {retry_delay_seconds} seconds... (Attempt {attempt + 1}/{max_retries})"
                )
                logging.warning(f"Caught ServerError: {e}. Retrying...")
                await asyncio.sleep(retry_delay_seconds)
            else:
                st.error(
                    "A server error occurred after multiple retries. Please try again later."
                )
                logging.error(f"Caught ServerError: {e}. Max retries reached.")
                # Do not re-raise, as it would crash the Streamlit app.
                break


def render_sidebar(is_in_talk_session: bool) -> None:
    """Renders the sidebar UI components."""
    with st.sidebar:
        language = st.selectbox(
            "Language",
            [("English", "en"), ("Japanese", "ja")],
            format_func=lambda m: m[0],
            disabled=is_in_talk_session,
        )
        examples = load_examples(language[1])
        example = st.selectbox(
            "Use Examples",
            options=["Do not use example"] + list(examples.keys()),
            disabled=is_in_talk_session,
        )

        if is_in_talk_session:
            if st.button("New Session", use_container_width=True):
                del st.session_state.session_id
                st.rerun()
    return example, examples


def render_new_session_view(loop: asyncio.EventLoop, example: str, examples: dict) -> None:
    """Renders the view for starting a new session."""
    with st.container():
        user_request = st.text_area(
            label="Input your request", value=examples.get(example, ""), height=280
        )
        if st.button("Submit", disabled=(not user_request.strip()), use_container_width=True):
            st.session_state.session_id = str(uuid.uuid4())

            loop.run_until_complete(
                get_session_service().create_session(
                    app_name=APP_NAME,
                    user_id=st.session_state.user_id,
                    session_id=st.session_state.session_id,
                    state={"user_request": user_request},
                )
            )

            st.session_state.agent = build_agent(
                api_key=st.session_state.api_key,
                max_iterations=10,
            )
            st.rerun()
    st.stop()


def render_chat_interface(loop: asyncio.EventLoop, talk_session) -> None:
    """Renders the main chat interface, including history and live responses."""
    user_request_container = st.container(height=280)
    chat_container = st.container()

    user_request = talk_session.state.get("user_request", "")
    with user_request_container:
        st.markdown(user_request)

    with chat_container:
        # Render chat history
        author = None
        for event in talk_session.events[1:]:
            response, _ = process_event(event)
            if response and not event.is_final_response():
                if author != event.author:
                    author = event.author
                    container = st.chat_message(author)
                container.markdown(response, unsafe_allow_html=True)


    agent = st.session_state.agent
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=get_session_service())

    # Handle new input and streaming response
    prompt = st.chat_input("Type your message")
    if prompt:
        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)
        loop.run_until_complete(
            stream_and_display_response(runner, talk_session, prompt, chat_container)
        )
    elif len(talk_session.events) == 0:
        # If no events, this is the first run after submitting the request.
        prompt = user_request
        loop.run_until_complete(
            stream_and_display_response(runner, talk_session, prompt, chat_container)
        )


def render_app() -> None:
    """The main entry point for the Streamlit application."""
    st.set_page_config(page_title="remip-example", layout="wide")
    loop = get_event_loop()

    # --- Initialization ---
    if "api_key" not in st.session_state:
        api_key = os.environ.get("GEMINI_API_KEY") or get_api_key()
        st.session_state.api_key = api_key

    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    import platform
    if platform.processor() == "":
        NODE_BIN_DIR = ensure_node()
        if str(NODE_BIN_DIR) not in os.environ["PATH"]:
            os.environ["PATH"] = os.pathsep.join(
                (str(NODE_BIN_DIR), os.environ.get("PATH", ""))
            )

    # --- UI Rendering ---
    is_in_talk_session = "session_id" in st.session_state
    example, examples = render_sidebar(is_in_talk_session)

    if not is_in_talk_session:
        render_new_session_view(loop, example, examples)
    else:
        talk_session = loop.run_until_complete(
            get_session_service().get_session(
                app_name=APP_NAME,
                user_id=st.session_state.user_id,
                session_id=st.session_state.session_id,
            )
        )
        render_chat_interface(loop, talk_session)


render_app()

