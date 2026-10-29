from dotenv import load_dotenv
load_dotenv()
import os, time
from google import genai
from google.genai import types

api_key = os.environ.get("GEMINI_API_KEY")

client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=1),
    )
)

models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]

for m in models:
    t0 = time.perf_counter()
    first_token = None
    try:
        stream = client.models.generate_content_stream(model=m, contents="Explain the adrenal cortex in 2 sentences.")
        for chunk in stream:
            if chunk.text:
                if first_token is None:
                    first_token = (time.perf_counter() - t0) * 1000
                print(chunk.text[:20], end="", flush=True)
        print()
        dt = (time.perf_counter() - t0) * 1000
        print(f"Model {m}: First token in {first_token:.1f}ms, Total {dt:.1f}ms")
    except Exception as e:
        dt = (time.perf_counter() - t0) * 1000
        print(f"\nModel {m} FAILED in {dt:.1f}ms: {e}")
