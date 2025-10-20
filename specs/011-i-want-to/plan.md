# Implementation Plan: Asynchronous Agent Architecture

This plan outlines the steps to implement the asynchronous agent architecture with a Streamlit UI, following a Test-Driven Development (TDD) approach. It has been updated to include multi-user support via a `user_id`.

---

## Architectural Note: Why Polling?

The UI implementation (Phase 2) uses a polling loop (`time.sleep` and `st.rerun`) to get live updates from the agent. This is a deliberate architectural choice.

-   **Reason**: Any long-running process within a Streamlit script, like waiting for a generator, is fragile. It can be terminated at any time by a user interaction, which causes the script to rerun. This would break the connection to the agent's response.
-   **Solution**: Polling uses a series of very short, stateless script runs. Each run quickly checks for updates and then exits. This makes the UI immune to the interruption problem and is the most robust way to handle streaming within the standard Streamlit framework.

---

## Phase 1: Core Service Implementation (Backend)

**Goal:** Build a fully functional, thread-safe, multi-tenant `AgentService` that can manage conversations and process messages asynchronously.

### Step 1.1: Data Models
-   **TDD:** Write a test list for data models.
-   **Red:** Write tests that fail to create `Message` and `TalkSession` dataclasses.
-   **Green:** Implement simple dataclasses for `Message` (content: str, sender: str) and `TalkSession` (id: str, **user_id: str**, messages: list[Message]).
-   **Refactor:** Ensure dataclasses are clean and readable.

### Step 1.2: `AgentService` Skeleton and Session Management
-   **TDD:** Write a test list for user-scoped session management.
-   **Red:** Write tests for `create_talk_session(user_id)`, `list_sessions(user_id)`, and `get_session(user_id, session_id)`. Include tests to ensure one user cannot access another user's sessions.
-   **Green:** Implement the `AgentService` class. Use a nested dictionary (`Dict[str, Dict[str, TalkSession]]`) to store sessions, keyed first by `user_id` and then by `session_id`.
-   **Refactor:** Clean up the implementation.

### Step 1.3: Basic Asynchronous Processing
-   **TDD:** Write a test list for the async processing loop.
-   **Red:** Write a test for `add_message(user_id, session_id, ...)` that verifies a message is added to the correct `InputQueue`. Write a test for `get_messages(user_id, session_id)` that verifies a response is available from the `OutputQueue`.
-   **Green:**
    -   In `AgentService`, manage input and output queues on a per-session basis.
    -   Start a single daemon `Worker` thread for the whole service.
    -   Implement `add_message` to put messages on the correct `InputQueue`.
    -   Implement `get_messages` to retrieve messages from the correct `OutputQueue`.
-   **Refactor:** Ensure the interaction between the service, queues, and worker is clear.

### Step 1.4: Implement Stoppable Runner
-   **TDD:** Write a test list for the stoppable runner.
-   **Red:** Write a test that starts a long-running runner for a session, then calls `add_message` for the same session, and verifies the first runner was stopped.
-   **Green:**
    -   Modify the `AgentRunner` to be stoppable.
    -   The `AgentService` will store the active runner for each session.
    -   When `add_message` is called, it will call `stop()` on the existing runner for that session.
-   **Refactor:** Isolate the runner management logic.

---

## Phase 2: Streamlit UI Implementation (Frontend)

**Goal:** Build a responsive user interface that interacts with the multi-tenant backend `AgentService`.

### Step 2.1: Application Singleton and User ID Management
-   Implement a function (`get_agent_service`) decorated with `@st.cache_resource` to create a single instance of `AgentService`.
-   On first run, generate a unique `user_id` (e.g., `str(uuid.uuid4())`) and store it in `st.session_state`.

### Step 2.2: Sidebar and Session Control
-   Create the sidebar UI.
-   Add a "New Talk" button that calls `agent_service.create_talk_session(user_id=st.session_state.user_id)`.
-   Display a list of sessions by calling `agent_service.list_sessions(user_id=st.session_state.user_id)`.

### Step 2.3: Chat Interface
-   If a `talk_session_id` is selected, display the chat history by calling `agent_service.get_messages(user_id=st.session_state.user_id, session_id=...)`.
-   Use `st.chat_input`. When the user submits, call `agent_service.add_message(user_id=st.session_state.user_id, session_id=...)`.

### Step 2.4: Streaming Responses
-   Implement the polling auto-refresh loop (`time.sleep`, `st.rerun`) to periodically call `get_messages` and update the chat display.

---
