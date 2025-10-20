"""Tests for the AgentService and related components."""

import time
from queue import Queue

import pytest
from remip_example.services import AgentService, Message, TalkSession


@pytest.fixture
def service():
    """Provides a fresh instance of AgentService for each test and stops it after."""
    service_instance = AgentService()
    yield service_instance
    service_instance.stop()


def test_create_message():
    message = Message(content="Hello!", sender="user")
    assert message.content == "Hello!"


def test_create_talk_session_data_model():
    session = TalkSession(id="session1", user_id="user1", messages=[])
    assert session.id == "session1"
    assert session.user_id == "user1"


def test_create_talk_session_for_user(service: AgentService):
    session_id = service.create_talk_session(user_id="user_a")
    session = service.get_session(user_id="user_a", session_id=session_id)
    assert session.user_id == "user_a"


def test_list_sessions_is_user_specific(service: AgentService):
    session_a1 = service.create_talk_session(user_id="user_a")
    session_b1 = service.create_talk_session(user_id="user_b")
    user_a_sessions = service.list_sessions(user_id="user_a")
    assert session_a1 in user_a_sessions
    assert session_b1 not in user_a_sessions


def test_get_session_security(service: AgentService):
    other_user_session_id = service.create_talk_session(user_id="user_b")
    with pytest.raises(KeyError):
        service.get_session(user_id="user_a", session_id=other_user_session_id)
    with pytest.raises(KeyError):
        service.get_session(user_id="user_a", session_id="non-existent-id")


def test_create_session_initializes_queues(service: AgentService):
    session_id = service.create_talk_session(user_id="user1")
    assert isinstance(service._input_queues[session_id], Queue)
    assert isinstance(service._output_queues[session_id], Queue)


def test_add_message_adds_to_history(service: AgentService):
    session_id = service.create_talk_session(user_id="user1")
    service.add_message(
        user_id="user1", session_id=session_id, message_content="Hello, agent!"
    )
    session = service.get_session(user_id="user1", session_id=session_id)
    assert len(session.messages) == 1


def test_add_message_puts_task_on_queue(service: AgentService):
    session_id = service.create_talk_session(user_id="user1")
    service.add_message(
        user_id="user1", session_id=session_id, message_content="Test message"
    )
    input_queue = service._input_queues[session_id]
    assert not input_queue.empty()


def test_get_messages_returns_history(service: AgentService):
    session_id = service.create_talk_session(user_id="user1")
    service.add_message(
        user_id="user1", session_id=session_id, message_content="Message 1"
    )
    # Give worker time to run
    time.sleep(0.5)
    messages = service.get_messages(user_id="user1", session_id=session_id)
    # Should have user message + agent response
    assert len(messages) >= 1


def test_get_messages_retrieves_from_output_queue(service: AgentService):
    session_id = service.create_talk_session(user_id="user1")
    output_queue = service._output_queues[session_id]
    agent_message = Message(content="Agent response", sender="agent")
    output_queue.put(agent_message)
    messages = service.get_messages(user_id="user1", session_id=session_id)
    assert len(messages) == 1
    assert messages[0].content == "Agent response"

    def test_worker_processes_message(service: AgentService):
        session_id = service.create_talk_session(user_id="user1")
        service.add_message(
            user_id="user1",
            session_id=session_id,
            message_content="I want to assign shifts fairly",
        )

        # Wait long enough for the worker to almost certainly run
        time.sleep(5)  # Give it more time for a complex request

        output_queue = service._output_queues[session_id]
        assert not output_queue.empty()
        response_message = output_queue.get_nowait()
        assert response_message.sender == "agent"
        assert isinstance(response_message.content, str)
        assert len(response_message.content) > 0
        assert "Error:" not in response_message.content


def test_create_session_stores_agent_mode(service: AgentService):
    """Tests that the agent_mode is correctly stored in the TalkSession."""
    # Test with agent_mode=True
    session_id_true = service.create_talk_session(user_id="user1", is_agent_mode=True)
    session_true = service.get_session(user_id="user1", session_id=session_id_true)
    assert session_true.agent_mode is True

    # Test with agent_mode=False
    session_id_false = service.create_talk_session(user_id="user1", is_agent_mode=False)
    session_false = service.get_session(user_id="user1", session_id=session_id_false)
    assert session_false.agent_mode is False
