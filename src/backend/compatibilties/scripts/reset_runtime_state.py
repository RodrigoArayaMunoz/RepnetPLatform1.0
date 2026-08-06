import asyncio
import json
import os

from services.runtime_recovery_service import reset_runtime_state


async def main() -> None:
    result = await reset_runtime_state(
        resume_process_queue=(
            os.getenv("RESUME_PROCESS_QUEUE", "").lower() == "true"
        ),
        resume_publication_sync=(
            os.getenv("RESUME_PUBLICATION_SYNC", "").lower() == "true"
        ),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
