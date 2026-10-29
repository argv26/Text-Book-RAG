"""Automated test and TTFT benchmark for the SSE Streaming endpoint /api/chat/stream."""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from ui.app import app

async def test_sse_stream():
    print("=" * 60)
    print("SUB-STEP 6.2: TESTING /api/chat/stream SSE PIPELINE & TTFT")
    print("=" * 60)

    test_queries = [
        "What are the zones of the adrenal cortex?",
        "Explain the boundaries and contents of the femoral triangle.",
        "hello",
    ]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        # First ensure guest login cookie
        me_res = await client.get("/api/auth/me")
        print(f"Auth check status: {me_res.status_code}")

        for query in test_queries:
            session_id = f"test_stream_{int(time.time() * 1000)}"
            print(f"\nTesting Query: '{query}'")

            t0 = time.perf_counter()
            first_token_time = None
            events_received = []
            tokens_accumulated = []

            async with client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "session_id": session_id,
                    "message": query,
                },
                timeout=30.0,
            ) as response:
                assert response.status_code == 200, f"Expected 200, got {response.status_code}"
                assert "text/event-stream" in response.headers.get("content-type", "")

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_block, buffer = buffer.split("\n\n", 1)
                        lines = [line.strip() for line in event_block.split("\n") if line.strip()]
                        evt_name = "message"
                        evt_data = {}

                        for line in lines:
                            if line.startswith("event:"):
                                evt_name = line.split(":", 1)[1].strip()
                            elif line.startswith("data:"):
                                data_str = line.split(":", 1)[1].strip()
                                try:
                                    evt_data = json.loads(data_str)
                                except Exception:
                                    evt_data = data_str

                        events_received.append(evt_name)

                        if evt_name == "token":
                            if first_token_time is None:
                                first_token_time = (time.perf_counter() - t0) * 1000
                            delta = evt_data.get("delta", "") if isinstance(evt_data, dict) else ""
                            tokens_accumulated.append(delta)

            total_time = (time.perf_counter() - t0) * 1000

            print(f"  Events received: {list(dict.fromkeys(events_received))}")
            print(f"  TTFT (Time-to-First-Token): {first_token_time:6.1f} ms" if first_token_time else "  TTFT: N/A")
            print(f"  Total Duration:            {total_time:6.1f} ms")
            print(f"  Tokens received:           {len(tokens_accumulated)} deltas ({len(''.join(tokens_accumulated))} chars)")

            # Assertions
            assert "token" in events_received, "Stream failed to emit any token events"
            assert "done" in events_received, "Stream failed to emit done event"
            assert first_token_time is not None, "First token time not recorded"
            assert first_token_time < 2000.0, f"TTFT {first_token_time:.1f}ms exceeded 2000ms threshold!"

            # Verify database persistence
            history_res = await client.get(f"/api/sessions/{session_id}")
            assert history_res.status_code == 200
            msgs = history_res.json().get("messages", [])
            assert len(msgs) >= 2, f"Expected user and assistant messages in DB, got {len(msgs)}"
            assert msgs[0]["role"] == "user"
            assert msgs[1]["role"] == "assistant"
            print(f"  Database verified: Saved conversation turn with {len(msgs[1].get('citations', []))} citations.")

    print("\n" + "=" * 60)
    print("ALL SUB-STEP 6.2 ACCEPTANCE CRITERIA PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_sse_stream())
