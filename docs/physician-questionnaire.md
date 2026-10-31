# Physician Domain-Expert Questionnaire: Textbook Discrepancies & AI Clinical Reliability

## 1. Overview & Objective

Medical knowledge is not homogeneous across academic literature. Different textbooks, editions, and clinical societies frequently present:
1. **Conflicting Diagnostic Thresholds**: Differing lab cutoff values (e.g., fasting glucose, TSH normal ranges, blood pressure categories).
2. **Contrarian Treatment Algorithms**: Differing first-line vs. second-line pharmaceutical options across medical subspecialties.
3. **Anatomical & Morphological Variants**: Discrepancies between theoretical anatomical norms and surgical variants.
4. **Historical vs. Modern Consensus**: Changes between textbook editions as new randomized controlled trials (RCTs) emerge.

This questionnaire is designed to gather qualitative and quantitative insights from practicing physicians, residents, and medical educators to establish gold-standard evaluation criteria for the **Text-Book-RAG** system.

---

## 2. Physician Profile & Demographics

* **Physician Name / Identifier**: ____________________________________________
* **Medical Specialty / Subspecialty**: ______________________________________
* **Years in Clinical Practice**: [ ] < 3 years (Resident/Fellow)  [ ] 3–10 years  [ ] > 10 years (Attending/Consultant)
* **Primary Practice Setting**: [ ] Academic Medical Center  [ ] Community Hospital  [ ] Private Practice
* **Primary Reference Textbooks Used**: _____________________________________

---

## 3. Core Questionnaire

### Section A: Contrarian Opinions & Cross-Textbook Discrepancies

1. **Specific Discrepancy Examples**:
   > *"Can you give 1–2 concrete examples in your specialty where two reputable textbooks or clinical guidelines present conflicting answers, differing cutoffs, or opposing management strategies?"*
   * *Example areas: Diagnostic criteria, staging systems, initial drug of choice, anatomical variants.*
   * **Notes**: ____________________________________________________________________

2. **Resolution Hierarchy**:
   > *"When you encounter a contradiction between two authoritative medical books (e.g., a general textbook like Harrison's / Anatomy 2e vs. a subspecialty text like Williams Endocrinology / Braunwald's Cardiology), what exact hierarchy or mental model do you use to decide which is more reliable?"*
   * [ ] **Subspecialty Over Generalist**: Dedicated subspecialty text takes priority over generalist review.
   * [ ] **Edition Recency**: Newer edition automatically supersedes older edition.
   * [ ] **Professional Society Consensus**: Society guideline consensus (e.g., ACC/AHA, ADA, ATA) supersedes textbook narrative.
   * [ ] **Pathophysiology vs. Clinical Outcome**: Prioritize empirical patient outcomes over theoretical mechanisms.
   * **Physician Elaboration**: ___________________________________________________

3. **Handling Clinical Ambiguity / Variants**:
   > *"In cases where there is no universal consensus (e.g., management of subclinical hypothyroidism in pregnancy or anatomic branching variations of the celiac trunk), how should an academic reference tool communicate this?"*
   * [ ] Give a single, definitive answer with a caveat.
   * [ ] Explicitly state both viewpoints, citing the respective sources and the controversy.
   * [ ] Provide the institutional consensus of the top medical center.
   * **Physician Comments**: _____________________________________________________

---

### Section B: AI Assistant Output Credibility & Evaluation Criteria

4. **Preferred Handling of Disagreements by AI**:
   > *"If an AI assistant finds conflicting passages in two indexed textbooks, which response format would you trust most?"*
   * **Option A (Synthetic Compromise)**: The AI averages or merges the two recommendations into a single harmonious recommendation without noting the source disagreement.
   * **Option B (Single Source Winner)**: The AI picks the newer or more specialized textbook and ignores the other.
   * **Option C (Contrarian Transparency)**: The AI explicitly reports the disagreement: *"Textbook A (p. 210) recommends X based on Y; however, Textbook B (p. 540) advises Z. Subspecialty practice generally prioritizes Textbook B because..."*
   * **Physician Selection & Rationale**: __________________________________________

5. **Rating Criteria for Clinical Correctness**:
   > *"How would you score the quality of an AI medical response on a scale of 1 to 5? Which factor matters most?"*
   * Rank the following in order of importance (1 = Most Critical, 5 = Least Critical):
     - [ ] **Exact Page Grounding**: Every sentence is backed by verified page/section citations.
     - [ ] **Nuance & Edge Case Recognition**: Acknowledges patient contraindications and clinical variants.
     - [ ] **Absence of Hallucinated Extrapolation**: Refuses to provide unsubstantiated advice outside the text.
     - [ ] **Medical Terminology Precision**: Uses proper clinical nomenclature instead of layperson simplifications.
     - [ ] **Conciseness & Structure**: Bulleted, high-yield layout for quick ward/clinic reference.

6. **The "Cost of Being Wrong" (Penalties)**:
   > *"What is the single most dangerous failure mode you see in medical AI tools today?"*
   * [ ] A confident but subtly incorrect statement (sycophantic hallucination).
   * [ ] Outdated clinical thresholds that have been replaced by newer guidelines.
   * [ ] Fabricated or misattributed citations that look real.
   * [ ] Omitting critical "red flag" clinical contraindications.
   * **Notes**: ____________________________________________________________________

---

### Section C: Visual Diagrams & Workflow Integration

7. **Visual Evidence Utility**:
   > *"When looking up a clinical problem, how important is seeing the original textbook diagram/pathway versus reading the text summary?"*
   * [ ] **Essential**: Must see anatomical plates, feedback loops, or diagnostic flowcharts to verify.
   * [ ] **High Value**: Helpful for spatial/pathway understanding, but text summary is primary.
   * [ ] **Low Value**: Text and diagnostic cutoffs are all that is needed on rounds.
   * **Notes**: ____________________________________________________________________

8. **Pre-Step Reasoning / Diagnostic Plan**:
   > *"Before diving into detailed book citations, would you value an AI assistant showing a concise 'Diagnostic Reasoning Plan' (e.g. key organ systems targeted, differential diagnosis buckets) before presenting the detailed literature extract?"*
   * [ ] Yes, it mirrors clinical problem-solving during rounds.
   * [ ] No, prefer jumping directly into the answer.
   * **Notes**: ____________________________________________________________________

---

## 4. Evaluation Rubric for Benchmarking Test Runs

When scoring AI outputs against expert physician answers, use this standardized 5-point rubric:

| Score | Grounding & Citation Fidelity | Discrepancy & Nuance Handling | Clinical Reliability |
| :---: | :--- | :--- | :--- |
| **5** | 100% of factual claims cite verified page numbers; verbatim source alignment. | Actively identifies textbook conflicts; accurately cites both views and resolves using subspecialty hierarchy. | Ready for clinical reference; no extrapolation; clear contraindication warnings. |
| **4** | Accurate citations for all major claims; minor stylistic paraphrasing. | Acknowledges that alternative clinical views exist; references primary source. | Safe for clinical review; minor non-critical omission of a rare variant. |
| **3** | Core claims cited, but some background statements ungrounded. | Mentions one textbook's perspective only, ignoring known alternative consensus. | Acceptable educational overview; requires clinician double-checking. |
| **2** | Misattributed citations or claims from wrong chapters. | Falsely merges conflicting guidelines into a fabricated compromise. | Unreliable; potential to mislead junior trainees. |
| **1** | Severe hallucination; fictitious citations; contradicts indexed textbook evidence. | Completely blind to major medical conflicts; invents non-existent guidelines. | Clinically dangerous; immediate failure. |

---

## 5. Interview Protocol & Next Steps

1. Conduct 30-minute structured interviews with at least 3–5 physicians across different specialties (e.g., Internal Medicine, Endocrinology, Surgery/Anatomy, Pediatrics).
2. Record verbatim examples of textbook discrepancies to build a **Golden Medical Discrepancy Test Set** in `tests/fixtures/discrepancies.json`.
3. Use the 5-point rubric above to grade the `AttendingPlanner` and `DifferentialSynthesizer` during Phase 3 verification.
