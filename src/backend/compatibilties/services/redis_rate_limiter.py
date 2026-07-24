import asyncio
import time
import uuid

import redis.asyncio as redis


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
                            cooldown_until = now + self.cooldown_seconds
                            pipe.multi()
                            pipe.set(
                                self._cooldown_key,
                                f"{cooldown_until:.6f}",
                                ex=max(2, int(self.cooldown_seconds) + 5),
                            )
                            pipe.set(
                                self._next_allowed_key,
                                f"{cooldown_until:.6f}",
                                ex=max(2, int(self.cooldown_seconds) + 5),
                            )
                            await pipe.execute()
                            await asyncio.sleep(max(0.05, self.cooldown_seconds))
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
