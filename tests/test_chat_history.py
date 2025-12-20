from __future__ import annotations

from google.adk.events import Event, EventActions
from google.genai import types

from remip_example.chat_history import (
    build_committed_events_from_partials,
    events_to_messages,
)


def _make_event(
    *,
    role: str,
    text: str,
    partial: bool = False,
    invocation_id: str | None = None,
) -> Event:
    content = types.Content(role=role, parts=[types.Part(text=text)])
    kwargs = {
        "content": content,
        "author": role,
        "partial": partial,
        "actions": EventActions(),
    }
    if invocation_id is not None:
        kwargs["invocationId"] = invocation_id
    return Event(**kwargs)


def test_events_to_messages_merges_partials_and_includes_history():
    events = [
        _make_event(role="user", text="Hello there"),
        _make_event(role="model", text="Partial ", partial=True, invocation_id="inv-1"),
        _make_event(role="model", text="chunk", partial=True, invocation_id="inv-1"),
        _make_event(role="model", text="!", partial=False, invocation_id="inv-1"),
        _make_event(
            role="model", text="Second reply", partial=False, invocation_id="inv-2"
        ),
    ]

    history = events_to_messages(events)

    assert history == [
        ("user", "Hello there"),
        ("assistant", "Partial chunk!"),
        ("assistant", "Second reply"),
    ]


def test_events_to_messages_handles_partial_without_final():
    events = [
        _make_event(
            role="model", text="Streaming ", partial=True, invocation_id="inv-1"
        ),
        _make_event(role="model", text="output", partial=True, invocation_id="inv-1"),
    ]

    history = events_to_messages(events)

    assert history == [("assistant", "Streaming output")]


def test_build_committed_events_from_partials_merges_by_invocation():
    partials = [
        _make_event(role="model", text="First ", partial=True, invocation_id="foo"),
        _make_event(role="model", text="piece", partial=True, invocation_id="foo"),
        _make_event(role="model", text="Another", partial=True, invocation_id="bar"),
    ]

    committed = build_committed_events_from_partials(partials)

    assert len(committed) == 2
    texts = {
        getattr(ev, "invocation_id", None): "".join(
            part.text for part in ev.content.parts
        )
        for ev in committed
    }
    assert texts["foo"] == "First piece"
    assert texts["bar"] == "Another"
