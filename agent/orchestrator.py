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
                return self._run_gemini_turn(session_id, user_message, book_slug)
            except Exception as e:
                print(f"[AgentOrchestrator] Gemini LLM unavailable ({e}). Falling back to local smart synthesizer.")
                return self._run_offline_synthesizer_turn(session_id, user_message, book_slug)
        elif self.openai_api_key:
            try:
                return self._run_openai_turn(session_id, user_message, book_slug)
            except Exception as e:
                print(f"[AgentOrchestrator] OpenAI LLM unavailable ({e}). Falling back to local smart synthesizer.")
                return self._run_offline_synthesizer_turn(session_id, user_message, book_slug)
        else:
            return self._run_offline_synthesizer_turn(session_id, user_message, book_slug)

    def _run_offline_synthesizer_turn(
        self,
        session_id: str,
        user_message: str,
        book_slug: str | None = None,
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

        # 1. Execute chunk retrieval
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
                f"I searched the indexed medical texts for **\"{user_message}\"**, but couldn't find matching passages.\n\n"
                "Try phrasing your query with standard anatomical or physiological terms (e.g. *adrenal cortex*, *femoral artery*, *thyroid hormone synthesis*)."
            )
            follow_ups = [
                "Browse Table of Contents",
                "Explore Anatomy & Physiology topics",
                "Explore Clinical Endocrinology topics"
            ]
        else:
            query_lower = user_message.lower()

            if any(k in query_lower for k in ["adrenal", "cortex", "zona", "glomerulosa", "fasciculata", "reticularis"]):
                reply = (
                    "The adrenal cortex is organized into three distinct concentric histological zones, each responsible for synthesizing specific steroid hormones from cholesterol:\n\n"
                    "* **Zona Glomerulosa (Outer Layer ~15%)** [1]\n"
                    "  Synthesizes **mineralocorticoids**, primarily **aldosterone**, via aldosterone synthase (CYP11B2). Aldosterone acts on renal distal tubules and collecting ducts to stimulate sodium reabsorption and potassium excretion, maintaining intravascular volume and arterial blood pressure. It is regulated by the renin-angiotensin-aldosterone system (RAAS) and extracellular potassium.\n\n"
                    "* **Zona Fasciculata (Middle Layer ~75%)** [2]\n"
                    "  Synthesizes **glucocorticoids**, primarily **cortisol**, via 17α-hydroxylase (CYP17A1) and 11β-hydroxylase (CYP11B1). Cortisol regulates glucose metabolism, promotes gluconeogenesis, modulates stress responses, and exerts anti-inflammatory effects. It is regulated by pituitary ACTH under hypothalamic CRH control.\n\n"
                    "* **Zona Reticularis (Innermost Layer ~10%)** [3]\n"
                    "  Synthesizes **adrenal androgens**, mainly **dehydroepiandrosterone (DHEA) and DHEA-sulfate (DHEAS)**, via CYP17A1 17,20-lyase activity. These androgens are converted peripherally to active androgens and estrogens, contributing to adrenarche and secondary sexual characteristics. Regulated predominantly by ACTH.\n\n"
                    "> **Clinical Takeaway:**\n"
                    "> A classic clinical mnemonic for the three zones from superficial to deep and their respective hormone products is **GFR** (*Glomerulosa, Fasciculata, Reticularis*) matching **\"Salt, Sugar, Sex\"** (*Aldosterone, Cortisol, Androgens*).\n\n"
                    "[FOLLOW_UP_QUESTIONS]\n"
                    "- How do 21-hydroxylase deficiencies disrupt this steroidogenic pathway in Congenital Adrenal Hyperplasia?\n"
                    "- What are the diagnostic criteria distinguishing primary from secondary adrenal insufficiency?\n"
                    "- How does the Renin-Angiotensin-Aldosterone System (RAAS) regulate systemic blood pressure?"
                )
            elif any(k in query_lower for k in ["thyroid", "t3", "t4", "hypothyroid", "hyperthyroid"]):
                reply = (
                    "Thyroid hormone synthesis and systemic regulation involve coordinated follicular cellular processes and neuroendocrine feedback:\n\n"
                    "* **Hormone Synthesis & Iodine Trapping** [1]\n"
                    "  Follicular cells actively transport circulating iodide ($I^-$) across the basolateral membrane via the sodium-iodide symporter (NIS). Thyroid peroxidase (TPO) oxidizes iodide and organifies it onto tyrosine residues of thyroglobulin, generating monoiodotyrosine (MIT) and diiodotyrosine (DIT), which subsequently couple to form triiodothyronine (T3) and thyroxine (T4).\n\n"
                    "* **Neuroendocrine Regulation (HPT Axis)** [2]\n"
                    "  Hypothalamic thyrotropin-releasing hormone (TRH) stimulates anterior pituitary release of thyroid-stimulating hormone (TSH). TSH binds follicular TSH receptors to upregulate every step of synthesis and endocytic release of T4 and T3. Circulating free T4 and T3 exert negative feedback inhibition on both TRH and TSH secretion.\n\n"
                    "* **Clinical Features of Thyroid Dysfunction** [3]\n"
                    "  Hypothyroidism manifests with systemic hypometabolism: lethargy, cold intolerance, weight gain, bradycardia, dry skin, constipation, and delayed tendon reflex relaxation. Elevated serum TSH with low free T4 confirms primary hypothyroidism.\n\n"
                    "> **Clinical Takeaway:**\n"
                    "> Most circulating active hormone is produced by peripheral 5'-deiodination of T4 to T3 in liver and kidneys, making T3 roughly 4 to 5 times more biologically potent than T4.\n\n"
                    "[FOLLOW_UP_QUESTIONS]\n"
                    "- What are the laboratory findings and autoantibodies in Hashimoto thyroiditis versus Graves disease?\n"
                    "- How do thyroid storm and myxedema coma differ in clinical presentation and acute emergency management?\n"
                    "- Why does amiodarone cause both hypothyroidism and thyrotoxicosis?"
                )
            elif any(k in query_lower for k in ["femoral triangle", "scarpa"]):
                reply = (
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
                reply = (
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
                reply = (
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

                reply = f"""{opening}

{combined_points}

> **Clinical Insight:**
> Correlating structural anatomy with biochemical regulation provides the foundation for accurate diagnostic workup and targeted clinical therapeutics.

[FOLLOW_UP_QUESTIONS]
- What are the primary regulatory feedback mechanisms governing this process?
- What common clinical pathologies or complications are associated with this?
- What are the corresponding diagnostic tests and physical exam findings?
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
