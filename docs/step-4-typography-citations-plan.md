# Implementation Plan — Step 4: Medical Typography, Formatting & Citation Hardening

**Step Focus**: Eliminating Raw LaTeX Leaks, Enforcing Medical Typography Standards, Citation Index Hardening (Eliminating Cache Misses), and Evidence Drawer Redesign  
**Parent Document**: [`docs/phase-2-requirements.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/phase-2-requirements.md)  
**Status**: Completed & Verified  

---

## 1. Problem Statement & User Pain Points Addressed

During usability testing, clinician evaluators reported four critical formatting and evidence issues:
1. **Broken Medical & Lab Units** (*"point 3 wrong : says text - no ala odu"*, *"units are wring"*):
   - Assistant responses displayed unrendered raw LaTeX math syntax: `$\text{pH} < 7.3$`, `($< 15\text{ mmol/L}$)`.
   - The literal code token `\text` was directly exposed to users.
2. **Broken Hormone & Chemical Notation** (*"wow good but units all wrong"*):
   - Thyroid hormones and standard clinical abbreviations rendered with raw math markup: `($T_4$ and $T_3$)` instead of clean clinical symbols `T4 and T3` or `T₄ and T₃`.
3. **Citation Index Discontinuity & Broken Cache** (*"citations 1 nundi direct 14, 2-13 lev, 14 not working"*):
   - LLMs copied original end-of-chapter literature reference numbers (e.g., `[14]`, `[409–411]`) directly from textbook prose.
   - The user clicked `[14]` and received `Citation [14] details not found in cache.`, breaking user trust.
4. **Developer Jargon & Distorted Text in Evidence Drawer** (*"section id enti, page no correct eh na"*):
   - The drawer exposed internal database indices: `SECTION ID: #1698`, `PROSE • 587 TOKENS`.
   - The drawer excerpt displayed raw bibliography references and OCR column hyphenations (`compli-\n cation` instead of `complication`).

**Step 4 Goal**: Harden the system prompt, backend citation sanitization pipeline, and frontend typography rendering to guarantee clean, doctor-ready formatting, eliminate raw LaTeX tags, ensure 100% of citation pills link to valid cached evidence, and transform the Evidence Drawer into an authoritative clinical tool.

---

## 2. Architectural Design & Shared Decisions

1. **Dual-Layer Typography Defense (Prompt + Frontend/Backend Sanitizer)**:
   - **Layer 1 (Prompt Contract)**: Strictly prohibit LaTeX math mode (`$...$`, `$$...$$`, `\text{...}`) for medical units, laboratory ranges, and hormone abbreviations in `agent/prompts.py`.
   - **Layer 2 (Backend Sanitizer in Orchestrator)**: Add `_sanitize_medical_typography(reply: str) -> str` in `agent/orchestrator.py` before persisting to database and returning to client.
   - **Layer 3 (Frontend Sanitizer in `app.js`)**: Add `sanitizeMedicalText(text)` in `ui/static/app.js` before markdown parsing to guarantee zero raw math markup slips through.
2. **Citation Remapping & Cache Integrity**:
   - The system prompt forbids copying original textbook literature citations from the source text.
   - The backend `AgentOrchestrator` runs `_sanitize_citations(reply: str, valid_citation_ids: set[int]) -> str`. Any citation `[k]` exceeding retrieved citation IDs is either stripped or converted to plain text (e.g. `(Ref 14)`) so un-cached clickable pills are never emitted.
   - In `app.js`, `formatCitationBadges(html)` checks `citationsCache.has(p1)` before rendering an interactive pill. If missing from cache, it remains plain bracket text `[#]`, completely preventing dead-end clicks.
3. **Clinician-Centered Evidence Drawer**:
   - Remove internal database identifiers: Delete `SECTION ID: #1698` and `TOKENS` count from the drawer UI.
   - Present clean hierarchical breadcrumbs: *Textbook Volume > Chapter Title > Topic Path*.
   - Present Printed Page cleanly: `p. 1422` or `pp. 1422–1425`.
   - Apply text de-hyphenation (`(\w+)-\s*\n\s*(\w+)` $\rightarrow$ `$1$2`) to passage excerpts to fix OCR column-split words (`compli-\n cation` $\rightarrow$ `complication`).

---

## 3. Detailed Sub-Step Breakdown & Verification Protocol

### Sub-Step 4.1: System Prompt Standards & Citation Contract (`agent/prompts.py` & `agent/orchestrator.py`)

- **Description & Why**:
  Upgrade the clinical agent prompt to explicitly mandate standard medical typography and enforce the citation indexing contract. Also update the offline fallback synthesizer in `agent/orchestrator.py` to adhere to the same standards.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Update agent/prompts.py with explicit Medical Typography and Citation Indexing rules.
  FILES: agent/prompts.py, agent/orchestrator.py
  INSTRUCTIONS:
  1. In agent/prompts.py, append strict rules to SYSTEM_PROMPT:
     - Forbid LaTeX math mode ($...$, $$...$$, \text{...}, \ge, \sim) for clinical units, lab values, or hormones.
     - Mandate plain text and standard Unicode: 'pH < 7.3', '15 mmol/L', '200–250 mg/dL', '~180 mg/dL', 'β-hydroxybutyrate ≥ 3 mmol/L'.
     - Hormone abbreviations in clean plain text: 'T4 and T3', 'fT4', 'TSH', 'TRH', 'ACTH', 'FSH', 'LH' (never '$T_4$' or '($T_4$ and $T_3$)').
     - Citation Contract: Only cite 1-based indices [1]..[N] corresponding to retrieved chunks. Never copy textbook bibliography numbers (like [14] or [409-411]).
  2. In agent/orchestrator.py, update the offline synthesizer responses to format units and hormones cleanly without LaTeX.
  ```
- **Relevant Files & Components**:
  - `agent/prompts.py`: `SYSTEM_PROMPT`
  - `agent/orchestrator.py`: `_run_offline_synthesizer_turn`
- **Acceptance Criteria**:
  - Model prompt includes clear, explicit rules prohibiting `\text{...}`, `$T_4$`, and out-of-range citations.
  - Offline synthesizer templates use clean plain text/Unicode (e.g. `T4 and T3`, `pH < 7.3`).
- **Automated Verification**:
  - Run python syntax check: `python -m py_compile agent/prompts.py agent/orchestrator.py`.
- **Human Verification Steps**:
  1. Open `agent/prompts.py`.
  2. Verify that section `### Medical Typography & Unit Rules` and `### Citation Integrity Contract` are present and unambiguous.
  3. Verify that negative examples (e.g. `\text{pH}`, `$T_4$`, `[14]`) are clearly marked as forbidden.

---

### Sub-Step 4.2: Backend Citation Normalizer & LaTeX Sanitizer Pipeline (`agent/orchestrator.py`)

- **Description & Why**:
  Even with strict prompting, LLMs can occasionally leak a bracketed number from source text or use LaTeX formatting. The backend orchestrator must act as a guaranteed safety barrier that validates every citation and sanitizes typography before persisting to database and returning to the client.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Implement backend typography and citation sanitization in agent/orchestrator.py.
  FILES: agent/orchestrator.py, temp/test_citation_normalizer.py
  INSTRUCTIONS:
  1. In agent/orchestrator.py, implement helper functions:
     - `_sanitize_medical_typography(text: str) -> str`:
       * Replace `\text{([^}]+)}` -> `\1`
       * Replace `\$\s*T_?([34])\s*\$` -> `T\1`
       * Replace `\$\s*([^$]+)\s*\$` with `\1` for basic symbols/units
       * Convert LaTeX math symbols: `\ge` / `\geq` -> `≥`, `\le` / `\leq` -> `≤`, `\sim` -> `~`, `\pm` -> `±`, `\mu` -> `μ`, `\alpha` -> `α`, `\beta` -> `β`
     - `_sanitize_citations(reply: str, valid_citation_ids: set[int]) -> str`:
       * Inspect every `\[(\d+)\]`. If int(num) not in valid_citation_ids, strip the bracket pill syntax or convert to plain text `(Ref \1)` so no broken clickable pill is created.
     - `_clean_chunk_prose(text: str) -> str` enhancement:
       * De-hyphenate column-split words: regex `(\w+)-\s*\n\s*(\w+)` -> `\1\2`.
  2. Integrate these sanitizers into `_run_gemini_turn`, `_run_openai_turn`, and `_run_offline_synthesizer_turn` before calling `_parse_follow_ups` and before saving assistant message.
  3. Create automated test in `temp/test_citation_normalizer.py` verifying all edge cases.
  ```
- **Relevant Files & Components**:
  - `agent/orchestrator.py`: `_sanitize_medical_typography`, `_sanitize_citations`, `_clean_chunk_prose`
  - `temp/test_citation_normalizer.py`: Unit test verifying sanitization logic
- **Acceptance Criteria**:
  - `\text{pH} < 7.3` is transformed to `pH < 7.3`.
  - `($T_4$ and $T_3$)` is transformed to `(T4 and T3)`.
  - An out-of-range citation `[14]` when only citations 1, 2, 3 exist is converted to plain text `(Ref 14)` or removed from clickable pill brackets.
  - Chunk de-hyphenation correctly joins `compli-\n cation` -> `complication`.
  - Test script `temp/test_citation_normalizer.py` passes 100%.
- **Automated Verification**:
  - Execute: `python temp/test_citation_normalizer.py`
  - Expected output: `ALL CITATION & TYPOGRAPHY TESTS PASSED`.
- **Human Verification Steps**:
  1. Inspect test script output log.
  2. Check that edge cases (consecutive brackets `[1][2]`, out-of-bound `[14]`, negative numbers, sub-indices) handle gracefully without exceptions.

---

### Sub-Step 4.3: Frontend Typography Sanitizer, Cache Protection & Evidence Drawer Redesign (`ui/static/app.js`, `ui/static/index.html`)

- **Description & Why**:
  Upgrade the frontend to sanitize any residual LaTeX markup before passing to Markdown, format only valid citation pills that exist in the local cache, remove developer jargon (`SECTION ID: #1698`, `587 TOKENS`) from the Evidence Drawer, and add automatic text de-hyphenation to passage excerpts.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Enhance ui/static/app.js and ui/static/index.html for clinical typography and evidence drawer redesign.
  FILES: ui/static/app.js, ui/static/index.html
  INSTRUCTIONS:
  1. In ui/static/app.js:
     - Implement `sanitizeMedicalText(text)`:
       * Strip `\text{...}` -> `...`
       * Replace `$T_4$` -> `T4`, `$T_3$` -> `T3`
       * Normalize `\ge` -> `≥`, `\le` -> `≤`, `\sim` -> `~`, `\pm` -> `±`, `\mu` -> `μ`, `\alpha` -> `α`, `\beta` -> `β`
       * Strip enclosing single dollar signs around alphanumeric units: `$\text{mmol/L}$` or `$mmol/L$` -> `mmol/L`
     - Call `sanitizeMedicalText` on `data.reply` before passing to `marked.parse()`.
     - In `formatCitationBadges(html)`:
       * Check `citationsCache.has(p1)`. If present, render clickable `<button class="citation-badge">`. If NOT present, return plain text `[${p1}]`. This prevents 100% of dead-end clicks and cache miss errors.
     - In `showCitationInDrawer(citId)`:
       * Remove `SECTION ID: #${cit.section_id}` block.
       * In its place, show a clean "Source Classification" badge (e.g. `Clinical Endocrinology` or `Verified Textbook Reference`).
       * When loading chunk text in `fetch(/api/chunk/...)`:
         - Apply de-hyphenation: `text.replace(/(\w+)-\s*\n\s*(\w+)/g, "$1$2")`.
         - Remove `• ${chunkData.token_count} TOKENS` from the type label. Replace with clean block badge: `VERIFIED PROSE` or `CLINICAL TABLE`.
  2. In ui/static/index.html:
     - Ensure drawer header and structure align with the clinical redesign.
  3. Verify with end-to-end browser / API test script `temp/test_ui_typography.py`.
  ```
- **Relevant Files & Components**:
  - `ui/static/app.js`: `sanitizeMedicalText`, `formatCitationBadges`, `showCitationInDrawer`
  - `ui/static/index.html`: Evidence drawer markup
  - `temp/test_ui_typography.py`: E2E test verifying API and rendered markup
- **Acceptance Criteria**:
  - Zero raw `\text` or unrendered LaTeX tags appear in the UI.
  - Out-of-cache citation numbers do not become clickable buttons; only cached citations become interactive pills.
  - Clicking any valid citation badge opens the Evidence Drawer showing:
    * Textbook Volume
    * Section Path
    * Printed Page
    * Clean de-hyphenated passage text without `TOKENS` or `SECTION ID` developer jargon.
- **Automated Verification**:
  - Run `python temp/test_ui_typography.py` to test the API response and verify HTML sanitization patterns.
- **Human Verification Steps**:
  1. Start the server: `python -m ui.app` (or test via local browser).
  2. Submit query: *"What are the biochemical diagnostic criteria for DKA and what thyroid hormone changes occur in nonthyroidal illness?"*
  3. Observe the response:
     - Verify `pH < 7.3`, `15 mmol/L`, `200–250 mg/dL` have NO `\text` keywords.
     - Verify `T4` and `T3` have NO dollar signs.
  4. Inspect citation pills:
     - Verify pills start from `[1]` and are consecutive.
     - Click on each citation pill:
       * The Evidence Drawer opens from the right.
       * The Drawer shows: Textbook Volume, Section Path, Printed Page (e.g., `p. 1422`), and Passage Excerpt.
       * Verify `SECTION ID: #1698` is NOT visible.
       * Verify `TOKENS` is NOT visible.
       * Verify no words are broken across lines like `compli-\n cation`.
       * Verify NO "Citation details not found in cache" error occurs.

---

## 4. Execution & Verification Results

### Automated Verification Results
1. **Citation & Typography Test Suite (`temp/test_citation_normalizer.py`)**:
   - `[PASS] Test 1: LaTeX \text{} tags stripped cleanly.`
   - `[PASS] Test 2: Thyroid hormone notation sanitized ($T_4$ -> T4, $fT_4$ -> fT4).`
   - `[PASS] Test 3: LaTeX math symbols converted to Unicode (\ge -> ≥, \sim -> ~, \beta -> β).`
   - `[PASS] Test 4: Out-of-bounds citations sanitized to '(Ref 14)'.`
   - `[PASS] Test 5: OCR hyphenated column-splits merged correctly ('compli-\n cation' -> 'complication').`
2. **End-to-End Chat & Evidence Test Suite (`temp/test_ui_typography.py`)**:
   - `[PASS] Chat turn succeeded with clean formatted medical text.`
   - `[PASS] Verified: Zero raw LaTeX tags (\text, $T, \ge, \sim) in chat output.`
   - `[PASS] Verified: 100% of citation pills map to cached citation records.`
   - `[PASS] Verified: Chunk fetched and de-hyphenated cleanly.`
3. **Regression Test Suite (`temp/test_auth_api.py`, `temp/test_sessions_api.py`)**:
   - `[PASS] 10/10 Auth API tests passed.`
   - `[PASS] 9/9 Session API tests passed.`

### Human Verification Checklist
- [x] Assistant response displays clean Unicode units (e.g. `pH < 7.3`, `< 15 mmol/L`, `200–250 mg/dL`, `T4 and T3`).
- [x] No literal `\text` code tokens or `$T` math delimiters are rendered.
- [x] Citation pills index sequentially and only valid cached chunks are rendered as clickable buttons.
- [x] Out-of-bounds citations from textbook bibliography are rendered as plain text `(Ref 14)`.
- [x] Evidence Drawer displays clean clinical hierarchy (Textbook Volume, Chapter/Topic Breadcrumb, Printed Page).
- [x] Developer jargon (`SECTION ID: #1698`, `587 TOKENS`) is completely removed from the Drawer.
- [x] Passage excerpts in the Drawer have column line wraps and hyphenations seamlessly merged.

