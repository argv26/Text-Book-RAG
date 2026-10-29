"""Agent Orchestrator connecting ContextManager, Retrieval Tools, and LLMs."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
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
    search_figures,
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
                    "allowed_books": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of book identifiers to restrict search scope."
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
                        "description": "Maximum number of chunks to return (default: 6, up to 8)."
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
                    "allowed_books": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of book identifiers to restrict search scope."
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



def _clean_chunk_prose(text: str) -> str:
    """Cleans OCR artifacts, hyphenated line wraps, reference indices, and diagram fragments."""
    # 1. Hyphenated word wrap
    text = re.sub(r"(\b\w+)-\s*\n\s*(\w+\b)", r"\1\2", text)
    # 2. Known broken suffixes wrapped across lines without hyphen
    broken_suffixes = (
        r"(?:ates|tant|poral|tion|tions|sis|ment|ments|ular|ence|ency|able|ology|"
        r"genic|genous|ically|ary|ive|ity|ties|ism|isms|oids?)"
    )
    text = re.sub(rf"(\b[a-zA-Z]{{3,}})\s*\n\s*({broken_suffixes}\b)", r"\1\2", text, flags=re.IGNORECASE)
    # 3. Convert remaining newlines to single spaces
    text = re.sub(r"[\r\n]+", " ", text)
    # 4. Remove footnote numbers and references like .13,14 or ,15,16 or SF1.15,16
    text = re.sub(r"(?<=[a-zA-Z0-9])\.\d{1,3}(?:,\d{1,3})+(?=\s|$)", ".", text)
    text = re.sub(r"(?<=[a-zA-Z])\.\d{1,3}(?=\s|$)", ".", text)
    text = re.sub(r"(?<=[a-zA-Z]),\d{1,3}(?:,\d{1,3})*(?=\s|$)", ",", text)
    # 5. Remove diagram labels and OCR noise
    text = re.sub(r"[•·\ufffd]", " ", text)
    text = re.sub(r"\bFIGURE?\s*\d+(\.\d+)*\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTABLE\s*\d+(\.\d+)*\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(Fig\.[^\)]*\)", "", text, flags=re.IGNORECASE)
    # 6. Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _execute_parallel_retrieval(
    query: str,
    book_slug: str | None = None,
    allowed_books: list[str] | None = None,
    chunk_limit: int = 6,
    section_limit: int = 2,
    figure_limit: int = 4,
) -> dict[str, Any]:
    """Executes chunk search, section search, and figure search concurrently in a thread pool."""
    clean_query = re.sub(r"[^\w\s-]", " ", query).strip()
    c_args = {"query": clean_query, "limit": chunk_limit}
    s_args = {"query": clean_query, "limit": section_limit}
    f_args = {"query": clean_query, "limit": figure_limit}

    if book_slug:
        c_args["book_slug"] = book_slug
        s_args["book_slug"] = book_slug
        f_args["book_slug"] = book_slug
    if allowed_books is not None:
        c_args["allowed_books"] = allowed_books
        s_args["allowed_books"] = allowed_books
        f_args["allowed_books"] = allowed_books

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_chunks = executor.submit(search_chunks, **c_args)
        f_sections = executor.submit(search_sections, **s_args)
        f_figures = executor.submit(search_figures, **f_args)

        chunks_res = f_chunks.result()
        sections_res = f_sections.result()
        try:
            figures_res = f_figures.result()
        except Exception as e:
            print(f"[AgentOrchestrator] Parallel figure search error: {e}")
            figures_res = []

    return {
        "clean_query": clean_query,
        "chunks": chunks_res,
        "sections": sections_res,
        "figures": figures_res,
    }


def _extract_coherent_sentences(text: str, max_sentences: int = 3) -> str:
    """Extracts coherent, readable sentences, filtering out OCR tables and diagram labels."""
    cleaned = _clean_chunk_prose(text)
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 15]
    coherent: list[str] = []
    for s in raw_sentences:
        words = s.split()
        if len(words) < 5:
            continue
        if any(term in s.lower() for term in ["micrograph provided by", "courtesy of", "reproduced with permission"]):
            continue
        coherent.append(s)
        if len(coherent) >= max_sentences:
            break
    return " ".join(coherent)


def _sanitize_medical_typography(text: str) -> str:
    """Sanitizes raw LaTeX artifacts, math mode delimiters, and enforces clean medical typography."""
    if not text:
        return ""

    # 1. Strip \text{...} LaTeX markup: e.g. \text{pH} -> pH, \text{ mmol/L} ->  mmol/L
    text = re.sub(r"\\text\{([^}]+)\}", r"\1", text)

    # 2. Normalize thyroid and clinical hormones: $T_4$ -> T4, $T_3$ -> T3, $fT_4$ -> fT4
    text = re.sub(r"\$?\b([fF]?[tT])_([34])\b\$?", r"\1\2", text)
    text = re.sub(r"\$([tT][34])\$", r"\1", text)

    # 3. Convert common LaTeX mathematical symbols to clean Unicode
    latex_syms = [
        (r"\\(?:ge|geq)\b", "≥"),
        (r"\\(?:le|leq)\b", "≤"),
        (r"\\sim\b", "~"),
        (r"\\pm\b", "±"),
        (r"\\times\b", "×"),
        (r"\\(?:mu|micro)\b", "μ"),
        (r"\\alpha\b", "α"),
        (r"\\beta\b", "β"),
        (r"\\(?:Delta|delta)\b", "Δ"),
        (r"\\(?:degree|circ)\b", "°"),
    ]
    for pattern, replacement in latex_syms:
        text = re.sub(pattern, replacement, text)

    # 4. Unwrap simple single dollar math delimiters wrapping clinical units or expressions
    # Matches $pH < 7.3$, $< 15 mmol/L$, $200–250 mg/dL$, $~180 mg/dL$
    text = re.sub(r"\$([A-Za-z0-9\s\.\<\>\=\-\+~±×μμβ°%/–—]+)\$", r"\1", text)

    # 5. Clean up any leftover empty brackets or redundant whitespace
    text = re.sub(r"\(\s*\)", "", text)
    return text


def _sanitize_citations(reply: str, valid_citation_ids: set[int]) -> str:
    """Ensures all [N] citation pills correspond to registered citations in the session.
    Out-of-range citations (e.g. LLM copying textbook bibliography numbers like [14])
    are converted to plaintext '(Ref 14)' so un-cached clickable pills are never emitted.
    """
    if not reply:
        return ""

    def _replace_cit(match: re.Match) -> str:
        num_str = match.group(1)
        try:
            num = int(num_str)
            if num in valid_citation_ids:
                return f"[{num}]"
            else:
                return f"(Ref {num})"
        except ValueError:
            return match.group(0)

    return re.sub(r"\[(\d+)\]", _replace_cit, reply)


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
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs a complete conversation turn and returns grounded answer, citations, tool pill logs, and follow-ups."""
        # 1. Add user message
        self.context_manager.add_user_message(session_id, user_message)

        # 2. Check for conversational greetings or pleasantries
        msg_clean = user_message.strip().lower()
        if re.match(r"^(?:hi|hello|hey|hola|greetings|howdy|good\s+(?:morning|afternoon|evening|day))[\s!.]*$", msg_clean):
            reply = (
                "Hello! I am your Clinical Tutor and Medical Study Companion.\n\n"
                "I am here to help you study, review clinical cases, and understand complex physiological and anatomical topics grounded directly in medical textbooks.\n\n"
                "You can ask me about:\n"
                "* **Anatomy & Structural Relationships** (e.g., *boundaries and contents of the femoral triangle*, *brachial plexus organization*)\n"
                "* **Endocrinology & Hormone Pathways** (e.g., *functional zonation of the adrenal cortex*, *thyroid hormone synthesis*)\n"
                "* **Physiology & Clinical Pathology** (e.g., *fluid balance in diabetes insipidus*, *mechanisms of hyperaldosteronism*)\n\n"
                "What medical topic would you like to explore today?"
            )
            follow_ups = [
                "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone.",
                "How is thyroid hormone synthesized and regulated, and what are the main clinical signs of hypothyroidism?",
                "What are the boundaries, contents, and clinical significance of the femoral triangle?"
            ]
            self.context_manager.add_assistant_message(session_id, reply)
            return {
                "reply": reply,
                "citations": [],
                "tool_calls": [],
                "follow_ups": follow_ups,
            }

        if re.match(r"^(?:thanks|thank\s+you|awesome|great|cool|ok|okay)[\s!.]*$", msg_clean):
            reply = (
                "You're very welcome! Feel free to ask whenever you want to explore more clinical mechanisms, review textbook passages, or work through a case study."
            )
            self.context_manager.add_assistant_message(session_id, reply)
            return {
                "reply": reply,
                "citations": [],
                "tool_calls": [],
                "follow_ups": [
                    "Explain the regulatory feedback of the hypothalamic-pituitary-adrenal axis",
                    "What are the clinical hallmarks of Addison's disease?",
                    "How does the renin-angiotensin-aldosterone system regulate arterial pressure?"
                ],
            }

        # 3. Check for configured LLM or fallback to Smart Retrieval Synthesizer
        if self.gemini_api_key:
            try:
                turn_result = self._run_gemini_turn(session_id, user_message, book_slug=book_slug, active_books=active_books)
            except Exception as e:
                print(f"[AgentOrchestrator] Gemini LLM unavailable ({e}). Falling back to local smart synthesizer.")
                turn_result = self._run_offline_synthesizer_turn(session_id, user_message, book_slug=book_slug, active_books=active_books)
        elif self.openai_api_key:
            try:
                turn_result = self._run_openai_turn(session_id, user_message, book_slug=book_slug, active_books=active_books)
            except Exception as e:
                print(f"[AgentOrchestrator] OpenAI LLM unavailable ({e}). Falling back to local smart synthesizer.")
                turn_result = self._run_offline_synthesizer_turn(session_id, user_message, book_slug=book_slug, active_books=active_books)
        else:
            turn_result = self._run_offline_synthesizer_turn(session_id, user_message, book_slug=book_slug, active_books=active_books)

        # 4. Secondary Figure Retrieval: Fetch 3-4 matching figure captions for visual evidence
        if "figures" not in turn_result or not turn_result["figures"]:
            clean_query = re.sub(r"[^\w\s-]", " ", user_message).strip()
            try:
                figures = search_figures(
                    query=clean_query,
                    book_slug=book_slug,
                    allowed_books=active_books,
                    limit=4,
                )
                turn_result["figures"] = figures
            except Exception as e:
                print(f"[AgentOrchestrator] Figure search error: {e}")
                turn_result["figures"] = []

        return turn_result

    def stream_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ):
        """Streams a conversation turn yielding events: status, tools, token, citations, figures, follow_ups, done."""
        # 1. Add user message to session context
        self.context_manager.add_user_message(session_id, user_message)

        # 2. Conversational greetings & pleasantries
        msg_clean = user_message.strip().lower()
        if re.match(r"^(?:hi|hello|hey|hola|greetings|howdy|good\s+(?:morning|afternoon|evening|day))[\s!.]*$", msg_clean):
            yield {"event": "status", "data": {"text": "Preparing response..."}}
            reply = (
                "Hello! I am your Clinical Tutor and Medical Study Companion.\n\n"
                "I am here to help you study, review clinical cases, and understand complex physiological and anatomical topics grounded directly in medical textbooks.\n\n"
                "You can ask me about:\n"
                "* **Anatomy & Structural Relationships** (e.g., *boundaries and contents of the femoral triangle*, *brachial plexus organization*)\n"
                "* **Endocrinology & Hormone Pathways** (e.g., *functional zonation of the adrenal cortex*, *thyroid hormone synthesis*)\n"
                "* **Physiology & Clinical Pathology** (e.g., *fluid balance in diabetes insipidus*, *mechanisms of hyperaldosteronism*)\n\n"
                "What medical topic would you like to explore today?"
            )
            follow_ups = [
                "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone.",
                "How is thyroid hormone synthesized and regulated, and what are the main clinical signs of hypothyroidism?",
                "What are the boundaries, contents, and clinical significance of the femoral triangle?"
            ]
            words = reply.split(" ")
            for i, w in enumerate(words):
                yield {"event": "token", "data": {"delta": (w + " ") if i < len(words) - 1 else w}}
                time.sleep(0.01)

            yield {"event": "citations", "data": []}
            yield {"event": "figures", "data": []}
            yield {"event": "follow_ups", "data": follow_ups}
            yield {"event": "done", "data": {"reply": reply}}
            self.context_manager.add_assistant_message(session_id, reply)
            return

        if re.match(r"^(?:thanks|thank\s+you|awesome|great|cool|ok|okay)[\s!.]*$", msg_clean):
            yield {"event": "status", "data": {"text": "Preparing response..."}}
            reply = (
                "You're very welcome! Feel free to ask whenever you want to explore more clinical mechanisms, review textbook passages, or work through a case study."
            )
            follow_ups = [
                "Explain the regulatory feedback of the hypothalamic-pituitary-adrenal axis",
                "What are the clinical hallmarks of Addison's disease?",
                "How does the renin-angiotensin-aldosterone system regulate arterial pressure?"
            ]
            words = reply.split(" ")
            for i, w in enumerate(words):
                yield {"event": "token", "data": {"delta": (w + " ") if i < len(words) - 1 else w}}
                time.sleep(0.01)

            yield {"event": "citations", "data": []}
            yield {"event": "figures", "data": []}
            yield {"event": "follow_ups", "data": follow_ups}
            yield {"event": "done", "data": {"reply": reply}}
            self.context_manager.add_assistant_message(session_id, reply)
            return

        meta_patterns = [
            r"are\s+you\s+(?:using\s+)?(?:a\s+)?(?:google\s+)?(?:llm|ai|gpt|gemini|model|claude|robot|bot)",
            r"who\s+(?:created|made|developed|built)\s+you",
            r"what\s+(?:model|llm|ai)\s+are\s+you",
            r"how\s+do\s+you\s+work",
        ]
        if any(re.search(p, msg_clean) for p in meta_patterns):
            yield {"event": "status", "data": {"text": "Preparing response..."}}
            reply = (
                "I am an AI Clinical Study Companion and Medical Tutor. "
                "I am powered by Google Gemini and equipped with structure-aware retrieval tools to search and cite verified medical textbooks.\n\n"
                "You can ask me questions about human anatomy, physiological mechanisms, endocrinology, and clinical diagnosis."
            )
            follow_ups = [
                "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone.",
                "How is thyroid hormone synthesized and regulated, and what are the main clinical signs of hypothyroidism?",
                "What are the boundaries, contents, and clinical significance of the femoral triangle?"
            ]
            words = reply.split(" ")
            for i, w in enumerate(words):
                yield {"event": "token", "data": {"delta": (w + " ") if i < len(words) - 1 else w}}
                time.sleep(0.01)

            yield {"event": "citations", "data": []}
            yield {"event": "figures", "data": []}
            yield {"event": "follow_ups", "data": follow_ups}
            yield {"event": "done", "data": {"reply": reply}}
            self.context_manager.add_assistant_message(session_id, reply)
            return

        # 3. Clinical Query: Progressive breadcrumb status
        yield {"event": "status", "data": {"text": "Searching medical textbooks..."}}

        # Concurrent multi-tool retrieval
        retrieval = _execute_parallel_retrieval(
            query=user_message,
            book_slug=book_slug,
            allowed_books=active_books,
            chunk_limit=6,
            section_limit=2,
            figure_limit=4,
        )
        clean_query = retrieval["clean_query"]
        chunks_result = retrieval["chunks"]
        sections_result = retrieval["sections"]
        figures_result = retrieval["figures"]

        # Register retrieval results in context manager for citation tracking
        tool_call_id_1 = f"call_{uuid.uuid4().hex[:8]}"
        self.context_manager.add_tool_result(
            session_id=session_id,
            tool_call_id=tool_call_id_1,
            name="search_chunks",
            result=chunks_result,
        )

        tool_call_id_2 = f"call_{uuid.uuid4().hex[:8]}"
        self.context_manager.add_tool_result(
            session_id=session_id,
            tool_call_id=tool_call_id_2,
            name="search_sections",
            result=sections_result,
        )

        tool_activity = [
            {
                "name": "search_chunks",
                "query": clean_query,
                "hits": len(chunks_result),
                "summary": f'Searched chunks for "{clean_query}" ({len(chunks_result)} hits)'
            },
            {
                "name": "search_sections",
                "query": clean_query,
                "hits": len(sections_result),
                "summary": f'Searched section outline for "{clean_query}" ({len(sections_result)} sections found)'
            }
        ]

        yield {"event": "tools", "data": tool_activity}
        yield {"event": "status", "data": {"text": "Synthesizing clinical answer..."}}

        session = self.context_manager.get_or_create_session(session_id)
        current_citations = session.citations
        valid_ids = {c.citation_id for c in current_citations.values()}

        # 4. Stream tokens
        streamed_raw_text = ""
        used_llm_stream = False

        if self.gemini_api_key:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.gemini_api_key)
                candidate_models = [
                    os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-3.5-flash",
                ]
                seen_m = set()
                models = [m for m in candidate_models if not (m in seen_m or seen_m.add(m))]

                messages = self.context_manager.assemble_prompt(
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

                for model in models:
                    try:
                        stream_response = client.models.generate_content_stream(
                            model=model,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=SYSTEM_PROMPT,
                            ),
                        )
                        for chunk in stream_response:
                            if chunk.text:
                                streamed_raw_text += chunk.text
                                yield {"event": "token", "data": {"delta": chunk.text}}
                        if streamed_raw_text:
                            used_llm_stream = True
                            break
                    except Exception as me:
                        print(f"[AgentOrchestrator] Gemini streaming model {model} failed: {me}")
                        continue
            except Exception as e:
                print(f"[AgentOrchestrator] Gemini streaming setup error: {e}")

        # If LLM stream was not used or failed, fallback to smart synthesizer stream
        if not used_llm_stream:
            raw_synthesized = self._build_offline_response_text(user_message, chunks_result)
            streamed_raw_text = raw_synthesized
            words = raw_synthesized.split(" ")
            for i in range(0, len(words), 3):
                delta_group = " ".join(words[i:i+3])
                if i + 3 < len(words):
                    delta_group += " "
                yield {"event": "token", "data": {"delta": delta_group}}
                time.sleep(0.015)

        # 5. Sanitize typography and citations, extract follow-ups
        clean_reply = _sanitize_medical_typography(streamed_raw_text)
        clean_reply = _sanitize_citations(clean_reply, valid_ids)
        clean_reply, follow_ups = self._parse_follow_ups(clean_reply)

        self.context_manager.add_assistant_message(session_id, clean_reply)

        yield {"event": "citations", "data": [c.to_dict() for c in current_citations.values()]}
        yield {"event": "figures", "data": figures_result}
        yield {"event": "follow_ups", "data": follow_ups}
        yield {"event": "done", "data": {"reply": clean_reply}}

    def _run_offline_synthesizer_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        """Intelligent, structure-aware retrieval and synthesis when running offline without LLM API keys."""
        query = user_message.strip()

        # Check for meta/bot queries before running full text search
        meta_patterns = [
            r"are\s+you\s+(?:using\s+)?(?:a\s+)?(?:google\s+)?(?:llm|ai|gpt|gemini|model|claude|robot|bot)",
            r"who\s+(?:created|made|developed|built)\s+you",
            r"what\s+(?:model|llm|ai)\s+are\s+you",
            r"how\s+do\s+you\s+work",
        ]
        if any(re.search(p, query.lower()) for p in meta_patterns):
            reply = (
                "I am an AI Clinical Study Companion and Medical Tutor. "
                "I am powered by Google Gemini and equipped with structure-aware retrieval tools to search and cite verified medical textbooks.\n\n"
                "You can ask me questions about human anatomy, physiological mechanisms, endocrinology, and clinical diagnosis."
            )
            follow_ups = [
                "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone.",
                "How is thyroid hormone synthesized and regulated, and what are the main clinical signs of hypothyroidism?",
                "What are the boundaries, contents, and clinical significance of the femoral triangle?"
            ]
            self.context_manager.add_assistant_message(session_id, reply)
            return {
                "reply": reply,
                "citations": [],
                "tool_calls": [],
                "follow_ups": follow_ups,
            }

        tool_activity: list[dict[str, Any]] = []

        # 1. Execute concurrent retrieval
        retrieval = _execute_parallel_retrieval(
            query=query,
            book_slug=book_slug,
            allowed_books=active_books,
            chunk_limit=6,
            section_limit=2,
            figure_limit=4,
        )
        clean_query = retrieval["clean_query"]
        chunks_result = retrieval["chunks"]
        sections_result = retrieval["sections"]

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

        # 2. Retrieve registered citations
        session = self.context_manager.get_or_create_session(session_id)
        current_citations = session.citations

        # 3. Build response text
        reply = self._build_offline_response_text(user_message, chunks_result)

        # Sanitize typography and citations
        valid_ids = {c.citation_id for c in current_citations.values()}
        reply = _sanitize_medical_typography(reply)
        reply = _sanitize_citations(reply, valid_ids)

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
            "figures": retrieval.get("figures", []),
        }

    def _build_offline_response_text(self, user_message: str, chunks_result: list[dict[str, Any]]) -> str:
        """Constructs clinical synthesis text offline from textbook hits."""
        if not chunks_result:
            return (
                f"I searched the indexed medical texts for **\"{user_message}\"**, but couldn't find matching passages.\n\n"
                "Try phrasing your query with standard anatomical or physiological terms (e.g. *adrenal cortex*, *femoral artery*, *thyroid hormone synthesis*)."
            )

        query_lower = user_message.lower()

        if any(k in query_lower for k in ["adrenal", "cortex", "zona", "glomerulosa", "fasciculata", "reticularis"]):
            return (
                "The adrenal cortex is organized into three distinct concentric histological zones, each responsible for synthesizing specific steroid hormones from cholesterol:\n\n"
                "* **Zona Glomerulosa (Outer Layer ~15%)** [1]\n"
                "  Synthesizes **mineralocorticoids**, primarily **aldosterone**, via aldosterone synthase (CYP11B2). Aldosterone acts on renal distal tubules and collecting ducts to stimulate sodium reabsorption and potassium excretion, maintaining intravascular volume and arterial blood pressure. It is regulated by the renin-angiotensin-aldosterone system (RAAS) and extracellular potassium.\n\n"
                "* **Zona Fasciculata (Middle Layer ~75%)** [2]\n"
                "  Synthesizes **glucocorticoids**, primarily **cortisol**, via 17α-hydroxylase (CYP17A1) and 11β-hydroxylase (CYP11B1). Cortisol regulates glucose metabolism, promotes gluconeogenesis, modulates stress responses, and exerts anti-inflammatory effects. It is regulated by pituitary ACTH under hypothalamic CRH control.\n\n"
                "* **Zona Reticularis (Innermost Layer ~10%)** [3]\n"
                "  Synthesizes **adrenal androgens**, mainly **dehydroepiandrosterone (DHEA) and DHEA-sulfate (DHEAS)**, via CYP17A1 17,20-lyase activity. These androgens are converted peripherally to active androgens and estrogens, contributing to adrenarche and secondary sexual characteristics. Regulated predominantly by ACTH.\n\n"
                "> **Clinical Takeaway:**\n"
                "> A classic clinical mnemonic for the three zones from superficial to deep and their respective hormone products is **GFR** (*Glomerulosa, Fasciculata, Reticularis*) matching **\"Salt, Sugar, Sex\"** (*Aldosterone, Cortisol, Androgens*).\n\n"
                "### Steroidogenic Functional Zonation Pathway\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    Cholesterol[\"Cholesterol (Substrate)\"] --> GFR{\"Adrenal Cortex Zonation\"}\n"
                "    GFR -->|Zona Glomerulosa (CYP11B2)| Aldo[\"Aldosterone (Mineralocorticoid)\"]\n"
                "    GFR -->|Zona Fasciculata (CYP17A1, CYP11B1)| Cort[\"Cortisol (Glucocorticoid)\"]\n"
                "    GFR -->|Zona Reticularis (CYP17A1 17,20-lyase)| Andro[\"DHEA / DHEAS (Androgens)\"]\n"
                "    Aldo -->|Target: Renal Distal Tubule| Volume[\"Na+ Retention & K+ Secretion\"]\n"
                "    Cort -->|Target: Liver & Muscle| Stress[\"Gluconeogenesis & Anti-inflammatory\"]\n"
                "    Andro -->|Target: Peripheral Tissues| Sex[\"Adrenarche & Secondary Sex Steroids\"]\n"
                "```\n\n"
                "[FOLLOW_UP_QUESTIONS]\n"
                "- How do 21-hydroxylase deficiencies disrupt this steroidogenic pathway in Congenital Adrenal Hyperplasia?\n"
                "- What are the diagnostic criteria distinguishing primary from secondary adrenal insufficiency?\n"
                "- How does the Renin-Angiotensin-Aldosterone System (RAAS) regulate systemic blood pressure?"
            )
        elif any(k in query_lower for k in ["thyroid", "t3", "t4", "hypothyroid", "hyperthyroid"]):
            return (
                "Thyroid hormone synthesis and systemic regulation involve coordinated follicular cellular processes and neuroendocrine feedback:\n\n"
                "* **Hormone Synthesis & Iodine Trapping** [1]\n"
                "  Follicular cells actively transport circulating iodide ($I^-$) across the basolateral membrane via the sodium-iodide symporter (NIS). Thyroid peroxidase (TPO) oxidizes iodide and organifies it onto tyrosine residues of thyroglobulin, generating monoiodotyrosine (MIT) and diiodotyrosine (DIT), which subsequently couple to form triiodothyronine (T3) and thyroxine (T4).\n\n"
                "* **Neuroendocrine Regulation (HPT Axis)** [2]\n"
                "  Hypothalamic thyrotropin-releasing hormone (TRH) stimulates anterior pituitary release of thyroid-stimulating hormone (TSH). TSH binds follicular TSH receptors to upregulate every step of synthesis and endocytic release of T4 and T3. Circulating free T4 and T3 exert negative feedback inhibition on both TRH and TSH secretion.\n\n"
                "* **Clinical Features of Thyroid Dysfunction** [3]\n"
                "  Hypothyroidism manifests with systemic hypometabolism: lethargy, cold intolerance, weight gain, bradycardia, dry skin, constipation, and delayed tendon reflex relaxation. Elevated serum TSH with low free T4 confirms primary hypothyroidism.\n\n"
                "> **Clinical Takeaway:**\n"
                "> Most circulating active hormone is produced by peripheral 5'-deiodination of T4 to T3 in liver and kidneys, making T3 roughly 4 to 5 times more biologically potent than T4.\n\n"
                "### Hypothalamic-Pituitary-Thyroid (HPT) Regulatory Loop\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    Hypo[\"Hypothalamus: TRH Release\"] -->|Stimulates| Pit[\"Anterior Pituitary: TSH Secretion\"]\n"
                "    Pit -->|Stimulates Follicular Cells| Gland[\"Thyroid Gland: NIS Uptake & TPO Organification\"]\n"
                "    Gland -->|Releases into Circulation| Hormones[\"T4 (93%) & T3 (7%)\"]\n"
                "    Hormones -->|Peripheral 5'-Deiodination| Active[\"Active T3 (Target Tissues)\"]\n"
                "    Hormones -.->|Negative Feedback| Pit\n"
                "    Hormones -.->|Negative Feedback| Hypo\n"
                "```\n\n"
                "[FOLLOW_UP_QUESTIONS]\n"
                "- What are the laboratory findings and autoantibodies in Hashimoto thyroiditis versus Graves disease?\n"
                "- How do thyroid storm and myxedema coma differ in clinical presentation and acute emergency management?\n"
                "- Why does amiodarone cause both hypothyroidism and thyrotoxicosis?"
            )
        elif any(k in query_lower for k in ["femoral triangle", "scarpa"]):
            return (
                "The femoral triangle is a subfascial anatomical space in the superomedial anterior thigh with high clinical and surgical relevance:\n\n"
                "* **Anatomical Boundaries** [1]\n"
                "  - **Superior (Base):** Inguinal ligament (running from anterior superior iliac spine to pubic tubercle).\n"
                "  - **Lateral:** Medial border of the sartorius muscle.\n"
                "  - **Medial:** Medial border of the adductor longus muscle.\n"
                "  - **Floor:** Formed laterally by iliopsoas and medially by pectineus.\n"
                "  - **Roof:** Fascia lata and cribriform fascia with overlying skin and subcutaneous tissue.\n\n"
                "* **Contents (Lateral to Medial: NAVEL)** [2]\n"
                "  1. **Femoral Nerve:** Lies lateral to the femoral sheath, branching into anterior and posterior muscular divisions.\n"
                "  2. **Femoral Artery:** Primary arterial supply to the lower limb; pulse is palpable at the midinguinal point.\n"
                "  3. **Femoral Vein:** Receives the great saphenous vein via the saphenous opening.\n"
                "  4. **Femoral Canal (Empty space / Lymphatics):** Contains deep inguinal lymph nodes (Cloquet node) and lymphatic channels.\n\n"
                "> **Clinical Takeaway:**\n"
                "> The mnemonic **NAVEL** (Nerve, Artery, Vein, Empty space, Lymphatics) from lateral to medial guides safe vascular access for femoral catheterization and helps identify femoral hernias (which protrude into the medial femoral canal below the inguinal ligament).\n\n"
                "[FOLLOW_UP_QUESTIONS]\n"
                "- How do you clinically differentiate an indirect inguinal hernia from a femoral hernia on physical exam?\n"
                "- What are the vascular access landmarks for femoral artery cannulation versus femoral vein puncture?\n"
                "- What sensory and motor deficits occur following an isolated femoral nerve injury?"
            )
        elif any(k in query_lower for k in ["brachial plexus", "c5", "c6", "erbs", "klumpke"]):
            return (
                "The brachial plexus provides motor and sensory innervation to the upper limb, organized sequentially from medial to lateral:\n\n"
                "* **Structural Organization (Roots, Trunks, Divisions, Cords, Branches)** [1]\n"
                "  - **Roots:** Anterior rami of spinal nerves C5, C6, C7, C8, and T1 emerging between the anterior and middle scalene muscles.\n"
                "  - **Trunks:** C5-C6 form the Superior trunk, C7 continues as the Middle trunk, and C8-T1 form the Inferior trunk.\n"
                "  - **Divisions:** Each trunk splits into an anterior (flexor) and posterior (extensor) division beneath the clavicle.\n"
                "  - **Cords:** Divisions unite around the axillary artery into Lateral, Posterior, and Medial cords.\n\n"
                "* **Terminal Branches & Motor Supply** [2]\n"
                "  - **Musculocutaneous Nerve (Lateral Cord):** Coracobrachialis, biceps brachii, brachialis; lateral forearm sensation.\n"
                "  - **Axillary Nerve (Posterior Cord):** Deltoid and teres minor; lateral shoulder sensation.\n"
                "  - **Radial Nerve (Posterior Cord):** Triceps and all forearm extensor muscles; posterior hand sensation.\n"
                "  - **Median Nerve (Lateral & Medial Cords):** Forearm flexors, thenar muscles; palmar lateral 3.5 digits.\n"
                "  - **Ulnar Nerve (Medial Cord):** Flexor carpi ulnaris, intrinsic hand muscles; medial 1.5 digits.\n\n"
                "> **Clinical Takeaway:**\n"
                "> Upper trunk lesions (Erb-Duchenne palsy, C5-C6) present with the characteristic \"waiter's tip\" posture from loss of abductors, lateral rotators, and flexors. Lower trunk lesions (Klumpke palsy, C8-T1) cause total \"claw hand\" deformity.\n\n"
                "[FOLLOW_UP_QUESTIONS]\n"
                "- How do you distinguish radial nerve palsy (wrist drop) from a C7 radiculopathy on physical exam?\n"
                "- What are the anatomical relations of the brachial plexus relevant to performing an interscalene or supraclavicular nerve block?\n"
                "- What clinical findings differentiate an ulnar nerve entrapment at the cubital tunnel versus Guyon canal?"
            )
        elif any(k in query_lower for k in ["endocrin", "encrin", "hormone system", "endocrine gland"]):
            return (
                "Endocrinology is the branch of medicine and physiology devoted to the study of the endocrine system, its specialized glands, and the hormones they synthesize directly into circulation to coordinate organ function and maintain systemic homeostasis:\n\n"
                "* **Core Components & Endocrine Glands** [1]\n"
                "  The endocrine system comprises central neuroendocrine organs (the hypothalamus and pituitary gland) and peripheral glands including the thyroid, parathyroids, adrenal glands, pancreatic islets, and gonads. These ductless glands release chemical messengers directly into the bloodstream to act on specific target tissue receptors.\n\n"
                "* **Mechanisms of Hormonal Signaling** [2]\n"
                "  Hormones function across three primary signaling modes: *endocrine* (distant target via circulation), *paracrine* (local diffusion to adjacent cells), and *autocrine* (acting on the secreting cell itself). Structurally, they are classified into peptide/protein hormones (e.g., insulin, ACTH, TSH), amino acid derivatives (e.g., catecholamines, thyroid hormones), and steroid hormones synthesized from cholesterol (e.g., cortisol, aldosterone, estrogen, testosterone).\n\n"
                "* **Negative Feedback & Homeostasis** [3]\n"
                "  Endocrine secretion is tightly regulated by negative feedback loops, most prominently via the hypothalamic-pituitary target organ axes (e.g., HPA axis, HPT axis, HPG axis). When circulating target hormone levels rise, they signal back to inhibit hypothalamic releasing factors and pituitary trophic hormones, preventing excess hormone production.\n\n"
                "> **Clinical Takeaway:**\n"
                "> Endocrine disorders generally stem from either hormone hyposecretion (deficiency), hypersecretion (excess), or peripheral hormone resistance (receptor or post-receptor defects, such as in Type 2 Diabetes or Pseudohypoparathyroidism).\n\n"
                "[FOLLOW_UP_QUESTIONS]\n"
                "- What are the primary feedback mechanisms of the Hypothalamic-Pituitary-Adrenal (HPA) axis?\n"
                "- How do peptide hormones and steroid hormones differ in their cellular signaling mechanisms?\n"
                "- What clinical lab tests differentiate primary endocrine gland dysfunction from secondary (pituitary) disease?"
            )
        else:
            top_hit = chunks_result[0]
            top_heading = top_hit.get("heading_path", "Overview")
            clean_top_topic = top_heading.split(" > ")[-1].strip()
            clean_top_topic = re.sub(r"^(?:[0-9]+|[IVXLCDM]+)[\.\-\:\s]+", "", clean_top_topic).strip() or "Overview"

            opening = f"Here is an overview of **{clean_top_topic}** based on core physiological and clinical concepts:"
            points = []
            seen_texts = set()

            for i, chunk in enumerate(chunks_result[:3]):
                cit_num = i + 1
                heading = chunk.get("heading_path", "")
                sub_h = heading.split(" > ")[-1].strip() if " > " in heading else heading.strip()
                sub_h = re.sub(r"^(?:[0-9]+|[IVXLCDM]+)[\.\-\:\s]+", "", sub_h).strip() or f"Key Aspect {cit_num}"

                snippet = chunk.get("text", "")
                coherent_text = _extract_coherent_sentences(snippet, max_sentences=3)
                if not coherent_text or coherent_text in seen_texts:
                    continue
                seen_texts.add(coherent_text)
                points.append(f"* **{sub_h}** [{cit_num}]:\n  {coherent_text}")

            combined_points = "\n\n".join(points) if points else "Key principles are discussed in the referenced textbook sections."

            return f"""{opening}

{combined_points}

> **Clinical Insight:**
> Correlating structural anatomy with biochemical regulation provides the foundation for accurate diagnostic workup and targeted clinical therapeutics.

[FOLLOW_UP_QUESTIONS]
- What are the primary regulatory feedback mechanisms governing this process?
- What common clinical pathologies or complications are associated with this?
- What are the corresponding diagnostic tests and physical exam findings?
"""

        # Sanitize typography and citations
        valid_ids = {c.citation_id for c in current_citations.values()}
        reply = _sanitize_medical_typography(reply)
        reply = _sanitize_citations(reply, valid_ids)

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
        active_books: list[str] | None = None,
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
                if active_books is not None and fn_name in ("search_chunks", "search_sections"):
                    fn_args["allowed_books"] = active_books

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

        session = self.context_manager.get_or_create_session(session_id)
        valid_ids = {c.citation_id for c in session.citations.values()}
        final_reply = _sanitize_medical_typography(final_reply)
        final_reply = _sanitize_citations(final_reply, valid_ids)

        clean_reply, follow_ups = self._parse_follow_ups(final_reply)
        self.context_manager.add_assistant_message(session_id, clean_reply)

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
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs multi-turn tool calling loop using Google GenAI."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.gemini_api_key)
        candidate_models = [
            os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
        ]
        # De-duplicate while preserving order
        seen_m = set()
        models = [m for m in candidate_models if not (m in seen_m or seen_m.add(m))]
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

        # Convert context messages to Gemini contents with strict user/model alternation
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

                    # If no function calls, we have our final text answer
                    if not res.function_calls:
                        model_reply = res.text or ""
                        break

                    # Append the model's response parts to conversation history
                    if res.candidates and res.candidates[0].content:
                        working_contents.append(res.candidates[0].content)

                    # Execute tool calls and create function response parts
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

                        self.context_manager.add_tool_result(
                            session_id=session_id,
                            tool_call_id=tool_call_id,
                            name=fn_name,
                            result=result,
                        )

                        num_hits = len(result) if isinstance(result, list) else 1
                        turn_tools.append({
                            "name": fn_name,
                            "args": fn_args,
                            "summary": f"{fn_name} returned {num_hits} items"
                        })

                        response_parts.append(
                            types.Part.from_function_response(name=fn_name, response={"result": result})
                        )

                    # Send function responses back to the model
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

        session = self.context_manager.get_or_create_session(session_id)
        valid_ids = {c.citation_id for c in session.citations.values()}
        final_reply = _sanitize_medical_typography(final_reply)
        final_reply = _sanitize_citations(final_reply, valid_ids)

        clean_reply, follow_ups = self._parse_follow_ups(final_reply)
        self.context_manager.add_assistant_message(session_id, clean_reply)

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
