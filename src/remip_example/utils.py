"""Utility functions for the remip-sample application."""

import atexit
import os
import pathlib
import signal
import socket
import subprocess
import tarfile
import time
import urllib.request

import streamlit as st
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

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


@st.cache_resource
def start_remip() -> int:
    port = 9999
    proc = subprocess.Popen(
        f"uvx remip --port {port}",
        shell=True,
        start_new_session=True,
    )

    global _mcp_server_process
    _mcp_server_process = proc
    atexit.register(_cleanup_mcp_server_group)

    wait_for_port("localhost", port)
    return port


@st.cache_resource
def get_mcp_toolset() -> McpToolset:
    """Starts the MCP server and returns a cached toolset instance."""
    port = start_remip_mcp()
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=f"http://localhost:{port}/mcp/",
            timeout=30,
            terminate_on_close=False,
        ),
    )
    print("TOOLSET", id(toolset))
    return toolset


@st.cache_resource
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


@st.cache_resource
def ensure_http_server(port: int = 3333) -> int:
    """
    Start the MCP server (HTTP mode) once per Streamlit session, and keep it alive
    across reruns. It will be terminated by the atexit handler when the process exits.
    """
    proc = subprocess.Popen(
        [
            "npx",
            "-y",
            "github:ohtaman/remip-mcp",
            "--http",
            "--start-remip-server",
            "--port",
            str(port),
        ],
        start_new_session=True,
    )

    def _cleanup():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
            except Exception:
                pass

    atexit.register(_cleanup)

    if not wait_for_port("127.0.0.1", port, timeout=10.0):
        raise RuntimeError(f"MCP HTTP server failed to start on port {port}")
    return port
