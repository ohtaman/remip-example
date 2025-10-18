# @pytest.fixture
# def mock_streamlit_session():
#     """Fixture to mock streamlit.session_state."""
#     with patch("remip_example.services.st") as mock_st:
#         mock_st.session_state = MagicMock()
#         yield mock_st

# @pytest.fixture
# def mock_session_service():
#     """Fixture to mock the DatabaseSessionService."""
#     with patch("remip_example.services.get_session_service") as mock_get_service:
#         mock_service = MagicMock()
#         mock_service.get_session = AsyncMock()
#         mock_service.delete_session = AsyncMock()
#         mock_service.create_session = AsyncMock()
#         mock_get_service.return_value = mock_service
#         yield mock_service

# @pytest.fixture
# def event_loop():
#     """Create an instance of the default event loop for each test case."""
#     loop = asyncio.get_event_loop_policy().new_event_loop()
#     yield loop
#     loop.close()

# @pytest.fixture(autouse=True)
# def patch_asyncio_run(event_loop):
#     """Patch asyncio.run to use the test event loop."""
#     def run_wrapper(coro):
#         return event_loop.run_until_complete(coro)

#     with patch("asyncio.run", new=run_wrapper):
#         yield

# def test_get_talk_session_exists(mock_streamlit_session, mock_session_service):
#     """Test get_talk_session when a session exists in the state."""
#     mock_streamlit_session.session_state.talk_session_info = TalkSessionInfo(
#         session_id="test_session", user_id="test_user"
#     )

#     get_talk_session()
#     mock_session_service.get_session.assert_called_once_with(
#         app_name="remip-example",
#         user_id="test_user",
#         session_id="test_session",
#     )

# def test_get_talk_session_not_exists(mock_streamlit_session, mock_session_service):
#     """Test get_talk_session when no session is in the state."""
#     # Ensure talk_session_info is not in the mock session_state
#     if "talk_session_info" in mock_streamlit_session.session_state:
#         del mock_streamlit_session.session_state.talk_session_info

#     session = get_talk_session()
#     assert session is None
#     mock_session_service.get_session.assert_not_called()

# def test_clear_talk_session(mock_streamlit_session, mock_session_service):
#     """Test that clear_talk_session deletes the session and clears the state."""
#     mock_streamlit_session.session_state.talk_session_info = TalkSessionInfo(
#         session_id="test_session", user_id="test_user"
#     )
#     # Mock the async_bridge
#     mock_streamlit_session.session_state.async_bridge = MagicMock()

#     clear_talk_session()
#     mock_session_service.delete_session.assert_called_once_with(
#         app_name="remip-example",
#         user_id="test_user",
#         session_id="test_session",
#     )
#     assert "talk_session_info" not in mock_streamlit_session.session_state

# def test_create_talk_session(mock_streamlit_session, mock_session_service):
#     """Test that create_talk_session creates a session and sets the state."""
#     mock_session = Session(id="new_session", app_name="remip-example", user_id="new_user", state={})
#     mock_session_service.create_session.return_value = mock_session

#     session = create_talk_session(user_id="new_user", session_id="new_session")
#     assert session == mock_session
#     mock_session_service.create_session.assert_called_once_with(
#         app_name="remip-example",
#         user_id="new_user",
#         session_id="new_session",
#         state={},
#     )
#     assert mock_streamlit_session.session_state.talk_session_info.session_id == "new_session"
#     assert mock_streamlit_session.session_state.talk_session_info.user_id == "new_user"
