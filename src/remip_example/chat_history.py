from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, List, Tuple

from google.adk.events import Event, EventActions
from google.genai import types


def event_text(event: Event) -> str:
    content = getattr(event, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", None) or []
    return "".join(
        getattr(part, "text", "") for part in parts if getattr(part, "text", None)
    )


def events_to_messages(events: Iterable[Event]) -> List[Tuple[str, str]]:
    """
    Convert a sequence of ADK events into chat history messages.

    Partial model events are accumulated per invocation and merged with their
    final response when available. Remaining partials without a final message
    are emitted as assistant messages so that interrupted generations are
    preserved.
    """
    history: List[Tuple[str, str]] = []
    partials: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    none_key_counter = 0

    last_none_key: str | None = None

    for event in events:
        text = event_text(event)
        if not text:
            continue

        role = getattr(getattr(event, "content", None), "role", None)
        if role == "user":
            history.append(("user", text))
            # reset active none key when user speaks
            last_none_key = None
            continue

        invocation_id = getattr(event, "invocationId", None) or getattr(
            event, "invocation_id", None
        )

        if invocation_id is None:
            if last_none_key is None or last_none_key not in partials:
                key = f"__none__:{none_key_counter}"
                none_key_counter += 1
                partials[key] = {"text": "", "role": "assistant"}
            key = last_none_key or f"__none__:{none_key_counter - 1}"
            last_none_key = key
        else:
            key = invocation_id
            last_none_key = None
            if key not in partials:
                partials[key] = {"text": "", "role": "assistant"}

        if getattr(event, "partial", False):
            partials[key]["text"] += text
            continue

        accumulated = partials.pop(key, {"text": "", "role": "assistant"})
        combined = (accumulated["text"] + text).strip()
        if combined:
            history.append((accumulated.get("role", "assistant"), combined))

    # Flush remaining partials (interruptions)
    for data in partials.values():
        text = data["text"].strip()
        if text:
            history.append((data.get("role", "assistant"), text))

    return history


def build_committed_events_from_partials(partials: Iterable[Event]) -> List[Event]:
    """
    Merge partial events by invocation and return finalized Event objects
    suitable for appending into the session history.
    """
    grouped: list[dict[str, object]] = []
    indexed: dict[str, dict[str, object]] = {}
    current_none_group: dict[str, object] | None = None

    for event in partials:
        invocation_id = getattr(event, "invocationId", None) or getattr(
            event, "invocation_id", None
        )
        if invocation_id:
            group = indexed.get(invocation_id)
            if not group:
                group = {
                    "invocation_id": invocation_id,
                    "events": [],
                    "author": event.author or "assistant",
                }
                grouped.append(group)
                indexed[invocation_id] = group
            current_none_group = None
        else:
            group = current_none_group
            if group is None:
                group = {
                    "invocation_id": None,
                    "events": [],
                    "author": event.author or "assistant",
                }
                grouped.append(group)
                current_none_group = group

        group["events"].append(event)

    committed: List[Event] = []
    for group in grouped:
        events_in_group = group["events"]
        merged_text = "".join(
            getattr(part, "text", "")
            for ev in events_in_group
            for part in (getattr(getattr(ev, "content", None), "parts", []) or [])
            if getattr(part, "text", None)
        ).strip()
        if not merged_text:
            continue

        content = types.Content(role="model", parts=[types.Part(text=merged_text)])
        kwargs = {
            "content": content,
            "author": group.get("author") or "assistant",
            "partial": False,
            "actions": EventActions(),
        }
        invocation_id = group.get("invocation_id")
        if invocation_id:
            kwargs["invocationId"] = invocation_id

        committed.append(Event(**kwargs))

    return committed
