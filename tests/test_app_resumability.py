"""Test for app resumability."""

# from google.adk import Agent, Runner
# from google.adk.runners import ResumabilityConfig
# from google.adk.agents.run_config import RunConfig
# from google.adk.tools import LongRunningFunctionTool
# from google.genai.types import Content


# def long_running_tool(prompt: str) -> dict:
#     """A tool that simulates a long-running operation.

#     Args:
#         prompt: The prompt to the tool.

#     Returns:
#         A dictionary with the result.
#     """
#     return {"result": f"Processed: {prompt}"}


# async def test_resume_with_new_message() -> None:
#     """Test that a paused agent can be resumed with a new message."""
#     # 1. Initial run with a message that triggers the long-running tool.
#     runner = Runner(
#         agent=Agent(
#             model="gemini-2.5-flash",
#             tools=[LongRunningFunctionTool(long_running_tool)]
#         ),
#         run_config=RunConfig(resumability_config=ResumabilityConfig(is_resumable=True)),
#     )
#     initial_events = await runner.run_async(
#         new_message=Content(
#             parts=[Content.Part(text="Use the long_running_tool with the prompt 'initial'")]
#         ),
#     )

#     # The agent should pause after calling the long-running tool.
#     assert len(initial_events) == 2
#     assert initial_events[1].get_function_calls()[0].name == "long_running_tool"
#     invocation_id = initial_events[0].invocation_id

#     # 2. Resume the run with a new message.
#     resumed_events = await runner.run_async(
#         invocation_id=invocation_id,
#         new_message=Content(
#             parts=[Content.Part(text="Use the long_running_tool with the prompt 'resumed'")]
#         ),
#     )

#     # The agent should have resumed and called the tool with the new prompt.
#     assert len(resumed_events) == 2
#     assert resumed_events[1].get_function_calls()[0].name == "long_running_tool"
#     assert (
#         resumed_events[1].get_function_calls()[0].args["prompt"] == "resumed"
#     ), "The agent should have used the new prompt."
