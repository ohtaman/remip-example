from types import SimpleNamespace
from remip_example.app import AVATARS, process_event


def create_mock_event(author: str, parts: list, is_final: bool = False):
    """Helper function to create a mock event object for testing."""
    mock = SimpleNamespace(
        author=author,
        content=SimpleNamespace(parts=parts),
        is_final_response=lambda: is_final,
    )
    return mock


def test_avatars_definition():
    """Test that AVATARS dictionary is correctly defined."""
    assert isinstance(AVATARS, dict)
    assert "remip_agent" in AVATARS
    assert "mentor_agent" in AVATARS
    assert "user" in AVATARS
    assert AVATARS["remip_agent"] == "🦸"
    assert AVATARS["mentor_agent"] == "🧚"
    assert AVATARS["user"] == "👤"


def test_process_event_with_text_content():
    """Test process_event with text content."""
    mock_part = SimpleNamespace(
        text="Hello", thought=None, function_call=None, function_response=None
    )
    mock_event = create_mock_event(author="user", parts=[mock_part])

    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "user"
    assert response_md == "Hello"
    assert thoughts_md is None


def test_process_event_with_function_call():
    """Test process_event with function call content."""
    mock_function_call = SimpleNamespace(name="test_function", args={"param": "value"})
    mock_part = SimpleNamespace(
        text=None,
        thought=None,
        function_call=mock_function_call,
        function_response=None,
    )
    mock_event = create_mock_event(author="remip_agent", parts=[mock_part])

    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "remip_agent"
    assert "Tool Call: test_function" in response_md
    assert '"param": "value"' in response_md
    assert thoughts_md is None


def test_process_event_with_function_response():
    """Test process_event with function response content."""
    mock_function_response = SimpleNamespace(
        name="test_function_response", response={"status": "success"}
    )
    mock_part = SimpleNamespace(
        text=None,
        thought=None,
        function_call=None,
        function_response=mock_function_response,
    )
    mock_event = create_mock_event(author="mentor_agent", parts=[mock_part])

    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "mentor_agent"
    assert "Tool Response: test_function_response" in response_md
    assert '"status": "success"' in response_md
    assert thoughts_md is None


def test_process_event_with_thought_content():
    """Test process_event with thought content."""
    mock_part = SimpleNamespace(
        text="Thinking...",
        thought="A thought",
        function_call=None,
        function_response=None,
    )
    mock_event = create_mock_event(author="remip_agent", parts=[mock_part])

    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "remip_agent"
    assert response_md is None
    assert thoughts_md == "Thinking..."


def test_process_event_skips_final_response():
    """Test that final responses from agents are skipped to avoid duplication."""
    mock_part = SimpleNamespace(
        text="Final Answer.", thought=None, function_call=None, function_response=None
    )
    mock_event = create_mock_event(
        author="remip_agent", parts=[mock_part], is_final=True
    )

    author, response_md, thoughts_md = process_event(mock_event)
    assert author == "remip_agent"
    assert response_md is None
    assert thoughts_md is None
