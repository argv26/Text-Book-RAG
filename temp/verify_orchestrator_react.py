"""Verify full ReAct turn with gemini candidate fallback."""
import sys, os
sys.path.insert(0, os.path.abspath("."))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from agent.orchestrator import AgentOrchestrator

orch = AgentOrchestrator()
print("Gemini API key loaded:", bool(orch.gemini_api_key))

queries = [
    ("hi", "Greeting - should NOT search chunks"),
    ("are you using google llm?", "Meta question - should answer directly from LLM, NO chunks"),
    ("Explain the zones of the adrenal cortex", "Medical question - SHOULD search chunks and cite"),
]

for q, desc in queries:
    print(f"\n==========================================")
    print(f"QUERY: {q} ({desc})")
    print(f"==========================================")
    res = orch.run_turn(f"test_react_{int(os.getpid())}", q)
    print("TOOL CALLS LOGGED:", len(res["tool_calls"]))
    for tc in res["tool_calls"]:
        print("  -> Tool called:", tc.get("name"), tc.get("summary", tc.get("args", "")))
    print("CITATIONS:", len(res["citations"]))
    print("REPLY PREVIEW:\n", res["reply"][:300].strip(), "...\n")

