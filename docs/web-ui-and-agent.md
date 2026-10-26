# Web Chat UI & Agent Orchestrator Specification

## 1. Overview
The Web Chat UI and Agent layer provide a complete, interactive, medical-grade conversational interface for the Structure-Aware Textbook RAG system. It is designed to maximize user engagement, trust, and multi-turn clinical exploration.

## 2. Core Architecture

```
[ Web Browser ]
      ▲
      │ HTTP / JSON
      ▼
[ FastAPI App (ui/app.py) ]
      ▲
      │ Orchestrates Turn
      ▼
[ AgentOrchestrator (agent/orchestrator.py) ]
      ├── [ System Prompt (agent/prompts.py) ]
      ├── [ ContextManager (context/manager.py) ]
      │         ├── Session State & Pruning
      │         ├── Tool Output Virtualization
      │         └── Citation Registry
      └── [ Retrieval Tools (retrieval/tools.py) ]
                ├── search_chunks (prose, tables, boxes)
                ├── search_sections (outline hierarchy)
                ├── read_section (assembled markdown)
                ├── get_toc (chapter tree)
                └── get_chunk (full chunk inspection)
```

## 3. Key Features

### 3.1. Interactive Evidence Drawer
- Every factual claim contains clickable citation badges like `[1]`, `[2]`.
- Clicking any badge slides out the **Evidence Drawer** from the right.
- Displays:
  - Source textbook volume name.
  - Exact section heading path (e.g. `Chapter 17 > 17.4 The Thyroid Gland`).
  - Printed page number (matching the physical textbook pagination).
  - Chunk ID and content type badge (`prose`, `table`, `box`).
  - Full verbatim textbook excerpt.

### 3.2. Real-Time Tool Execution Badges
- Collapsible tool pills inform the user what the agent searched under the hood (e.g. `⚡ Searched chunks for "thyroid hormone" (4 hits)`).
- Provides transparency and clinical confidence.

### 3.3. Dynamic Follow-Up Suggestions
- At the bottom of each assistant answer, interactive suggestion chips invite the user to explore deeper clinical correlates, differential diagnoses, or related anatomical structures.
- Clicking any chip sends it as the next query instantly.

### 3.4. Dual-Mode Execution
- **LLM Mode:** When `GEMINI_API_KEY` or `OPENAI_API_KEY` is configured in the environment, executes full multi-turn LLM tool-calling loops.
- **Offline Smart Synthesizer Mode:** When running locally without API keys, automatically falls back to deterministic multi-hop retrieval and structured clinical synthesis with exact citations and follow-up prompts. Zero setup required.

## 4. API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves single-page application (`index.html`) |
| `GET` | `/api/books` | Lists indexed textbooks and page statistics |
| `POST` | `/api/chat` | Executes multi-turn conversation turn |
| `GET` | `/api/chunk/{chunk_id}` | Retrieves raw chunk excerpt and metadata |
| `GET` | `/api/session/{session_id}/citations` | Retrieves all citations accumulated across turns |

## 5. How to Run

1. Activate virtual environment:
   ```bash
   .venv\Scripts\activate
   ```

2. Start the web server:
   ```bash
   python -m ui.app
   # or
   uvicorn ui.app:app --reload --port 8000
   ```

3. Open your browser:
   Visit `http://127.0.0.1:8000`

