"""The in-process rate limiter, with a controllable clock. No database, no network."""

import threading

from app.core.rate_limit import Limit, RateLimiter

LIMIT = Limit(requests=3, window_seconds=60)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_requests_within_the_limit_pass_and_the_next_is_refused() -> None:
    limiter = RateLimiter(clock=Clock())
    assert [limiter.hit("k", LIMIT) for _ in range(3)] == [None, None, None]
    assert limiter.hit("k", LIMIT) == 60


def test_the_window_resets() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    for _ in range(4):
        limiter.hit("k", LIMIT)
    clock.now += 61
    assert limiter.hit("k", LIMIT) is None


def test_retry_after_counts_down() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("k", LIMIT)
    clock.now += 45
    assert limiter.hit("k", LIMIT) == 15


def test_keys_are_independent() -> None:
    limiter = RateLimiter(clock=Clock())
    for _ in range(4):
        limiter.hit("a", LIMIT)
    assert limiter.hit("b", LIMIT) is None


def test_storage_is_bounded_and_expired_windows_are_dropped() -> None:
    clock = Clock()
    limiter = RateLimiter(max_keys=100, clock=clock)
    for i in range(1000):
        limiter.hit(f"client-{i}", LIMIT)
    assert len(limiter) == 100
    clock.now += 61
    limiter.hit("fresh", LIMIT)
    assert len(limiter) == 1


def test_each_bucket_keeps_its_own_window_length() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    long = Limit(requests=1, window_seconds=600)
    limiter.hit("long", long)
    clock.now += 120
    limiter.hit("short", LIMIT)  # Must not evict the still-live long window.
    assert limiter.hit("long", long) is not None


def test_concurrent_hits_are_counted_exactly() -> None:
    limiter = RateLimiter()
    limit = Limit(requests=1000, window_seconds=60)
    results: list[int | None] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(100):
            outcome = limiter.hit("k", limit)
            with lock:
                results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(None) == 1000
    assert len(results) == 1200
