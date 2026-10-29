"""Benchmark and concurrency verification for parallel multi-tool retrieval."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.orchestrator import _execute_parallel_retrieval
from retrieval.tools import search_chunks, search_sections, search_figures

SAMPLE_QUERIES = [
    "adrenal cortex steroid synthesis",
    "femoral triangle boundaries",
    "brachial plexus organization",
    "thyroid hormone regulation",
    "pituitary adenoma visual field",
    "aldosterone mechanism distal tubule",
    "parathyroid hormone calcium homeostasis",
    "carotid sheath neurovascular bundle",
]

def run_sequential(query: str):
    t0 = time.perf_counter()
    chunks = search_chunks(query=query, limit=6)
    sections = search_sections(query=query, limit=2)
    figures = search_figures(query=query, limit=4)
    dt = (time.perf_counter() - t0) * 1000
    return dt, len(chunks), len(sections), len(figures)

def run_parallel(query: str):
    t0 = time.perf_counter()
    res = _execute_parallel_retrieval(query=query, chunk_limit=6, section_limit=2, figure_limit=4)
    dt = (time.perf_counter() - t0) * 1000
    return dt, len(res["chunks"]), len(res["sections"]), len(res["figures"])

def test_benchmark():
    print("=" * 60)
    print("SUB-STEP 6.1: BENCHMARKING SEQUENTIAL VS PARALLEL RETRIEVAL")
    print("=" * 60)

    seq_times = []
    par_times = []

    for q in SAMPLE_QUERIES:
        dt_seq, c1, s1, f1 = run_sequential(q)
        dt_par, c2, s2, f2 = run_parallel(q)

        seq_times.append(dt_seq)
        par_times.append(dt_par)

        print(f"Query: '{q[:32]}...'")
        print(f"  Sequential: {dt_seq:6.1f} ms (chunks={c1}, sections={s1}, figures={f1})")
        print(f"  Parallel:   {dt_par:6.1f} ms (chunks={c2}, sections={s2}, figures={f2})")
        assert c1 == c2, f"Chunk count mismatch: {c1} != {c2}"
        assert s1 == s2, f"Section count mismatch: {s1} != {s2}"
        assert f1 == f2, f"Figure count mismatch: {f1} != {f2}"

    avg_seq = sum(seq_times) / len(seq_times)
    avg_par = sum(par_times) / len(par_times)
    speedup = ((avg_seq - avg_par) / avg_seq) * 100

    print("-" * 60)
    print(f"Average Sequential: {avg_seq:6.1f} ms")
    print(f"Average Parallel:   {avg_par:6.1f} ms  ({speedup:.1f}% faster / {avg_seq/avg_par:.2f}x speedup)")
    print("=" * 60)

    # Acceptance criteria: average parallel retrieval under 200ms
    assert avg_par < 200.0, f"Average parallel retrieval exceeded 200ms: {avg_par} ms"
    print("PASS: Parallel retrieval speed threshold (< 200ms) satisfied.")

def test_concurrency_stress():
    print("\n" + "=" * 60)
    print("TESTING THREAD-SAFETY UNDER 50 RAPID REPEATED QUERIES")
    print("=" * 60)
    errors = 0
    t0 = time.perf_counter()

    for i in range(50):
        q = SAMPLE_QUERIES[i % len(SAMPLE_QUERIES)]
        try:
            res = _execute_parallel_retrieval(query=q)
            assert len(res["chunks"]) > 0 or len(res["sections"]) > 0
        except Exception as e:
            print(f"Error on iteration {i}: {e}")
            errors += 1

    total_time = (time.perf_counter() - t0) * 1000
    print(f"50 queries completed in {total_time:.1f} ms ({total_time/50:.1f} ms/query)")
    assert errors == 0, f"Encountered {errors} errors during concurrency stress test"
    print("PASS: Zero thread contention or SQLite cursor errors.")

if __name__ == "__main__":
    test_benchmark()
    test_concurrency_stress()
