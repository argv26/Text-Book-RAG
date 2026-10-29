"""Agent Orchestrator connecting ContextManager, Retrieval Tools, and LLMs."""
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any

from agent.offline import (
    build_offline_response_text,
    parse_follow_ups,
    run_offline_synthesizer_turn,
)
from agent.prompts import SYSTEM_PROMPT
from agent.providers import run_gemini_turn, run_openai_turn
from agent.sanitizers import (
    _clean_chunk_prose,
    _extract_coherent_sentences,
    _sanitize_citations,
    _sanitize_medical_typography,
)
from agent.tools_spec import TOOL_DEFINITIONS, execute_tool
from context.manager import ContextManager
from context.models import CitationRecord
from retrieval.parallel import _execute_parallel_retrieval
from retrieval.tools import search_figures

__all__ = [
    "AgentOrchestrator",
    "TOOL_DEFINITIONS",
    "execute_tool",
    "_clean_chunk_prose",
    "_execute_parallel_retrieval",
    "_extract_coherent_sentences",
    "_sanitize_medical_typography",
    "_sanitize_citations",
]


class AgentOrchestrator:
    """Orchestrates multi-turn conversation, structure-aware tool invocation, and LLM responses."""

    def __init__(self, context_manager: ContextManager | None = None):
        self.context_manager = context_manager or ContextManager()
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")

    def _parse_follow_ups(self, text: str) -> tuple[str, list[str]]:
        return parse_follow_ups(text)

    def _build_offline_response_text(self, user_message: str, chunks_result: list[dict[str, Any]]) -> str:
        return build_offline_response_text(user_message, chunks_result)

    def _run_offline_synthesizer_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        return run_offline_synthesizer_turn(
            context_manager=self.context_manager,
            session_id=session_id,
            user_message=user_message,
            book_slug=book_slug,
            active_books=active_books,
        )

    def _run_openai_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        return run_openai_turn(
            context_manager=self.context_manager,
            session_id=session_id,
            user_message=user_message,
            api_key=self.openai_api_key,
            book_slug=book_slug,
            active_books=active_books,
        )

    def _run_gemini_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        return run_gemini_turn(
            context_manager=self.context_manager,
            session_id=session_id,
            user_message=user_message,
            api_key=self.gemini_api_key,
            book_slug=book_slug,
            active_books=active_books,
        )

    def run_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
        active_books: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs a complete conversation turn returning structured response."""
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
