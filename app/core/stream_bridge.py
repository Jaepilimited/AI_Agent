"""Bridge a blocking sync generator (e.g. an LLM client's generate_stream)
into an async generator by running it in a background thread.

Mirrors the queue + call_soon_threadsafe pattern already used for the
BigQuery streaming route (see orchestrator.py's _bq_worker), factored out
so CS/Team/Multi routes can reuse it instead of re-implementing the bridge.
"""
import asyncio
from typing import AsyncIterator, Callable, Iterable


async def stream_sync_generator(sync_gen_factory: Callable[[], Iterable[str]]) -> AsyncIterator[str]:
    """Run sync_gen_factory() in a background thread, yielding its chunks asynchronously.

    Args:
        sync_gen_factory: A zero-arg callable returning a sync iterable of str
            chunks (e.g. ``lambda: llm.generate_stream(prompt, temperature=0.3)``).

    Yields:
        str chunks as they arrive from the sync generator.

    Raises:
        Whatever exception the sync generator raised, re-raised on the calling
        (async) side after the background thread finishes.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    def _worker():
        try:
            for chunk in sync_gen_factory():
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    loop.run_in_executor(None, _worker)

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            return
        if isinstance(item, Exception):
            raise item
        yield item
