import asyncio
import time

import redis.asyncio as redis


class RedisWindowRateLimiter:
    def __init__(self, redis_url: str, namespace: str, requests_per_second: int):
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.requests_per_second = max(1, int(requests_per_second))

    async def acquire(self) -> None:
        while True:
            now = time.time()
            window = int(now)
            key = f"rate:{self.namespace}:{window}"

            count = await self.client.incr(key)
            if count == 1:
                await self.client.expire(key, 2)

            if int(count) <= self.requests_per_second:
                return

            sleep_for = max(0.05, 1.0 - (now - window))
            await asyncio.sleep(sleep_for)