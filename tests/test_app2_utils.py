from google.adk.events.event import Event
from google.genai.types import Content, Part, FunctionCall, FunctionResponse
from remip_example.app2 import process_event

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
    expected_html = (
        '<details><summary>Tool Call: my_tool</summary>'
        '<pre>{\n  "arg1": "value1"\n}</pre></details>'
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
    expected_html = (
        '<details><summary>Tool Response: my_tool</summary>'
        '<pre>{\n  "result": "success"\n}</pre></details>'
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
    expected_response_html = (
        '<details><summary>Tool Call: complex_tool</summary>'
        '<pre>{\n  "param": "value"\n}</pre></details>'
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