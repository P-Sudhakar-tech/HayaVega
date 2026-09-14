"""Isolated benchmark for `create_asgi_application(..., max_sync_workers=)`,
added to close the ASGI-vs-FastAPI gap documented in
`benchmarks/README.md#asgi-vs-fastapi` and `INDUSTRY_LEADERSHIP_ROADMAP.md`.

Every synchronous view in `mangoo.asgi` used to be dispatched through
`asyncio.to_thread`, which runs on the event loop's *default* executor --
lazily created as `ThreadPoolExecutor(max_workers=min(32, os.cpu_count() + 4))`.
That sizing is tuned for occasional CPU-bound offloading, not for a whole
application's concurrent I/O-bound sync views: on a 4-core machine it is 8
workers, well under the concurrency this project's own benchmark suite
drives (`oha -c 30`), so 22 of every 30 concurrent requests would queue
behind the other 8 regardless of how fast the view itself is.

This simulates that exact shape -- N concurrent "clients" each making
several trivial synchronous calls -- comparing the default executor against
a dedicated, explicitly-sized one.

**Disclosed limitation: this in-process simulation turned out too noisy on
this environment to trust on its own.** Repeated runs do not agree on an
ordering between worker counts (8/16/32/64 all won at least one run across
five repeats) -- likely dominated by thread-creation/warm-up cost and
general scheduling noise at this call volume, not a stable signal about
worker-count itself. Kept here, run and disclosed exactly as measured,
because a benchmark that doesn't support the conclusion is still worth
keeping visible rather than deleting once it stopped being convenient (the
same standard this project's other benchmarks are held to). **The real
evidence for this fix is the HTTP-level before/after comparison in
`benchmarks/README.md#asgi-vs-fastapi`** -- a real `uvicorn` server under
real `oha` load, run twice before and twice after, which was large and
consistent in every run. 16 was picked as the shipped default because it
matches this project's own documented `waitress --threads=16` guidance, not
because this script proved it optimal.

Run: python benchmarks/asgi_executor_sizing.py
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

CONCURRENCY = 30
REQUESTS_PER_CLIENT = 50


def cheap_sync_view(_request=None) -> dict:
    return {"status": "ok"}


async def run_with_default_executor() -> float:
    async def one_call() -> dict:
        return await asyncio.to_thread(cheap_sync_view)

    async def client() -> None:
        for _ in range(REQUESTS_PER_CLIENT):
            await one_call()

    started = perf_counter()
    await asyncio.gather(*(client() for _ in range(CONCURRENCY)))
    elapsed = perf_counter() - started
    return CONCURRENCY * REQUESTS_PER_CLIENT / elapsed


async def run_with_custom_executor(max_workers: int) -> float:
    executor = ThreadPoolExecutor(max_workers=max_workers)
    loop = asyncio.get_running_loop()

    async def one_call() -> dict:
        return await loop.run_in_executor(executor, cheap_sync_view)

    async def client() -> None:
        for _ in range(REQUESTS_PER_CLIENT):
            await one_call()

    started = perf_counter()
    await asyncio.gather(*(client() for _ in range(CONCURRENCY)))
    elapsed = perf_counter() - started
    executor.shutdown(wait=True)
    return CONCURRENCY * REQUESTS_PER_CLIENT / elapsed


def main() -> None:
    cpu_count = os.cpu_count() or 1
    default_workers = min(32, cpu_count + 4)
    print(f"os.cpu_count() = {cpu_count}; asyncio's default executor would use {default_workers} workers")
    print(f"Simulated concurrency: {CONCURRENCY} clients x {REQUESTS_PER_CLIENT} requests each, trivial sync view (no real I/O)\n")

    scenarios = [
        (f"default executor ({default_workers} workers)", run_with_default_executor()),
        ("dedicated executor (16 workers, the shipped default)", run_with_custom_executor(16)),
        ("dedicated executor (32 workers)", run_with_custom_executor(32)),
        ("dedicated executor (64 workers)", run_with_custom_executor(64)),
    ]
    for label, coro in scenarios:
        throughput = asyncio.run(coro)
        print(f"{label:<52} {throughput:9.0f} calls/sec")


if __name__ == "__main__":
    main()
