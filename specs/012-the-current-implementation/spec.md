# Feature Specification: Integrate Real Agent Logic with Mode Selection

**Feature Branch**: `012-the-current-implementation`
**Created**: 2025-10-19
**Status**: Draft
**Input**: User description: "The current implementation just returns what user said. Please use LoopAgent (or remip_agent) defined in agent.py, and allow the user to choose which one."

---

## User Scenarios & Testing *(mandatory)*

### Primary User Story
As a user, when creating a new conversation, I want to choose between a full "Agent Mode" (using `LoopAgent`) and a "Direct Mode" (using the raw `remip_agent`), so I can control the complexity of the agent I am interacting with.

### Acceptance Scenarios
1.  **Given** I select "Agent Mode" and start a new session, **When** I send a message, **Then** the system should process it using the `LoopAgent`.
2.  **Given** I select "Direct Mode" and start a new session, **When** I send a message, **Then** the system should process it using the raw `remip_agent`.
3.  **Given** a session is active, **When** I send any message, **Then** the agent's response should NOT be a simple echo like "Processed: [my message]".

### Edge Cases
-   What happens if the selected agent (`LoopAgent` or `remip_agent`) encounters an error? How is this communicated to the user?
-   How does the system handle a long-running agent task that is interrupted by a new user message?

## Requirements *(mandatory)*

### Functional Requirements
-   **FR-001**: The `AgentService.create_talk_session` method MUST accept a boolean parameter, `is_agent_mode`, to specify the desired agent.
-   **FR-002**: The `TalkSession` data model MUST store the selected `agent_mode` for that session.
-   **FR-003**: The `AgentRunner` class MUST be initialized with the `agent_mode` from the current `TalkSession`.
-   **FR-004**: The `AgentRunner` MUST use the `build_agent` function, passing the `is_agent_mode` flag to it, to get the correct agent instance.
-   **FR-005**: The `AgentRunner.run_async` method MUST pass the user's message to the instantiated agent for processing.
-   **FR-006**: The placeholder echo logic in `AgentRunner.run_async` MUST be completely removed.
-   **FR-007**: The Streamlit UI MUST provide a mechanism (e.g., a checkbox) for the user to select the agent mode before creating a new session.

### Key Entities *(include if feature involves data)*
-   **TalkSession**: Represents a single conversation. It will now include an `agent_mode` attribute to define which agent logic to use.
-   **AgentRunner**: The component that executes the agent logic. It will be configured on a per-task basis based on the session's `agent_mode`.
-   **LoopAgent / remip_agent**: The two possible agent configurations that can be used for processing.

---

## Review & Acceptance Checklist
*GATE: Automated checks run during main() execution*

### Content Quality
- [ ] No implementation details (languages, frameworks, APIs)
- [X] Focused on user value and business needs
- [X] Written for non-technical stakeholders
- [X] All mandatory sections completed

### Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain
- [X] Requirements are testable and unambiguous
- [X] Success criteria are measurable
- [X] Scope is clearly bounded
- [ ] Dependencies and assumptions identified

---

## Execution Status
*Updated by main() during processing*

- [X] User description parsed
- [X] Key concepts extracted
- [ ] Ambiguities marked
- [X] User scenarios defined
- [X] Requirements generated
- [X] Entities identified
- [ ] Review checklist passed

---
