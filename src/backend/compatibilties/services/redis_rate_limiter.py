import asyncio
import time
import uuid

import redis.asyncio as redis


class CombinedRateLimiter:
    """Apply an endpoint budget and the shared write budget to every attempt."""

    def __init__(self, *limiters):
        self.limiters = limiters
        self.window_seconds = max(
            (float(getattr(limiter, "window_seconds", 0) or 0) for limiter in limiters),
            default=0,
        )

    async def acquire(self) -> None:
        for limiter in self.limiters:
            await limiter.acquire()

    async def penalize(self, cooldown_seconds: float) -> None:
        for limiter in self.limiters:
            await limiter.penalize(cooldown_seconds)


class RedisWindowRateLimiter:
    def __init__(
        self,
        redis_url: str,
        namespace: str,
        requests_per_second: float,
        *,
        max_requests_per_window: int | None = None,
        window_seconds: int | None = None,
        cooldown_seconds: float = 0.0,
    ):
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.requests_per_second = max(0.05, float(requests_per_second))
        self.min_interval = 1.0 / self.requests_per_second
        self.max_requests_per_window = (
            max(1, int(max_requests_per_window))
            if max_requests_per_window is not None
            else None
        )
        self.window_seconds = (
            max(1, int(window_seconds))
            if window_seconds is not None
            else None
        )
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._next_allowed_key = f"rate:{self.namespace}:next_allowed_at"
        self._window_key = f"rate:{self.namespace}:window"
        self._cooldown_key = f"rate:{self.namespace}:cooldown_until"

    async def acquire(self) -> None:
        while True:
            now = time.time()

            try:
                async with self.client.pipeline(transaction=True) as pipe:
                    watched_keys = [self._next_allowed_key]
                    if self.max_requests_per_window and self.window_seconds:
                        watched_keys.extend([self._window_key, self._cooldown_key])

                    await pipe.watch(*watched_keys)
                    raw_value = await pipe.get(self._next_allowed_key)
                    next_allowed_at = float(raw_value) if raw_value else 0.0

                    if self.max_requests_per_window and self.window_seconds:
                        raw_cooldown = await pipe.get(self._cooldown_key)
                        cooldown_until = float(raw_cooldown) if raw_cooldown else 0.0

                        if cooldown_until > now:
                            await pipe.reset()
                            await asyncio.sleep(max(0.05, cooldown_until - now))
                            continue

                    if next_allowed_at > now:
                        await pipe.reset()
                        await asyncio.sleep(max(0.05, next_allowed_at - now))
                        continue

                    if self.max_requests_per_window and self.window_seconds:
                        window_start = now - self.window_seconds
                        current_window_count = await pipe.zcount(
                            self._window_key,
                            window_start,
                            "+inf",
                        )

                        if current_window_count >= self.max_requests_per_window:
                            oldest = await pipe.zrangebyscore(
                                self._window_key,
                                window_start,
                                "+inf",
                                start=0,
                                num=1,
                                withscores=True,
                            )
                            await pipe.reset()
                            # A full local window is normal flow control. Wait
                            # only until its oldest request expires; API 429s
                            # apply their explicit cooldown through penalize().
                            wait_seconds = (
                                oldest[0][1] + self.window_seconds - now + 0.001
                                if oldest else 0.05
                            )
                            await asyncio.sleep(max(0.05, wait_seconds))
                            continue

                    reserved_until = max(now, next_allowed_at) + self.min_interval
                    pipe.multi()
                    pipe.set(
                        self._next_allowed_key,
                        f"{reserved_until:.6f}",
                        ex=max(2, int(self.min_interval * 4) + 2),
                    )

                    if self.max_requests_per_window and self.window_seconds:
                        member = f"{now:.6f}:{uuid.uuid4().hex}"
                        pipe.zremrangebyscore(self._window_key, "-inf", window_start)
                        pipe.zadd(self._window_key, {member: now})
                        pipe.expire(
                            self._window_key,
                            max(
                                2,
                                int(self.window_seconds + self.cooldown_seconds) + 5,
                            ),
                        )

                    await pipe.execute()
                    return

            except redis.WatchError:
                await asyncio.sleep(0.05)

    async def penalize(self, cooldown_seconds: float) -> None:
        cooldown_seconds = max(0.0, float(cooldown_seconds))
        if cooldown_seconds <= 0:
            return

        while True:
            now = time.time()
            try:
                async with self.client.pipeline(transaction=True) as pipe:
                    await pipe.watch(self._next_allowed_key)
                    raw_value = await pipe.get(self._next_allowed_key)
                    next_allowed_at = float(raw_value) if raw_value else 0.0
                    penalized_until = max(
                        next_allowed_at,
                        now + cooldown_seconds,
                    )
                    pipe.multi()
                    pipe.set(
                        self._next_allowed_key,
                        f"{penalized_until:.6f}",
                        ex=max(2, int(cooldown_seconds) + 5),
                    )
                    await pipe.execute()
                    return
            except redis.WatchError:
                await asyncio.sleep(0.05)
