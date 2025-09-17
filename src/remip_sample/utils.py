"""Utility functions for the remip-sample application."""

import socket
import time
import subprocess
import atexit

import streamlit as st

from remip_sample.config import MCP_PORT

def wait_for_port(host: str, port: int, timeout: float = 10.0, interval: float = 0.1) -> bool:
    """Waits for the specified host:port to start listening."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(interval)
            result = sock.connect_ex((host, port))
            if result == 0:
                return True
        time.sleep(interval)
    return False

@st.cache_resource
def start_remip_mcp() -> int:
    """Starts the remip-mcp server as a subprocess and returns the port."""
    port = MCP_PORT
    proc = subprocess.Popen(
        f"npx -y github:ohtaman/remip-mcp --http --start-remip-server --port {port}",
        shell=True,
    )
    atexit.register(proc.terminate)
    wait_for_port("localhost", port)
    return port

def finalize_response(text: str) -> str:
    """Light post-processing for final rendering."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
