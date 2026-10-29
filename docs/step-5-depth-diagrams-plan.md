# Implementation Plan — Step 5: Answer Depth, Diagrams & Clinical Flowcharts

**Step Focus**: Authoritative Textbook-Grade Depth, Native Mermaid.js Clinical Algorithms, and Related Figure/Diagram Gallery  
**Parent Document**: [`docs/phase-2-requirements.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/phase-2-requirements.md)  
**Status**: Completed & Verified  

---

## 1. Problem Statement & User Pain Points Addressed

During clinician usability testing, evaluators raised three critical clinical learning limitations:
1. **Answers are too short / shallow** (*"and short answers istundi, detailed ivali konchem, ah concept varku cover cheyali depth boundaries una"*):
   - Clinicians and medical students preparing for board exams or rounds cannot rely on brief 200-word overviews.
   - They require comprehensive coverage across core clinical domains:
     - Definition & Clinical Scope
     - Pathophysiology & Molecular/Endocrine Mechanism
     - Clinical Presentation & Cardinal Signs (stratified by demographics)
     - Diagnostic Workup & Lab Cutoffs (exact units: `pH < 7.3`, `mg/dL`, `mmol/L`)
     - Management Protocols & Emergency Interventions
     - Complications & High-Yield Clinical Pearls
2. **Lack of Visual Flowcharts & Algorithms** (*"also images flow charts ivali"*):
   - Medical decision-making is inherently algorithmic (e.g. fluid resuscitation in DKA, evaluation of hypokalemia, pubertal delay workup). Text-only descriptions are difficult to parse in acute scenarios.
3. **Absence of Textbook Figures & Diagrams** (*"nice but no diagram (fetch 4 to 5 related diagrams (if necessary))"*):
   - Medical textbooks rely heavily on anatomical illustrations, pathway schematics, and clinical graphs. The database already contains **1,596 verified figure captions**, but they are currently ignored and never surfaced to the clinician.

**Step 5 Goal**: Expand agent synthesis to deliver textbook-grade clinical depth, render dynamic interactive clinical flowcharts natively via Mermaid.js, and surface 3–5 relevant textbook figure/diagram cards alongside answers.

---

## 2. Architectural Design & Shared Decisions

1. **Structured Comprehensive Clinical Synthesis**:
   - Upgrade the prompt contract to mandate structured textbook-grade depth: Definition, Pathophysiology, Presentation, Diagnostic Criteria, Management Protocols, and Complications.
   - Increase retrieval limit in `agent/orchestrator.py` from 4 chunks to 6–8 chunks to supply sufficient factual density without context overflow.
2. **Native Mermaid.js Clinical Flowchart Engine**:
   - Integrate Mermaid.js (`mermaid@10`) into `ui/static/index.html`.
   - Prompt the LLM to generate ````mermaid flowchart TD```` code blocks whenever explaining a clinical protocol, diagnostic algorithm, or physiological feedback loop.
   - Frontend `ui/static/app.js` detects `pre code.language-mermaid` blocks, initializes Mermaid with neutral clinical styling, and converts them into responsive SVG diagrams. If Mermaid is unavailable, it gracefully displays as styled Markdown code.
3. **Textbook Figure & Diagram Extraction Pipeline**:
   - When a medical topic is queried, `AgentOrchestrator` runs a secondary retrieval via `search_chunks(query, content_type="figure_caption", limit=4)` to retrieve relevant textbook figure captions.
   - Pass these figures in the API response under `figures: list[dict]`.
   - Frontend `ui/static/app.js` displays a clean, collapsible **"Related Textbook Figures & Diagrams"** card tray below the response, showing Figure Number, Title, Source Textbook, Printed Page, and Caption snippet.
   - Clicking a figure card opens the Evidence Drawer to inspect the full figure context and surrounding text.

---

## 3. Detailed Sub-Step Breakdown & Verification Protocol

### Sub-Step 5.1: Multi-Chunk Context & Deep Clinical Prompting (`agent/prompts.py`, `agent/orchestrator.py`)

- **Description & Why**:
  Expand chunk retrieval limits and instruct the model to produce structured, exhaustive clinical explanations bounded by textbook evidence.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Upgrade clinical depth in agent/prompts.py and agent/orchestrator.py.
  FILES: agent/prompts.py, agent/orchestrator.py
  INSTRUCTIONS:
  1. In agent/prompts.py:
     - Add Clinical Depth & Structure Guidelines:
       * Exhaustively cover the queried clinical entity within the boundaries of the retrieved chunks.
       * Organize comprehensive answers using clear Markdown sections:
         1. Clinical Definition & Scope
         2. Pathophysiology & Biochemical Mechanisms
         3. Clinical Presentation & Cardinal Signs
         4. Diagnostic Criteria & Lab Thresholds
         5. Acute Management & Protocol Guidelines
         6. Complications & High-Yield Pearls
       * Explicitly forbid brief, superficial 1-paragraph summaries for disease or syndrome queries.
  2. In agent/orchestrator.py:
     - Increase default chunk retrieval limit from 4 to 6-8 chunks in `search_chunks` calls within `_run_offline_synthesizer_turn` and TOOL_DEFINITIONS parameter suggestions.
  3. Verify with temp/test_depth_synthesis.py.
  ```
- **Relevant Files & Components**:
  - `agent/prompts.py`: `SYSTEM_PROMPT`
  - `agent/orchestrator.py`: `_run_offline_synthesizer_turn`, `TOOL_DEFINITIONS`
  - `temp/test_depth_synthesis.py`: Verification script
- **Acceptance Criteria**:
  - Medical queries (e.g. DKA, Van Wyk-Grumbach, Thyroid Storm) yield detailed multi-section responses (>400 words) covering pathophysiology, diagnosis, and treatment.
  - Citations are distributed across multiple sections.
- **Automated Verification**:
  - Run: `.\.venv\Scripts\python.exe temp/test_depth_synthesis.py`
  - Asserts response contains >350 words, has structured sections (Pathophysiology, Diagnostic, Management), and contains >=3 citations.
- **Human Verification Steps**:
  1. Ask: *"Explain diabetic ketoacidosis (DKA) pathophysiology, presentation, and diagnostic criteria."*
  2. Verify response includes detailed sections with exact lab cutoffs (`pH < 7.3`, `bicarbonate < 15 mmol/L`, `glucose > 200–250 mg/dL`, `β-hydroxybutyrate ≥ 3 mmol/L`).
  3. Confirm the explanation reads as an authoritative textbook lecture, not a superficial bulleted snippet.

---

### Sub-Step 5.2: Dynamic Mermaid.js Clinical Flowchart Engine (`ui/static/index.html`, `ui/static/app.js`, `agent/prompts.py`)

- **Description & Why**:
  Render interactive clinical decision trees, diagnostic algorithms, and hormone feedback loops directly in the chat dialogue via Mermaid.js.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Integrate Mermaid.js for clinical flowcharts in ui/static/index.html, ui/static/app.js, and agent/prompts.py.
  FILES: ui/static/index.html, ui/static/app.js, agent/prompts.py
  INSTRUCTIONS:
  1. In ui/static/index.html:
     - Add Mermaid library script: `<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>`.
  2. In ui/static/app.js:
     - Initialize Mermaid on page load: `mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' })`.
     - In `appendAssistantMessage(data)`:
       * Parse markdown, then find all `<pre><code class="language-mermaid">...</code></pre>` blocks.
       * Replace each code block with a styled container `<div class="mermaid-diagram bg-slate-50 border border-slate-200 rounded-xl p-4 my-3 overflow-x-auto">`.
       * Call `mermaid.run({ nodes: container.querySelectorAll('.mermaid') })` to render SVG flowcharts.
  3. In agent/prompts.py:
     - Instruct the model: *"When explaining a clinical protocol, diagnostic algorithm, or physiological feedback loop, include a clean Mermaid flowchart (```mermaid flowchart TD ... ```) summarizing the decision pathway."*
  4. Verify with temp/test_mermaid_rendering.py and browser inspection.
  ```
- **Relevant Files & Components**:
  - `ui/static/index.html`: CDN script tag
  - `ui/static/app.js`: Mermaid initialization & post-render hook
  - `agent/prompts.py`: Mermaid diagram generation instruction
- **Acceptance Criteria**:
  - Generated Mermaid flowcharts render as clean, interactive SVG diagrams with clear arrows and boxes.
  - Diagram containers support horizontal scrolling on mobile devices without layout breaking.
- **Automated Verification**:
  - Automated check in `temp/test_mermaid_rendering.py` confirming `index.html` loads Mermaid, `app.js` handles `.language-mermaid`, and sample Mermaid markup parses cleanly.
- **Human Verification Steps**:
  1. Query: *"What is the emergency management protocol for DKA?"*
  2. Observe the generated flowchart:
     - Confirm an interactive diagram renders with clear nodes (e.g. `IV Fluids` $\rightarrow$ `Check Potassium` $\rightarrow$ `Insulin Infusion`).
     - Test responsive rendering: resize browser window to mobile width, verify horizontal scroll works smoothly without overflowing the chat container.

---

### Sub-Step 5.3: Textbook Figure & Diagram Extraction Pipeline (`retrieval/tools.py`, `agent/orchestrator.py`, `ui/app.py`, `ui/static/app.js`)

- **Description & Why**:
  Surface 3 to 5 matching textbook figures and diagram plates from the 1,596 indexed `figure_caption` records in `textbook_rag.db`, making the system visual and authentic.
- **Self-Contained Task Prompt**:
  ```text
  TASK: Implement textbook figure retrieval and UI card gallery.
  FILES: retrieval/tools.py, agent/orchestrator.py, ui/app.py, ui/static/app.js
  INSTRUCTIONS:
  1. In retrieval/tools.py:
     - Expose `search_figures(query: str, book_slug: str | None = None, allowed_books: list[str] | None = None, limit: int = 4) -> list[dict]`:
       * Wraps `search_chunks(query=query, content_type="figure_caption", book_slug=book_slug, allowed_books=allowed_books, limit=limit)`.
  2. In agent/orchestrator.py:
     - During `run_turn()`, after retrieval, search for 3-4 matching figure captions using the core medical keywords.
     - Include `figures: list[dict]` in the return dictionary of `run_turn()`.
  3. In ui/app.py:
     - Add `figures: list[dict[str, Any]] = []` to `ChatResponse`.
     - Save figures in consultation message metadata if present.
  4. In ui/static/app.js:
     - In `appendAssistantMessage(data)`:
       * If `data.figures && data.figures.length > 0`, render an expandable tray:
         `"Related Textbook Figures & Diagrams (N)"` with an image/diagram icon.
       * Each figure card displays:
         - Figure Identifier (e.g. `FIGURE 20.12`)
         - Clean title / topic path
         - Textbook volume & Printed page (e.g. `p. 864`)
         - Caption excerpt (de-hyphenated)
       * Clicking a figure card opens the Evidence Drawer displaying the complete figure caption and source details.
  5. Verify with temp/test_figure_pipeline.py.
  ```
- **Relevant Files & Components**:
  - `retrieval/tools.py`: `search_figures`
  - `agent/orchestrator.py`: figure retrieval hook
  - `ui/app.py`: `ChatResponse` model update
  - `ui/static/app.js`: Figure cards tray & drawer link
  - `temp/test_figure_pipeline.py`: Pipeline test
- **Acceptance Criteria**:
  - Queries for anatomical, physiological, or endocrine topics return 3–5 relevant figure caption records.
  - Figure cards render neatly beneath the assistant answer with textbook volume and page reference.
  - Clicking a figure card opens the Evidence Drawer with full caption and page details.
- **Automated Verification**:
  - Run `.\.venv\Scripts\python.exe temp/test_figure_pipeline.py`.
  - Asserts `/api/chat` returns `figures` array containing `chunk_id`, `book_slug`, `page_start`, and `text`.
- **Human Verification Steps**:
  1. Query: *"How does blood pressure regulation and femoral circulation work?"*
  2. Observe beneath the answer:
     - A tray appears: *"Related Textbook Figures & Diagrams (3)"*.
     - Verify cards display: e.g. `FIGURE 20.12: Blood Pressure Measurement`, `p. 864`, volume title.
     - Click the card: The Evidence Drawer opens showing the verified textbook passage excerpt for that figure.

---

## 4. Execution & Verification Results

### Automated Verification Results
1. **Depth, Diagrams & Figures Test Suite (`temp/test_step5_depth_diagrams.py`)**:
   - `[PASS] search_figures returned 4 matching textbook figures with chunk_id, printed page, and captions.`
   - `[PASS] /api/chat returned status 200 with 867 words and 7 citations (comprehensive textbook-grade depth).`
   - `[PASS] Textbook Figures returned: 4 figures in payload.`
   - `[PASS] Verified: Mermaid flowchart block included in synthesized response.`
   - `[PASS] Verified: mermaid.min.js CDN included in ui/static/index.html.`
   - `[PASS] Verified: renderMermaidDiagrams and showFigureInDrawer present in ui/static/app.js.`
2. **Typography & Citation Regression Suite (`temp/test_ui_typography.py`)**:
   - `[PASS] 100% of citation pills map to cached records.`
   - `[PASS] Zero raw LaTeX math tags (\text, $T, \ge, \sim) in response.`
   - `[PASS] Chunk fetching and text de-hyphenation verified.`
3. **Session & Auth Regression Suites (`temp/test_sessions_api.py`, `temp/test_auth_api.py`)**:
   - `[PASS] All session and authentication endpoints passing 100%.`

### Human Verification Checklist
- [x] Assistant delivers textbook-grade clinical depth (>400 words) with structured sections: *Definition, Pathophysiology, Presentation, Lab Criteria, Protocols, Complications*.
- [x] Clinical algorithms and physiological loops render as dynamic, interactive Mermaid.js SVG flowcharts.
- [x] Mermaid diagrams are responsive and horizontally scrollable on mobile without layout overflow.
- [x] A dedicated tray *"Related Textbook Figures & Diagrams (N)"* appears below medical responses with 3–4 figure cards.
- [x] Each figure card displays Figure identifier (e.g. `FIGURE 20.12`), clean caption, textbook volume, and printed page (`p. 864`).
- [x] Clicking any figure card opens the Evidence Drawer with full figure details and textbook context.

