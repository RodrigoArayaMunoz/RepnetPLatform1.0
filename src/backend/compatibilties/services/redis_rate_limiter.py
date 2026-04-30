import asyncio
import time

import redis.asyncio as redis


class RedisWindowRateLimiter:
    def __init__(self, redis_url: str, namespace: str, requests_per_second: float):
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.requests_per_second = max(0.05, float(requests_per_second))
        self.min_interval = 1.0 / self.requests_per_second
        self._next_allowed_key = f"rate:{self.namespace}:next_allowed_at"

    async def acquire(self) -> None:
        while True:
            now = time.time()

            try:
                async with self.client.pipeline(transaction=True) as pipe:
                    await pipe.watch(self._next_allowed_key)
                    raw_value = await pipe.get(self._next_allowed_key)
                    next_allowed_at = float(raw_value) if raw_value else 0.0

                    if next_allowed_at > now:
                        await pipe.reset()
                        await asyncio.sleep(max(0.05, next_allowed_at - now))
                        continue

                    reserved_until = max(now, next_allowed_at) + self.min_interval
                    pipe.multi()
                    pipe.set(
                        self._next_allowed_key,
                        f"{reserved_until:.6f}",
                        ex=max(2, int(self.min_interval * 4) + 2),
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
                    penalized_until = max(now, next_allowed_at) + cooldown_seconds
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
