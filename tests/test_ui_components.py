from unittest.mock import patch, mock_open, MagicMock
from pathlib import Path
from remip_example.ui_components import load_examples, settings_form

# Test List for load_examples
# - Test that it loads examples from a directory.
# - Test that it handles an empty directory.
# - Test that it correctly parses the title and content.

@patch("pathlib.Path.glob")
@patch("builtins.open", new_callable=mock_open, read_data="Title\nContent")
def test_load_examples(mock_file, mock_glob):
    """Test that it loads examples from a directory."""
    mock_glob.return_value = [Path("examples/ja/example1.md")]
    examples = load_examples("ja")
    assert "Title" in examples
    assert examples["Title"] == "Title\nContent"

@patch("pathlib.Path.glob")
def test_load_examples_empty_dir(mock_glob):
    """Test that it handles an empty directory."""
    mock_glob.return_value = []
    examples = load_examples("en")
    assert examples == {}

# Test List for settings_form
# - Test that it returns the values from the streamlit widgets.

def test_settings_form():
    """Test that it returns the values from the streamlit widgets."""
    mock_st = MagicMock()
    mock_st.selectbox.return_value = "en"
    mock_st.toggle.return_value = False

    with patch("remip_example.ui_components.st", mock_st):
        language, is_agent_mode = settings_form()

    assert language == "en"
    assert is_agent_mode is False
    mock_st.selectbox.assert_called_once_with("Language", ["ja", "en"])
    mock_st.toggle.assert_called_once_with("Agent Mode", value=True)

