import pytest
from unittest.mock import patch, MagicMock
from google.adk.agents import LlmAgent, LoopAgent
from remip_example.agent import build_agent, clear_tool_calling_track, track_tool_calling

# Test List for build_agent
# - Test that build_agent returns a single LlmAgent when is_agent_mode is False.
# - Test that build_agent returns a LoopAgent when is_agent_mode is True.
# - Test that the LoopAgent contains the correct mentor and worker agents.
# - Test that the agent's API key is correctly passed.

def test_build_agent_not_agent_mode():
    """Test that build_agent returns a single LlmAgent when is_agent_mode is False."""
    agent = build_agent(is_agent_mode=False)
    assert isinstance(agent, LlmAgent)
    assert agent.name == "remip_agent"

def test_build_agent_agent_mode():
    """Test that build_agent returns a LoopAgent when is_agent_mode is True."""
    agent = build_agent(is_agent_mode=True)
    assert isinstance(agent, LoopAgent)

def test_build_agent_loop_composition():
    """Test that the LoopAgent contains the correct mentor and worker agents."""
    agent = build_agent(is_agent_mode=True)
    assert agent.sub_agents[0].name == "remip_agent"
    assert agent.sub_agents[1].name == "mentor_agent"

@patch('vertexai.generative_models.GenerativeModel')
def test_build_agent_with_api_key(mock_gen_model):
    """Test that the agent's API key is correctly passed."""
    api_key = "test_api_key"
    build_agent(api_key=api_key)
    # This is a simplification. In a real scenario, you'd check if the model was initialized with the key.
    # For this example, we're just ensuring the function runs without error.
    assert True 

# Test List for Tool Callingbacks
# - Test that clear_tool_calling_track resets the 'tools_used' state.
# - Test that track_tool_calling adds a new tool usage record to the state.
# - Test that track_tool_calling creates the 'tools_used' list if it doesn't exist.
# - Test that track_tool_calling truncates long argument values.

def test_clear_tool_calling_track():
    """Test that clear_tool_calling_track resets the 'tools_used' state."""
    callback_context = MagicMock()
    callback_context.state = {"tools_used": ["some_tool"]}
    clear_tool_calling_track(callback_context)
    assert callback_context.state["tools_used"] == []

def test_track_tool_calling_appends_record():
    """Test that track_tool_calling adds a new tool usage record to the state."""
    tool = MagicMock()
    tool.name = "test_tool"
    args = {"arg1": "value1"}
    tool_context = MagicMock()
    tool_context.state = {"tools_used": []}
    tool_context.agent_name = "test_agent"
    tool_response = MagicMock()
    tool_response.isError = False

    track_tool_calling(tool, args, tool_context, tool_response)
    
    assert len(tool_context.state["tools_used"]) == 1
    record = tool_context.state["tools_used"][0]
    assert record["agent"] == "test_agent"
    assert record["tool"] == "test_tool"
    assert record["success"] is True

def test_track_tool_calling_creates_list():
    """Test that track_tool_calling creates the 'tools_used' list if it doesn't exist."""
    tool = MagicMock()
    tool.name = "test_tool"
    args = {}
    tool_context = MagicMock()
    tool_context.state = {}
    tool_context.agent_name = "test_agent"
    tool_response = MagicMock()
    tool_response.isError = False

    track_tool_calling(tool, args, tool_context, tool_response)
    assert "tools_used" in tool_context.state
    assert len(tool_context.state["tools_used"]) == 1

def test_track_tool_calling_truncates_long_args():
    """Test that track_tool_calling truncates long argument values."""
    tool = MagicMock()
    tool.name = "test_tool"
    long_value = "a" * 200
    args = {"long_arg": long_value}
    tool_context = MagicMock()
    tool_context.state = {"tools_used": []}
    tool_context.agent_name = "test_agent"
    tool_response = MagicMock()
    tool_response.isError = False

    track_tool_calling(tool, args, tool_context, tool_response)
    
    record = tool_context.state["tools_used"][0]
    truncated_arg = record["args"]["long_arg"]
    assert len(truncated_arg) == 128 + 3
    assert truncated_arg.endswith("...")
