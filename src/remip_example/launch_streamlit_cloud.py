import os

import streamlit as st

from remip_example import app
from remip_example.utils import ensure_node, start_remip, start_remip_mcp


if __name__ == "__main__":
    NODE_BIN_DIR = ensure_node()
    if str(NODE_BIN_DIR) not in os.environ["PATH"]:
        os.environ["PATH"] = os.pathsep.join(
            (str(NODE_BIN_DIR), os.environ.get("PATH", ""))
        )

    # Hack to avoid error
    if "__streamlit_community_cloud_initialized__" not in st.session_state:
        start_remip()
        start_remip_mcp()
        st.session_state.__streamlit_community_cloud_initialized__ = True
        st.rerun()

    app.main()
