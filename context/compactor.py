"""Context compaction and tool output virtualization."""
from __future__ import annotations

import copy
from typing import Any

from context.models import Message


def estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 characters per token for English)."""
    if not text:
        return 0
    return max(1, len(text.strip()) // 4)


def virtualize_tool_outputs(messages: list[Message], keep_last_turns: int = 1) -> list[Message]:
    """Replaces older raw tool outputs with lightweight summaries.

    Keeps the raw tool results of the most recent `keep_last_turns` active turns,
    while evicting bulky text from past turns to prevent context explosion.
    """
    if not messages:
        return []

    # Identify assistant message turn boundaries
    assistant_indices = [i for i, m in enumerate(messages) if m.role == "assistant"]

    # If the last message is NOT an assistant message (i.e. active turn in progress),
    # cutoff is the assistant before the current turn.
    # If the last message IS an assistant message (turn just completed),
    # cutoff is the assistant before the last completed turn.
    if len(messages) > 0 and messages[-1].role == "assistant":
        # Turn is completed: tools supporting this last assistant must be kept
        target_pos = -(keep_last_turns + 1)
        cutoff_index = assistant_indices[target_pos] if len(assistant_indices) >= (keep_last_turns + 1) else 0
    else:
        # Active turn in progress (user / tool is recent):
        target_pos = -keep_last_turns
        cutoff_index = assistant_indices[target_pos] if len(assistant_indices) >= keep_last_turns else 0

    compacted: list[Message] = []
    for idx, msg in enumerate(messages):
        if idx < cutoff_index and msg.role == "tool" and not msg.is_virtualized:
            # Virtualize this tool message
            orig_toks = msg.token_count or estimate_tokens(msg.content)
            tool_name = msg.name or "tool"
            virtual_text = f"[Tool output from {tool_name} ({orig_toks} tokens) evicted; synthesized in assistant response above]"
            c_msg = copy.copy(msg)
            c_msg.content = virtual_text
            c_msg.token_count = estimate_tokens(virtual_text)
            c_msg.is_virtualized = True
            compacted.append(c_msg)
        else:
            compacted.append(msg)

    return compacted


def prune_to_budget(messages: list[Message], max_tokens: int) -> list[Message]:
    """Drops oldest conversation turns until total tokens fit within max_tokens budget.

    Always preserves:
    1. System message (index 0 if present).
    2. The most recent user query and active evidence.
    """
    if not messages:
        return []

    has_system = messages[0].role == "system"
    system_msg = messages[0] if has_system else None
    history = messages[1:] if has_system else messages[:]

    def total_toks(msgs: list[Message]) -> int:
        return sum(m.token_count or estimate_tokens(m.content) for m in msgs)

    sys_toks = (system_msg.token_count or estimate_tokens(system_msg.content)) if system_msg else 0
    remaining_budget = max_tokens - sys_toks

    # If within budget, return as is
    if total_toks(history) <= remaining_budget:
        return messages

    # Evict oldest messages (in pairs: user + assistant/tool) from the front
    while len(history) > 2 and total_toks(history) > remaining_budget:
        history.pop(0)

    result = [system_msg] + history if system_msg else history
    return result
