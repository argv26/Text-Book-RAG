"""LLM provider integrations for OpenAI and Google Gemini tool-calling ReAct loops."""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from agent.offline import parse_follow_ups
from agent.prompts import SYSTEM_PROMPT
from agent.sanitizers import _sanitize_citations, _sanitize_medical_typography
from agent.tools_spec import TOOL_DEFINITIONS, execute_tool

if TYPE_CHECKING:
    from context.manager import ContextManager


def run_openai_turn(
    context_manager: ContextManager,
    session_id: str,
    user_message: str,
    api_key: str,
    book_slug: str | None = None,
    active_books: list[str] | None = None,
) -> dict[str, Any]:
    """Runs multi-turn tool calling loop using OpenAI API."""
    import openai

    client = openai.OpenAI(api_key=api_key)
    tool_activity: list[dict[str, Any]] = []

    messages = context_manager.assemble_prompt(
        session_id=session_id,
        system_prompt=SYSTEM_PROMPT,
    )

    max_iterations = 5
    final_reply = ""

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            final_reply = msg.content or ""
            break

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments or "{}")
            if book_slug and "book_slug" not in fn_args:
                fn_args["book_slug"] = book_slug
            if active_books is not None and fn_name in ("search_chunks", "search_sections"):
                fn_args["allowed_books"] = active_books

            result = execute_tool(fn_name, fn_args)

            context_manager.add_tool_result(
                session_id=session_id,
                tool_call_id=tc.id,
                name=fn_name,
                result=result,
            )

            num_hits = len(result) if isinstance(result, list) else 1
            tool_activity.append({
                "name": fn_name,
                "args": fn_args,
                "summary": f"{fn_name} returned {num_hits} items",
            })

        messages = context_manager.assemble_prompt(
            session_id=session_id,
            system_prompt=SYSTEM_PROMPT,
        )

    session = context_manager.get_or_create_session(session_id)
    valid_ids = {c.citation_id for c in session.citations.values()}
    final_reply = _sanitize_medical_typography(final_reply)
    final_reply = _sanitize_citations(final_reply, valid_ids)

    clean_reply, follow_ups = parse_follow_ups(final_reply)
    context_manager.add_assistant_message(session_id, clean_reply)

    return {
        "reply": clean_reply,
        "citations": [c.to_dict() for c in session.citations.values()],
        "tool_calls": tool_activity,
        "follow_ups": follow_ups,
    }


def run_gemini_turn(
    context_manager: ContextManager,
    session_id: str,
    user_message: str,
    api_key: str,
    book_slug: str | None = None,
    active_books: list[str] | None = None,
) -> dict[str, Any]:
    """Runs multi-turn tool calling loop using Google GenAI."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    candidate_models = [
        os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
    ]
    seen_m = set()
    models = [m for m in candidate_models if not (m in seen_m or seen_m.add(m))]
    tool_activity: list[dict[str, Any]] = []

    tools = [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters=t["function"]["parameters"],
            )
            for t in TOOL_DEFINITIONS
        ])
    ]

    messages = context_manager.assemble_prompt(
        session_id=session_id,
        system_prompt=SYSTEM_PROMPT,
    )

    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] in ["assistant", "model"] else "user"
        text_content = m["content"]
        if m["role"] == "tool":
            text_content = f"[Reference retrieved for {m.get('name', 'tool')}]:\n{text_content}"

        if contents and contents[-1].role == role:
            contents[-1].parts.append(types.Part.from_text(text=text_content))
        else:
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text_content)]))

    max_iterations = 5
    final_reply = ""
    last_error = None

    for model in models:
        try:
            working_contents = list(contents)
            turn_tools = list(tool_activity)
            model_reply = ""

            for _ in range(max_iterations):
                res = client.models.generate_content(
                    model=model,
                    contents=working_contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=tools,
                    ),
                )

                if not res.function_calls:
                    model_reply = res.text or ""
                    break

                if res.candidates and res.candidates[0].content:
                    working_contents.append(res.candidates[0].content)

                response_parts = []
                for fc in res.function_calls:
                    fn_name = fc.name
                    fn_args = dict(fc.args) if fc.args else {}
                    if book_slug and "book_slug" not in fn_args:
                        fn_args["book_slug"] = book_slug
                    if active_books is not None and fn_name in ("search_chunks", "search_sections"):
                        fn_args["allowed_books"] = active_books

                    result = execute_tool(fn_name, fn_args)
                    tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

                    context_manager.add_tool_result(
                        session_id=session_id,
                        tool_call_id=tool_call_id,
                        name=fn_name,
                        result=result,
                    )

                    num_hits = len(result) if isinstance(result, list) else 1
                    turn_tools.append({
                        "name": fn_name,
                        "args": fn_args,
                        "summary": f"{fn_name} returned {num_hits} items",
                    })

                    response_parts.append(
                        types.Part.from_function_response(name=fn_name, response={"result": result})
                    )

                working_contents.append(types.Content(role="user", parts=response_parts))

            if model_reply:
                final_reply = model_reply
                tool_activity = turn_tools
                break
        except Exception as e:
            print(f"[AgentOrchestrator] Model {model} failed ({e}). Trying next candidate...")
            last_error = e
            continue

    if not final_reply:
        raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

    session = context_manager.get_or_create_session(session_id)
    valid_ids = {c.citation_id for c in session.citations.values()}
    final_reply = _sanitize_medical_typography(final_reply)
    final_reply = _sanitize_citations(final_reply, valid_ids)

    clean_reply, follow_ups = parse_follow_ups(final_reply)
    context_manager.add_assistant_message(session_id, clean_reply)

    return {
        "reply": clean_reply,
        "citations": [c.to_dict() for c in session.citations.values()],
        "tool_calls": tool_activity,
        "follow_ups": follow_ups,
    }

