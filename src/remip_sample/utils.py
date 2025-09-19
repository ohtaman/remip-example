"""Utility functions for the remip-sample application."""

import os
import shutil
import tarfile
import urllib.request
import pathlib
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

def ensure_node(version: str = "24.8.0", install_dir: str = ".node") -> str:
    base_path = pathlib.Path.cwd() / install_dir
    node_path = base_path / f"node-v{version}-linux-x64"
    bin_path = node_path / "bin"
    node_path = bin_path / "node"

    if not node_path.exists():
        base_path.mkdir(parents=True, exist_ok=True)
        url = f"https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.gz"
        tarball_path = base_path / f"node-v{version}-linux-x64.tar.gz"
        with urllib.request.urlopen(url) as r:
            with open(tarball_path, "wb") as f:
                f.write(r.read())
        with tarfile.open(tarball_path, "r:gz") as f:
            f.extractall(base_path)

    return bin_path
