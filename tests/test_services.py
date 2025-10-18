from unittest.mock import patch

from remip_example.services import get_mcp_toolset, get_session_service


@patch("remip_example.services.start_remip_mcp")
def test_get_mcp_toolset_is_singleton(mock_start_remip):
    """Tests that get_mcp_toolset returns the same instance every time."""
    mock_start_remip.return_value = 3333  # Mock the port

    print("\nTesting McpToolset singleton...")
    instance1 = get_mcp_toolset()
    instance2 = get_mcp_toolset()

    assert instance1 is instance2
    print("McpToolset singleton test passed.")


def test_get_session_service_is_singleton():
    """Tests that get_session_service returns the same instance every time."""
    print("\nTesting SessionService singleton...")
    instance1 = get_session_service()
    instance2 = get_session_service()

    assert instance1 is instance2
    print("SessionService singleton test passed.")
