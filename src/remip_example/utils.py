"""Utility functions for the remip-sample application."""

import tarfile
import urllib.request
import pathlib
import socket
import time
import subprocess
import atexit
import os
import signal

import streamlit as st

from remip_example.config import MCP_PORT


def wait_for_port(
    host: str, port: int, timeout: float = 10.0, interval: float = 0.1
) -> bool:
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


# Global variable to hold the server process so we can terminate it on exit.
_mcp_server_process: subprocess.Popen | None = None


def _cleanup_mcp_server_group():
    """Cleanup function to terminate the entire MCP server process group."""
    global _mcp_server_process
    if _mcp_server_process and _mcp_server_process.poll() is None:
        print(
            "Terminating ReMIP server process group (PGID:"
            f" {os.getpgid(_mcp_server_process.pid)})..."
        )
        try:
            # Send SIGTERM to the entire process group.
            os.killpg(os.getpgid(_mcp_server_process.pid), signal.SIGTERM)
            _mcp_server_process.wait(timeout=5)
            print("ReMIP server process group terminated.")
        except (ProcessLookupError, PermissionError):
            pass
        except subprocess.TimeoutExpired:
            print("Process group did not terminate gracefully. Forcing kill.")
            os.killpg(os.getpgid(_mcp_server_process.pid), signal.SIGKILL)
            _mcp_server_process.wait()


@st.cache_resource
def start_remip_mcp() -> int:
    """
    Starts the remip-mcp server in a new process group and returns the port.
    This ensures the server and any of its children can be terminated together.
    """
    port = MCP_PORT
    proc = subprocess.Popen(
        f"npx -y github:ohtaman/remip-mcp --http --start-remip-server --port {port}",
        shell=True,
        start_new_session=True,
    )

    global _mcp_server_process
    _mcp_server_process = proc
    atexit.register(_cleanup_mcp_server_group)

    wait_for_port("localhost", port)
    return port


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

    return bin_path.absolute()
