from unittest.mock import MagicMock
from remip_example.app import AVATARS, process_event
from google.adk.events.event import Event
from google.genai.types import Part, FunctionCall, FunctionResponse


def test_avatars_definition():
    """Test that AVATARS dictionary is correctly defined."""
    assert isinstance(AVATARS, dict)
    assert "remip_agent" in AVATARS
    assert "mentor_agent" in AVATARS
    assert "user" in AVATARS
    assert AVATARS["remip_agent"] == "🦸"
    assert AVATARS["mentor_agent"] == "🧚"
    assert AVATARS["user"] is None


def test_process_event_with_text_content():
    """Test process_event with text content."""
    mock_event = MagicMock(spec=Event)
    mock_event.author = "user"
    mock_event.content = MagicMock()
    mock_event.content.parts = [
        MagicMock(
            spec=Part,
            text="Hello",
            thought=None,
            function_call=None,
            function_response=None,
        )
    ]
    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "user"
    assert response_md == "Hello"
    assert thoughts_md is None


def test_process_event_with_function_call():
    """Test process_event with function call content."""
    mock_event = MagicMock(spec=Event)
    mock_event.author = "remip_agent"
    mock_event.content = MagicMock()
    mock_function_call = MagicMock(spec=FunctionCall)
    mock_function_call.name = "test_function"
    mock_function_call.args = {"param": "value"}
    mock_event.content.parts = [
        MagicMock(
            spec=Part,
            text=None,
            thought=None,
            function_call=mock_function_call,
            function_response=None,
        )
    ]
    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "remip_agent"
    assert "Tool Call: test_function" in response_md
    assert '"param": "value"' in response_md
    assert thoughts_md is None


def test_process_event_with_function_response():
    """Test process_event with function response content."""
    mock_event = MagicMock(spec=Event)
    mock_event.author = "mentor_agent"
    mock_event.content = MagicMock()
    mock_function_response = MagicMock(spec=FunctionResponse)
    mock_function_response.name = "test_function_response"
    mock_function_response.response = '{"status": "success"}'
    mock_event.content.parts = [
        MagicMock(
            spec=Part,
            text=None,
            thought=None,
            function_call=None,
            function_response=mock_function_response,
        )
    ]
    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "mentor_agent"
    assert "Tool Response: test_function_response" in response_md
    print(response_md)
    assert '\\"status\\": \\"success\\"' in response_md
    assert thoughts_md is None


def test_process_event_with_thought_content():
    """Test process_event with thought content."""
    mock_event = MagicMock(spec=Event)
    mock_event.author = "remip_agent"
    mock_event.content = MagicMock()
    mock_event.content.parts = [
        MagicMock(
            spec=Part,
            text="Thinking...",
            thought="Thinking...",
            function_call=None,
            function_response=None,
        )
    ]
    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "remip_agent"
    assert response_md is None
    assert thoughts_md == "Thinking..."
