import sys, os
sys.path.insert(0, os.path.abspath("."))
sys.stdout.reconfigure(encoding='utf-8')
from google import genai
from google.genai import types
from agent.prompts import SYSTEM_PROMPT
from agent.orchestrator import TOOL_DEFINITIONS

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
tools = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name=t['function']['name'],
        description=t['function']['description'],
        parameters=t['function']['parameters']
    )
    for t in TOOL_DEFINITIONS
])]

for q in ['Are you using google llm?', 'Explain the zones of the adrenal cortex']:
    res = client.models.generate_content(
        model='gemini-flash-lite-latest',
        contents=[types.Content(role='user', parts=[types.Part.from_text(text=q)])],
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, tools=tools)
    )
    fc = [f.name for f in (res.function_calls or [])]
    print(f'Query: "{q}"')
    print(f'Function calls: {fc}')
    if not fc:
        print(f'Direct reply: {res.text.strip()[:200]}...')
    print()
