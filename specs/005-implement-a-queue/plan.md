# Implementation Plan: Queue-Based Runner Architecture

**Feature Branch**: `005-implement-a-queue`
**Specification**: `spec.md`

This plan outlines the steps to refactor the asynchronous runner to a robust, queue-based "cancel and replace" architecture. We will follow a Test-Driven Development (TDD) approach, starting with the core logic and then integrating it into the application.

## Phase 1: Core Logic and Worker (TDD Cycle)

This phase focuses on building the foundational components of the new architecture. Each step includes writing a failing test first, then writing the code to make it pass.

### TDD Task List (Test List):
1.  **Test `TaskQueue`:**
    *   `test_add_task`: Verify a task can be added to the queue.
    *   `test_get_task`: Verify a task can be retrieved from the queue.
    *   `test_clear_tasks`: Verify that all pending tasks can be cleared from the queue.
2.  **Implement `TaskQueue`:**
    *   Create a simple, thread-safe queue wrapper class.
3.  **Test `AsyncWorker`:**
    *   `test_process_single_task`: Verify the worker can take one task from an input queue, execute it, and put the result on an output queue.
    *   `test_cancellation`: Verify that if a cancellation is signaled, the worker stops the current task gracefully and does not proceed to the next one until instructed.
    *   `test_cancel_and_replace`: Verify that submitting a new task correctly cancels the running one and gets executed.
4.  **Implement `AsyncWorker`:**
    *   Create the worker class that runs in a background thread.
    *   It will take an input queue, an output queue, and a cancellation token/event as dependencies.
    *   The main loop will fetch a task, check for cancellation, execute, and repeat.
5.  **Test `RunnerService`:**
    *   `test_submit_new_task_when_idle`: Verify that submitting a task when the worker is idle starts it immediately.
    *   `test_submit_new_task_when_busy`: Verify that submitting a task when the worker is busy triggers the "cancel and replace" logic.
6.  **Implement `RunnerService`:**
    *   Create a high-level service class that manages the `TaskQueue` and `AsyncWorker`.
    *   This service will expose a simple `submit(task)` method to the rest of the application. This method will contain the core logic: `signal_cancel()`, `clear_queue()`, `add_task()`.

## Phase 2: Application Integration

Once the core components are built and tested, we will integrate them into the main application.

1.  **Refactor `app.py` (or relevant UI components):**
    *   Instantiate the new `RunnerService` **once** and store it in Streamlit's `st.session_state`. This is critical to prevent creating new threads on every script rerun.
    *   On each rerun, retrieve the service instance from `st.session_state`.
    *   Replace direct calls to the old runner with calls to `runner_service.submit()`.
    *   Implement a mechanism to listen to the output queue from the `RunnerService` and update the Streamlit UI state accordingly. This will likely involve a polling loop that checks the queue for new events.

## Phase 3: Documentation

Clear documentation is crucial for maintainability.

1.  **Update `README.md`:**
    *   Add a new section titled **"Asynchronous Task Architecture"**.
    *   Explain the Producer-Consumer pattern and the "cancel and replace" strategy.
    *   Include a Markdown-based diagram illustrating the architecture: `UI -> Input Queue -> Worker -> Output Queue -> UI`.
    *   Explain why this architecture was chosen (robustness, decoupling, flexibility).

## Completion Checklist
- [ ] All tests in Phase 1 are passing.
- [ ] The `RunnerService` is successfully integrated into the application in Phase 2.
- [ ] The UI correctly reflects the status of asynchronous tasks (running, cancelled, completed).
- [ ] The `README.md` is updated with the new architecture documentation in Phase 3.
- [ ] The feature branch is ready for review and merging.
