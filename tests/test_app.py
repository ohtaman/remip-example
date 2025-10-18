import json
from unittest.mock import MagicMock, patch
from google.adk.events.event import Event
from google.genai.types import Content, Part, FunctionCall, FunctionResponse
from remip_example.app import process_event, group_events, initialize_session

# Test List for process_event
# - Test case for an event with simple text.
# - Test case for an event with only thoughts.
# - Test case for an event with a tool call.
# - Test case for an event with a tool response.
# - Test case for a complex event containing text, thoughts, and a tool call.
# - Test case for an empty or None event content.

def test_process_event_with_simple_text():
    """Test case for an event with simple text."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[Part(text="Hello, world!")],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    assert response == "Hello, world!"
    assert thoughts is None


def test_process_event_with_only_thoughts():
    """Test case for an event with only thoughts."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[Part(text="This is a thought.", thought=True)],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    assert response is None
    assert thoughts == "This is a thought."


def test_process_event_with_tool_call():
    """Test case for an event with a tool call."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[
                Part(
                    function_call=FunctionCall(
                        name="my_tool", args={"arg1": "value1"}
                    )
                )
            ],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    tool_args = json.dumps({"arg1": "value1"}, indent=2, ensure_ascii=False)
    expected_html = (
        f'<details><summary>Tool Call: my_tool</summary>\n\n'
        f'```json\n{tool_args}\n```\n\n</details>'
    )
    assert response == expected_html
    assert thoughts is None


def test_process_event_with_tool_response():
    """Test case for an event with a tool response."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[
                Part(
                    function_response=FunctionResponse(
                        name="my_tool",
                        response={"result": "success"},
                    )
                )
            ],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    tool_response = json.dumps({"result": "success"}, indent=2, ensure_ascii=False)
    expected_html = (
        f'<details><summary>Tool Response: my_tool</summary>\n\n'
        f'```json\n{tool_response}\n```\n\n</details>'
    )
    assert response == expected_html
    assert thoughts is None


def test_process_event_complex():
    """Test case for a complex event with text, thoughts, and a tool call."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[
                Part(text="Thinking about what to do... ", thought=True),
                Part(text="Okay, I will use a tool.", thought=True),
                Part(
                    function_call=FunctionCall(
                        name="complex_tool", args={"param": "value"}
                    )
                ),
                Part(text="Here is the result."),
            ],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    expected_thoughts = "Thinking about what to do... Okay, I will use a tool."
    tool_args = json.dumps({"param": "value"}, indent=2, ensure_ascii=False)
    expected_response_html = (
        f'<details><summary>Tool Call: complex_tool</summary>\n\n'
        f'```json\n{tool_args}\n```\n\n</details>'
        'Here is the result.'
    )
    assert thoughts == expected_thoughts
    assert response == expected_response_html


def test_process_event_with_empty_content():
    """Test case for an event with empty content."""
    event = Event(
        author="assistant",
        content=Content(
            role="assistant",
            parts=[],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "assistant"
    assert response is None
    assert thoughts is None


def test_process_event_with_none_content():
        """Test case for an event with None content."""
        event = Event(
            author="assistant",
            content=None,
        )
        author, response, thoughts = process_event(event)
        assert author == "assistant"
        assert response is None
        assert thoughts is None


def test_process_event_with_unserializable_response():
    """Test case for a tool response that is not JSON serializable but contains parsable JSON."""
    # Mock objects to simulate the nested, non-serializable structure
    class MockTextContent:
        def __init__(self, text):
            self.text = text

    class MockCallToolResult:
        def __init__(self, content):
            self.content = content
        def __str__(self):
            return f"MockCallToolResult(content={self.content})"
        def __repr__(self):
            return self.__str__()

    summary_data = {"summary": {"status": "optimal", "objective_value": 139}}
    summary_json_str = json.dumps(summary_data)
    
    text_content = MockTextContent(text=summary_json_str)
    tool_result = MockCallToolResult(content=[text_content])

    event = Event(
        author="tool",
        content=Content(
            role="tool",
            parts=[
                Part(
                    function_response=FunctionResponse(
                        name="my_tool",
                        response={"result": tool_result},
                    )
                )
            ],
        ),
    )
    author, response, thoughts = process_event(event)
    assert author == "tool"
    
    # The expected output is the beautifully formatted JSON extracted from the mock object
    pretty_json = json.dumps(summary_data, indent=2, ensure_ascii=False)
    expected_html = (
        f'<details><summary>Tool Response: my_tool</summary>\n\n'
        f'```json\n{pretty_json}\n```\n\n</details>'
    )
    assert response == expected_html
    assert thoughts is None

# Test List for group_events
# - Test grouping of multiple events from the same author.
# - Test grouping of events from different authors.
# - Test with an empty list of events.
# - Test with events that have no author.

def test_group_events_same_author():
    """Test grouping of multiple events from the same author."""
    events = [
        Event(author="assistant", content=Content(parts=[Part(text="Hello.")])),
        Event(author="assistant", content=Content(parts=[Part(text=" How are you?")])),
    ]
    grouped = group_events(events)
    assert len(grouped) == 1
    assert grouped[0][0] == "assistant"
    assert grouped[0][1] == "Hello. How are you?"

def test_group_events_different_authors():
    """Test grouping of events from different authors."""
    events = [
        Event(author="user", content=Content(parts=[Part(text="Hi")])),
        Event(author="assistant", content=Content(parts=[Part(text="Hello.")])),
        Event(author="assistant", content=Content(parts=[Part(text=" How can I help?")])),
    ]
    grouped = group_events(events)
    assert len(grouped) == 2
    assert grouped[0][0] == "user"
    assert grouped[0][1] == "Hi"
    assert grouped[1][0] == "assistant"
    assert grouped[1][1] == "Hello. How can I help?"

def test_group_events_empty_list():
    """Test with an empty list of events."""
    assert group_events([]) == []

def test_group_events_no_author():
    """Test with events that have no author."""
    events = [
        Event(author="", content=Content(parts=[Part(text="Message 1")])),
        Event(author="assistant", content=Content(parts=[Part(text="Message 2")])),
    ]
    grouped = group_events(events)
    assert len(grouped) == 2
    assert grouped[0][0] == "unknown"
    assert grouped[0][1] == "Message 1"
    assert grouped[1][0] == "assistant"
    assert grouped[1][1] == "Message 2"

# Test List for initialize_session
# - Test that api_key and user_id are initialized in session_state.
# - Test that api_key is read from environment variables if available.
# - Test that api_key_dialog is called if api_key is not in env.

@patch("remip_example.app.os.environ.get")
@patch("remip_example.app.api_key_dialog")
def test_initialize_session(mock_api_key_dialog, mock_os_get):
    """Test that api_key and user_id are initialized in session_state."""
    mock_os_get.return_value = "test_api_key"
    
    with patch("remip_example.app.st") as mock_st:
        # Simulate an empty session state that allows attribute setting
        mock_st.session_state = MagicMock(spec=dict)
        mock_st.session_state.__contains__.side_effect = lambda item: item in mock_st.session_state.__dict__

        initialize_session()

        assert mock_st.session_state.api_key == "test_api_key"
        assert hasattr(mock_st.session_state, "user_id")
        assert hasattr(mock_st.session_state, "async_bridge")
        mock_api_key_dialog.assert_not_called()

@patch("remip_example.app.os.environ.get")
@patch("remip_example.app.api_key_dialog")
def test_initialize_session_calls_dialog(mock_api_key_dialog, mock_os_get):
    """Test that api_key_dialog is called if api_key is not in env."""
    mock_os_get.return_value = None
    mock_api_key_dialog.return_value = "dialog_api_key"

    with patch("remip_example.app.st") as mock_st:
        mock_st.session_state = MagicMock(spec=dict)
        mock_st.session_state.__contains__.side_effect = lambda item: item in mock_st.session_state.__dict__
        
        initialize_session()

        assert mock_st.session_state.api_key == "dialog_api_key"
        mock_api_key_dialog.assert_called_once()