import unittest
from unittest.mock import patch

# This import will fail, which is the purpose of the "Red" step in TDD
from remip_sample.services import get_mcp_toolset, get_session_service

class TestServices(unittest.TestCase):
    """Tests for the singleton services."""

    @patch("remip_sample.services.start_remip_mcp")
    def test_get_mcp_toolset_is_singleton(self, mock_start_remip):
        """Tests that get_mcp_toolset returns the same instance every time."""
        mock_start_remip.return_value = 3333  # Mock the port

        print("\nTesting McpToolset singleton...")
        instance1 = get_mcp_toolset()
        instance2 = get_mcp_toolset()

        self.assertIs(instance1, instance2, "get_mcp_toolset should return the same instance")
        print("McpToolset singleton test passed.")

    def test_get_session_service_is_singleton(self):
        """Tests that get_session_service returns the same instance every time."""
        print("\nTesting SessionService singleton...")
        instance1 = get_session_service()
        instance2 = get_session_service()

        self.assertIs(instance1, instance2, "get_session_service should return the same instance")
        print("SessionService singleton test passed.")

if __name__ == "__main__":
    unittest.main()
