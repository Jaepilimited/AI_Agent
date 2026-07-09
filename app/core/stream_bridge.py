"""Bridge a blocking sync generator (e.g. an LLM client's generate_stream)
into an async generator by running it in a background thread.

Mirrors the queue + call_soon_threadsafe pattern already used for the
BigQuery streaming route (see orchestrator.py's _bq_worker), factored out
so CS/Team/Multi routes can reuse it instead of re-implementing the bridge.
"""
import asyncio
from typing import AsyncIterable, AsyncIterator, Callable, Iterable


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


class StreamTimeout(Exception):
    """Raised by stream_with_timeout() when total elapsed time exceeds the budget."""


async def stream_with_timeout(
    async_gen: AsyncIterable[str], timeout_s: float
) -> AsyncIterator[str]:
    """Iterate an async generator, raising StreamTimeout if total time exceeds timeout_s.

    IMPORTANT: each __anext__() is awaited with the *remaining* budget, and we
    never call __anext__() again after a timeout. asyncio.wait_for() cancels
    the wrapped awaitable when it times out, and cancelling an async
    generator's __anext__() closes/exhausts it — a second __anext__() call on
    an already-cancelled generator raises StopAsyncIteration immediately
    instead of resuming. A poll-and-retry loop (re-calling __anext__() after
    each short timeout) silently drops the entire stream because of this;
    that bug shipped once and was caught by scripts/_test_handle_multi.py-style
    direct testing before it reached production — don't reintroduce polling here.
    """
    it = async_gen.__aiter__()
    start = asyncio.get_event_loop().time()
    while True:
        remaining = timeout_s - (asyncio.get_event_loop().time() - start)
        if remaining <= 0:
            raise StreamTimeout()
        try:
            chunk = await asyncio.wait_for(it.__anext__(), timeout=remaining)
        except asyncio.TimeoutError:
            raise StreamTimeout()
        except StopAsyncIteration:
            return
        yield chunk
