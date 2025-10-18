# remip-example

This repository serves as a sample project for the [`remip`](https://github.com/ohtaman/remip) and [`remip-mcp`](https://github.com/ohtaman/remip-mcp) libraries.

It demonstrates how to build an interactive, agent-based chat application using Streamlit and the Google Agent Development Kit (ADK), providing a complete example of how these components work together.

## Features

-   **Interactive Chat UI**: A user-friendly web interface built with Streamlit.
-   **Multi-Agent System**: Demonstrates a setup where different agents can be used to handle requests.
-   **Integration with `remip-mcp`**: Demonstrates how to use tools from the `remip-mcp` library to solve mathematical optimization problems.
-   **Example Prompts for Mathematical Optimization**: Includes pre-defined examples that showcase how the agent handles mathematical optimization tasks.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/remip-example.git
    cd remip-example
    ```

2.  **Install dependencies:**
    This project uses `uv` for package management. Install the required packages using the following command:
    ```bash
    uv sync
    ```

## Configuration

To use this application, you need a Gemini API key. Set it as an environment variable:

```bash
export GEMINI_API_KEY="YOUR_API_KEY"
```

Alternatively, the application will prompt you to enter the API key when it first runs.

## Usage

### Quick Start (No Installation)

If you have `uv` and Python installed, you can run the application directly from GitHub without cloning the repository:

```bash
uvx --from git+https://github.com/ohtaman/remip-example remip-example
```

### Local Development

To start the Streamlit application from your local clone, run the following command in your terminal:

```bash
uv run streamlit run src/remip_example/app.py
```

The application will open in your web browser.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue if you have suggestions or find any bugs.
