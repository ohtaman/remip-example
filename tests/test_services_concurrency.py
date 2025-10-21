import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from google.adk.events.event import Event
from google.genai.types import Content, Part

from remip_example.services import AgentService


# A mock for the agent runner that lets us control timing
async def mock_run_async(*args, **kwargs):
    new_message = kwargs.get("new_message")
    if "first" in new_message.parts[0].text.lower():
        # First message stream
        yield Event(author="agent", content=Content(parts=[Part(text="first-part1")]))
        await asyncio.sleep(0.8)  # Slow task
        # This part should be cancelled and never appear in the output
        yield Event(author="agent", content=Content(parts=[Part(text="first-part2")]))
    else:
        # Second message stream
        yield Event(author="agent", content=Content(parts=[Part(text="second-part1")]))
        await asyncio.sleep(0.1)
        yield Event(author="agent", content=Content(parts=[Part(text="second-part2")]))


@pytest.mark.xfail(
    reason="This test is expected to fail until the concurrency logic is fixed."
)
@patch("remip_example.services.Runner.run_async", new=mock_run_async)
def test_second_message_interrupts_first_and_stream_is_correct():
    """
    Tests that sending a second message correctly interrupts the first,
    and that the output stream contains only the results from the second message.
    """
    service = AgentService()
    user_id = "test_user"
    session_id = service.create_talk_session(user_id)

    responses = []

    def consume_stream():
        for event in service.stream_new_responses(session_id):
            if event and event.content and event.content.parts:
                responses.append(event.content.parts[0].text)

    # Run the consumer in a background thread. It will block until the stream ends (with a SENTINEL).
    consumer_thread = threading.Thread(target=consume_stream, daemon=True)
    consumer_thread.start()

    # Send the first (slow) message
    service.add_message(user_id, session_id, "first message")
    time.sleep(0.2)  # Allow the first task to start and yield its first part

    # Send the second (fast) message, which should interrupt the first
    service.add_message(user_id, session_id, "second message")

    # Wait for the consumer thread to finish.
    # It should finish after the *second* task completes and puts its SENTINEL on the queue.
    consumer_thread.join(timeout=2.0)

    service.stop()

    # The desired behavior is that the first stream's partial output is kept,
    # and the second stream's output follows.
    assert (
        responses
        == [
            "first-part1",
            "second-part1",
            "second-part2",
        ]
    ), "The output should contain the partial first stream and the complete second stream"
