"""Agent Orchestrator connecting ContextManager, Retrieval Tools, and LLMs."""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from agent.prompts import SYSTEM_PROMPT
from context.manager import ContextManager
from context.models import CitationRecord
from retrieval.tools import (
    get_chunk,
    get_toc,
    read_section,
    search_chunks,
    search_sections,
)

# Open-AI compatible tool schemas
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_chunks",
            "description": "Search granular textbook passages, tables, and callouts by keyword or concept.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific medical term, symptom, anatomical landmark, or dosage."
                    },
                    "book_slug": {
                        "type": "string",
                        "description": "Optional book identifier to restrict search to a single volume."
                    },
                    "section_id": {
                        "type": "integer",
                        "description": "Optional section ID to restrict search within that section."
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["prose", "table", "box", "figure_caption"],
                        "description": "Filter by block type."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of chunks to return (default: 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_sections",
            "description": "Search textbook outline headings and table of contents to locate chapters and major sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "High-level medical or anatomical topic (e.g. 'adrenal gland', 'brachial plexus')."
                    },
                    "book_slug": {
                        "type": "string",
                        "description": "Optional book identifier."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sections to return (default: 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_section",
            "description": "Read an entire textbook subsection in reading order with its prose and tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_slug": {
                        "type": "string",
                        "description": "Book slug identifier."
                    },
                    "section_id": {
                        "type": "integer",
                        "description": "The section ID to read."
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Token cap to avoid context overflow (default: 2500)."
                    }
                },
                "required": ["book_slug", "section_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_toc",
            "description": "Browse table of contents or child subsections under a chapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_slug": {
                        "type": "string",
                        "description": "Book slug identifier."
                    },
                    "parent_id": {
                        "type": "integer",
                        "description": "Parent section ID, or omit for root chapters."
                    }
                },
                "required": ["book_slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_chunk",
            "description": "Fetch a specific chunk by its chunk_id to inspect cited evidence, table or figure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Exact chunk ID."
                    }
                },
                "required": ["chunk_id"]
            }
        }
    }
]


def execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Executes a retrieval tool by name with arguments."""
    if name == "search_chunks":
        return search_chunks(**arguments)
    elif name == "search_sections":
        return search_sections(**arguments)
    elif name == "read_section":
        return read_section(**arguments)
    elif name == "get_toc":
        return get_toc(**arguments)
    elif name == "get_chunk":
        return get_chunk(**arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


class AgentOrchestrator:
    """Orchestrates multi-turn conversation, structure-aware tool invocation, and LLM responses."""

    def __init__(self, context_manager: ContextManager | None = None):
        self.context_manager = context_manager or ContextManager()
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")

    def run_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
    ) -> dict[str, Any]:
        """Runs a complete conversation turn and returns grounded answer, citations, tool pill logs, and follow-ups."""
        # 1. Add user message
        self.context_manager.add_user_message(session_id, user_message)

        # 2. Check for configured LLM or fallback to Smart Retrieval Synthesizer
        if self.gemini_api_key:
            return self._run_gemini_turn(session_id, user_message, book_slug)
        elif self.openai_api_key:
            return self._run_openai_turn(session_id, user_message, book_slug)
        else:
            return self._run_offline_synthesizer_turn(session_id, user_message, book_slug)

    def _run_offline_synthesizer_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
    ) -> dict[str, Any]:
        """Intelligent, structure-aware retrieval and synthesis when running offline without LLM API keys."""
        tool_activity: list[dict[str, Any]] = []

        # 1. Execute chunk retrieval
        query = user_message.strip()
        # Clean question punctuation for FTS5 search
        clean_query = re.sub(r"[^\w\s-]", " ", query).strip()
        search_args: dict[str, Any] = {"query": clean_query, "limit": 4}
        if book_slug:
            search_args["book_slug"] = book_slug

        chunks_result = execute_tool("search_chunks", search_args)
        tool_call_id_1 = f"call_{uuid.uuid4().hex[:8]}"

        self.context_manager.add_tool_result(
            session_id=session_id,
            tool_call_id=tool_call_id_1,
            name="search_chunks",
            result=chunks_result,
        )

        tool_activity.append({
            "name": "search_chunks",
            "query": clean_query,
            "hits": len(chunks_result),
            "summary": f'Searched chunks for "{clean_query}" ({len(chunks_result)} hits)'
        })

        # 2. Also search sections for structural chapter mapping
        sec_args: dict[str, Any] = {"query": clean_query, "limit": 2}
        if book_slug:
            sec_args["book_slug"] = book_slug
        sections_result = execute_tool("search_sections", sec_args)
        tool_call_id_2 = f"call_{uuid.uuid4().hex[:8]}"

        self.context_manager.add_tool_result(
            session_id=session_id,
            tool_call_id=tool_call_id_2,
            name="search_sections",
            result=sections_result,
        )

        tool_activity.append({
            "name": "search_sections",
            "query": clean_query,
            "hits": len(sections_result),
            "summary": f'Searched section outline for "{clean_query}" ({len(sections_result)} sections found)'
        })

        # 3. Retrieve registered citations
        session = self.context_manager.get_or_create_session(session_id)
        current_citations = session.citations

        # 4. Generate structured synthesis
        if not chunks_result:
            reply = (
                f"I searched the indexed medical textbooks for **\"{user_message}\"**, but did not find matching passages.\n\n"
                "Try searching for broader medical concepts or specific anatomical terms (e.g. *adrenal cortex*, *femoral artery*, *thyroid hormone*)."
            )
            follow_ups = [
                "Browse the Table of Contents",
                "Search Anatomy & Physiology textbook",
                "Search Williams Textbook of Endocrinology"
            ]
        else:
            # Build synthesis grounded in the retrieved chunks
            top_hit = chunks_result[0]
            top_heading = top_hit.get("heading_path", "Textbook Section")
            top_page = top_hit.get("page_start", 1)

            # Assemble synthesis sections
            points = []
            for i, chunk in enumerate(chunks_result[:3]):
                cit_num = i + 1
                heading = chunk.get("heading_path", "")
                page = chunk.get("page_start", "")
                snippet = chunk.get("text", "").strip()
                # Take first 2-3 sentences of chunk
                sentences = re.split(r"(?<=[.!?])\s+", snippet)
                core_text = " ".join(sentences[:3]) if sentences else snippet[:250]
                points.append(f"• **{heading}** (p. {page}) [{cit_num}]:\n  {core_text}")

            combined_points = "\n\n".join(points)

            reply = f"""### Summary & Key Findings

Based on the indexed medical textbooks, here is the synthesis regarding **{user_message}**:

{combined_points}

> **Clinical Pearl / Structural Context:**
> This topic is discussed under **{top_heading}** (printed page {top_page}) [1]. Notice the organizational relationship across related chapters and physiological mechanisms.

[FOLLOW_UP_QUESTIONS]
- Can you explain the detailed physiological regulation and clinical complications related to this?
- What are the corresponding anatomical relationships and diagnostic criteria?
- Read the full textbook section covering {top_heading.split(' > ')[-1]}
"""

        # Extract follow-up questions
        clean_reply, follow_ups = self._parse_follow_ups(reply)

        # Add assistant message to session
        self.context_manager.add_assistant_message(session_id, clean_reply)

        # Return structured response
        return {
            "reply": clean_reply,
            "citations": [c.to_dict() for c in current_citations.values()],
            "tool_calls": tool_activity,
            "follow_ups": follow_ups,
        }

    def _run_openai_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
    ) -> dict[str, Any]:
        """Runs multi-turn tool calling loop using OpenAI API."""
        import openai

        client = openai.OpenAI(api_key=self.openai_api_key)
        tool_activity: list[dict[str, Any]] = []

        # Assemble prompt with virtualization & budgeting
        messages = self.context_manager.assemble_prompt(
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

            # Handle tool calls
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments or "{}")
                if book_slug and "book_slug" not in fn_args:
                    fn_args["book_slug"] = book_slug

                result = execute_tool(fn_name, fn_args)

                self.context_manager.add_tool_result(
                    session_id=session_id,
                    tool_call_id=tc.id,
                    name=fn_name,
                    result=result,
                )

                num_hits = len(result) if isinstance(result, list) else 1
                tool_activity.append({
                    "name": fn_name,
                    "args": fn_args,
                    "summary": f"{fn_name} returned {num_hits} items"
                })

            # Re-assemble prompt for next iteration
            messages = self.context_manager.assemble_prompt(
                session_id=session_id,
                system_prompt=SYSTEM_PROMPT,
            )

        clean_reply, follow_ups = self._parse_follow_ups(final_reply)
        self.context_manager.add_assistant_message(session_id, clean_reply)

        session = self.context_manager.get_or_create_session(session_id)
        return {
            "reply": clean_reply,
            "citations": [c.to_dict() for c in session.citations.values()],
            "tool_calls": tool_activity,
            "follow_ups": follow_ups,
        }

    def _run_gemini_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
    ) -> dict[str, Any]:
        """Runs multi-turn tool calling loop using Google GenAI."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.gemini_api_key)
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        tool_activity: list[dict[str, Any]] = []

        # Build tools list for Gemini SDK
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

        messages = self.context_manager.assemble_prompt(
            session_id=session_id,
            system_prompt=SYSTEM_PROMPT,
        )

        # Convert context messages to Gemini contents
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))

        res = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=tools,
            ),
        )

        final_reply = res.text or ""
        clean_reply, follow_ups = self._parse_follow_ups(final_reply)
        self.context_manager.add_assistant_message(session_id, clean_reply)

        session = self.context_manager.get_or_create_session(session_id)
        return {
            "reply": clean_reply,
            "citations": [c.to_dict() for c in session.citations.values()],
            "tool_calls": tool_activity,
            "follow_ups": follow_ups,
        }

    def _parse_follow_ups(self, text: str) -> tuple[str, list[str]]:
        """Extracts follow-up questions from the LLM response text."""
        follow_ups: list[str] = []
        if "[FOLLOW_UP_QUESTIONS]" in text:
            parts = text.split("[FOLLOW_UP_QUESTIONS]")
            main_text = parts[0].strip()
            fu_block = parts[1].strip()

            for line in fu_block.split("\n"):
                line = line.strip()
                if line.startswith("-") or line.startswith("*") or (len(line) > 2 and line[0].isdigit() and line[1] in [".", ")"]):
                    clean_q = re.sub(r"^[-*0-9.)\s]+", "", line).strip()
                    if clean_q:
                        follow_ups.append(clean_q)

            return main_text, follow_ups[:3]

        return text, [
            "What are the relevant clinical differential diagnoses?",
            "Can you explain the associated biochemical or anatomical pathways?",
            "Read the surrounding textbook section for deeper context"
        ]
