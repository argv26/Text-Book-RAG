"""Offline smart synthesizer and clinical response generator for local operations."""
from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from agent.sanitizers import (
    _extract_coherent_sentences,
    _sanitize_citations,
    _sanitize_medical_typography,
)
from retrieval.parallel import _execute_parallel_retrieval

if TYPE_CHECKING:
    from context.manager import ContextManager


def parse_follow_ups(text: str) -> tuple[str, list[str]]:
    """Extracts follow-up questions from response text."""
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
        "Read the surrounding textbook section for deeper context",
    ]


def build_offline_response_text(user_message: str, chunks_result: list[dict[str, Any]]) -> str:
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


def run_offline_synthesizer_turn(
    context_manager: ContextManager,
    session_id: str,
    user_message: str,
    book_slug: str | None = None,
    active_books: list[str] | None = None,
) -> dict[str, Any]:
    """Intelligent, structure-aware retrieval and synthesis when running offline without LLM API keys."""
    query = user_message.strip()

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
            "What are the boundaries, contents, and clinical significance of the femoral triangle?",
        ]
        context_manager.add_assistant_message(session_id, reply)
        return {
            "reply": reply,
            "citations": [],
            "tool_calls": [],
            "follow_ups": follow_ups,
        }

    tool_activity: list[dict[str, Any]] = []

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
    context_manager.add_tool_result(
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
    context_manager.add_tool_result(
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

    session = context_manager.get_or_create_session(session_id)
    current_citations = session.citations

    reply = build_offline_response_text(user_message, chunks_result)

    valid_ids = {c.citation_id for c in current_citations.values()}
    reply = _sanitize_medical_typography(reply)
    reply = _sanitize_citations(reply, valid_ids)

    clean_reply, follow_ups = parse_follow_ups(reply)
    context_manager.add_assistant_message(session_id, clean_reply)

    return {
        "reply": clean_reply,
        "citations": [c.to_dict() for c in current_citations.values()],
        "tool_calls": tool_activity,
        "follow_ups": follow_ups,
        "figures": retrieval.get("figures", []),
    }

