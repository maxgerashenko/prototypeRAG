"""asyncio.run() in a fresh thread.

pytest-playwright's sync API keeps an event loop running in the main thread for the
whole session, so after the UI tests a plain asyncio.run() fails with "cannot be called
from a running event loop". Running it in a worker thread works in any test order.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor


def run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
