import asyncio
import threading
import time
from unittest.mock import patch

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


# @pytest.mark.xfail(
#     reason="This test is expected to fail until the concurrency logic is fixed."
# )
@patch("remip_example.services.Runner.run_async", new=mock_run_async)
def test_second_message_interrupts_first_and_stream_is_correct():
    """
    Tests that a second message interrupts the first stream and that subsequent
    calls to stream responses return the correct chunks.
    """
    service = AgentService()
    user_id = "test_user"
    session_id = service.create_talk_session(user_id)

    first_stream_responses = []
    first_stream_finished = threading.Event()
    second_thread = None

    def consume_stream():
        try:
            for event in service.stream_new_responses(session_id):
                if event and event.content and event.content.parts:
                    first_stream_responses.append(event.content.parts[0].text)
        finally:
            first_stream_finished.set()

    # Run the consumer in a background thread. It will block until the stream ends (with a SENTINEL).
    consumer_thread = threading.Thread(target=consume_stream, daemon=True)
    consumer_thread.start()

    try:
        # Send the first (slow) message
        service.add_message(user_id, session_id, "first message")

        # Wait until the first chunk arrives to ensure the first stream has started.
        deadline = time.time() + 2.0
        while not first_stream_responses and time.time() < deadline:
            time.sleep(0.01)
        assert (
            first_stream_responses
        ), "First stream never yielded the initial chunk in time."

        # Send the second (fast) message, which should interrupt the first
        service.add_message(user_id, session_id, "second message")

        # The first stream should finish quickly after the second message arrives.
        assert first_stream_finished.wait(
            timeout=2.0
        ), "First stream did not terminate after the second message arrived."

        # Consume the second stream separately (matching how the service is intended to be used)
        second_stream_responses = []
        second_stream_finished = threading.Event()

        def consume_second_stream():
            try:
                for event in service.stream_new_responses(session_id):
                    if event and event.content and event.content.parts:
                        second_stream_responses.append(event.content.parts[0].text)
            finally:
                second_stream_finished.set()

        second_thread = threading.Thread(target=consume_second_stream, daemon=True)
        second_thread.start()

        assert second_stream_finished.wait(
            timeout=2.0
        ), "Second stream did not complete as expected."

        assert first_stream_responses == ["first-part1"]
        assert second_stream_responses == ["second-part1", "second-part2"]
    finally:
        service.stop()
        consumer_thread.join(timeout=0.1)
        if second_thread is not None:
            second_thread.join(timeout=0.1)
