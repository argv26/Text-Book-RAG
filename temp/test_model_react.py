"""Test which Gemini models are available and support function calling."""
import sys, os
sys.path.insert(0, os.path.abspath("."))
sys.stdout.reconfigure(encoding="utf-8")

from google import genai
from google.genai import types
from agent.orchestrator import TOOL_DEFINITIONS

API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

MODELS_TO_TRY = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
]

tools = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name=t["function"]["name"],
        description=t["function"]["description"],
        parameters=t["function"]["parameters"],
    )
    for t in TOOL_DEFINITIONS
])]

system = "You are a medical tutor. Use search_chunks to look up textbook content when the user asks a medical question. For greetings or general chat, respond directly without calling tools."

for model in MODELS_TO_TRY:
    print(f"\n--- Testing {model} ---")
    for query in ["hi", "Explain the zones of the adrenal cortex", "are you using google llm?"]:
        try:
            res = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=query)])],
                config=types.GenerateContentConfig(system_instruction=system, tools=tools),
            )
            fc = [f.name for f in (res.function_calls or [])]
            if fc:
                print(f'  "{query}" -> TOOL CALLS: {fc}')
            else:
                print(f'  "{query}" -> DIRECT: {(res.text or "").strip()[:100]}...')
        except Exception as e:
            err = str(e)[:120]
            print(f'  "{query}" -> ERROR: {err}')
            break  # Don't waste quota on this model

