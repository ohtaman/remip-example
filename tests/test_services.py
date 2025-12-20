"""Tests for the AgentService and related components."""

import pytest
from remip_example.services import AgentService, TalkSession


@pytest.fixture
def service():
    """Provides a fresh instance of AgentService for each test and stops it after."""
    service_instance = AgentService()
    yield service_instance
    service_instance.stop()


def test_create_talk_session_data_model():
    session = TalkSession(id="session1", user_id="user1")
    assert session.id == "session1"
    assert session.user_id == "user1"


def test_create_talk_session_for_user(service: AgentService):
    session_id = service.create_talk_session(user_id="user_a")
    session = service.get_talk_session(user_id="user_a", session_id=session_id)
    assert session.user_id == "user_a"


def test_list_sessions_is_user_specific(service: AgentService):
    session_a1 = service.create_talk_session(user_id="user_a")
    session_b1 = service.create_talk_session(user_id="user_b")
    user_a_sessions = service.list_talk_sessions(user_id="user_a")
    assert session_a1 in user_a_sessions
    assert session_b1 not in user_a_sessions


def test_get_session_security(service: AgentService):
    other_user_session_id = service.create_talk_session(user_id="user_b")
    with pytest.raises(KeyError):
        service.get_talk_session(user_id="user_a", session_id=other_user_session_id)
    with pytest.raises(KeyError):
        service.get_talk_session(user_id="user_a", session_id="non-existent-id")


def test_create_session_stores_agent_mode(service: AgentService):
    """Tests that the agent_mode is correctly stored in the TalkSession."""
    # Test with agent_mode=True
    session_id_true = service.create_talk_session(user_id="user1", is_agent_mode=True)
    session_true = service.get_talk_session(user_id="user1", session_id=session_id_true)
    assert session_true.agent_mode is True

    # Test with agent_mode=False
    session_id_false = service.create_talk_session(user_id="user1", is_agent_mode=False)
    session_false = service.get_talk_session(
        user_id="user1", session_id=session_id_false
    )
    assert session_false.agent_mode is False
