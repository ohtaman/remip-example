# Implementation Plan: Generate README.md

**Branch**: `008-generate-readme-md` | **Date**: 2025-10-19 | **Spec**: [specs/008-generate-readme-md/spec.md](specs/008-generate-readme-md/spec.md)
**Input**: Feature specification from `/specs/008-generate-readme-md/spec.md`

## Summary
This feature will create a tool to automatically generate a high-quality `README.md` file for the project. The tool will analyze the project's structure, `pyproject.toml`, and an `examples/` directory to create a comprehensive and easy-to-read document for new users and contributors.

## Technical Context
**Language/Version**: Python 3.12
**Primary Dependencies**: `tomli` (for parsing pyproject.toml)
**Storage**: N/A
**Testing**: pytest
**Target Platform**: Local execution (CLI tool)
**Project Type**: Single project
**Performance Goals**: N/A
**Constraints**: Must correctly parse standard `pyproject.toml` files.
**Scale/Scope**: The tool will be developed for the current project but designed to be adaptable.

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The proposed plan adheres to the project's constitution. It introduces a new, isolated service for README generation without altering existing architectural patterns.

## Project Structure

### Documentation (this feature)
```
specs/008-generate-readme-md/
├── plan.md              # This file
├── research.md          # Not required for this feature
├── data-model.md        # Not required for this feature
├── quickstart.md        # Will contain instructions to run the generator
└── tasks.md             # To be generated next
```

### Source Code (repository root)
```
# Using existing structure: Single project
src/
└── remip_example/
    ├── readme_generator.py # New file for this feature
    └── ... (existing files)

tests/
├── test_readme_generator.py # New test file for this feature
└── ... (existing files)
```

**Structure Decision**: Option 1: Single project (existing structure)

## Phase 0: Outline & Research
No significant research is required for this feature as the scope is well-defined and relies on standard libraries.

## Phase 1: Design & Contracts
This feature is a CLI tool, not a service with APIs, so no API contracts will be generated. The "contract" is the structure of the generated `README.md`.

1.  **Data Model**: Not applicable, as we are not creating persistent entities.
2.  **Quickstart**: A `quickstart.md` will be created with instructions on how to run the README generator.
3.  **Test Plan (Test List for TDD)**:
    - **Test 1 (Fail)**: Create a test that calls a `ReadmeGenerator` class and asserts it fails because the class doesn't exist.
    - **Test 2 (Pass)**: Create the `ReadmeGenerator` class in `src/remip_example/readme_generator.py`.
    - **Test 3 (Fail)**: Create a test to check if the generator can parse the project name from a mock `pyproject.toml`.
    - **Test 4 (Pass)**: Implement the logic to read and parse `pyproject.toml` for the project name.
    - **Test 5 (Fail)**: Create a test to check if the generator includes a project description.
    - **Test 6 (Pass)**: Implement logic to add a static description section.
    - **Test 7 (Fail)**: Create a test to check for the "Installation" section based on dependencies in `pyproject.toml`.
    - **Test 8 (Pass)**: Implement logic to generate installation instructions.
    - **Test 9 (Fail)**: Create a test to check for a "Usage" section that references files in the `examples/` directory.
    - **Test 10 (Pass)**: Implement logic to scan the `examples/` directory and list its contents.
    - **Test 11 (Fail)**: Create a test to check for a "Contributing" section.
    - **Test 12 (Pass)**: Implement logic to add a static "Contributing" section.
    - **Test 13 (Fail)**: Create a test for the full `generate()` method, asserting the complete README content is generated correctly.
    - **Test 14 (Pass)**: Implement the `generate()` method that assembles all sections into the final README string.
    - **Test 15 (Refactor)**: Review and refactor the `ReadmeGenerator` for clarity and efficiency.

## Phase 2: Task Planning Approach
The `/tasks` command will convert the test list from Phase 1 into a structured `tasks.md` file. Each test pair (Fail/Pass) will become a task, followed by refactoring tasks.

## Progress Tracking
**Phase Status**:
- [X] Phase 0: Research complete
- [ ] Phase 1: Design complete
- [ ] Phase 2: Task planning complete
- [ ] Phase 3: Tasks generated
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [X] Initial Constitution Check: PASS
- [ ] Post-Design Constitution Check: PENDING
- [X] All NEEDS CLARIFICATION resolved
- [ ] Complexity deviations documented
