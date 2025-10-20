# Implementation Plan: Robust Asynchronous Worker

This plan details the refactoring of the `AgentService` to implement a robust, asyncio-native worker thread. This architecture resolves the underlying conflicts between the `threading` model and the `asyncio`/`anyio` libraries used by the ADK and its tools.

---

## Architectural Goal

The root cause of the application instability (`RuntimeError: ...bound to a different event loop`, `...exit cancel scope in a different task`) is the use of `asyncio.run()` for each task within the worker thread. This creates a new, short-lived event loop for every message, which conflicts with stateful async libraries like `anyio` used by the `McpToolset`.

The solution is to create a **single, persistent event loop** that lives for the entire duration of the worker thread. All async operations will run as tasks within this stable loop, ensuring consistency.

---

## Phase 1: Refactor the `AgentService` Worker

### Step 1.1: Create the Asynchronous Worker Loop (`_async_worker`)

-   **Action**: Create a new `async def _async_worker(self)` method in `AgentService`.
-   **Details**:
    -   This method will contain the main `while self._is_running:` loop.
    -   Instead of a blocking `queue.get()`, the loop will iterate through the input queues and check for tasks in a non-blocking way.
    -   If no tasks are found, it will use `await asyncio.sleep(0.1)` to yield control, preventing a busy-wait and making the loop efficient.

### Step 1.2: Adapt Agent Execution for `async`

-   **Action**: Modify the agent execution logic inside `_async_worker`.
-   **Details**:
    -   The call to the ADK `Runner` will now use the `run_async` method: `response_generator = runner.run_async(...)`.
    -   The code will iterate through the resulting async generator to get the response events: `async for event in response_generator:`.
    -   The logic for extracting text from the event parts remains the same.

### Step 1.3: Implement the new Synchronous Worker Bootstrap (`_worker`)

-   **Action**: The existing `_worker` method will be replaced with a simple bootstrap function.
-   **Details**: Its only responsibilities will be:
    1.  Create a new `asyncio` event loop (`asyncio.new_event_loop()`).
    2.  Set it as the current loop for the thread (`asyncio.set_event_loop(loop)`).
    3.  Run the `_async_worker` method until it completes (`loop.run_until_complete(self._async_worker())`).

## Phase 2: UI Implementation (No Change)

-   **Action**: Add a `st.checkbox` to `app.py` to control the `is_agent_mode` parameter when creating a new session.
-   **Status**: This step remains unchanged and will be completed after the backend is stable.
