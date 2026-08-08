from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(slots=True, frozen=True)
class ReplaySelector:
    dead_letter_ids: tuple[int, ...] = ()
    task_type: str | None = None
    failure_class: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "dead_letter_ids": list(self.dead_letter_ids),
            "task_type": self.task_type,
            "failure_class": self.failure_class,
        }


@dataclass(slots=True, frozen=True)
class ReplayResult:
    dead_letter_id: int
    replay_generation: int
    replay_task_id: int


class DeadLetterReplayService:
    """Selectively replay unreplayed dead letters with transactional lineage.

    A dead letter is locked, its replacement task and outbox row are created,
    replay audit history is inserted, and the archive is marked replayed in one
    PostgreSQL transaction. A crash cannot leave an untracked replacement task.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def replay(
        self,
        *,
        selector: ReplaySelector | None = None,
        limit: int = 100,
        max_attempts: int = 5,
        priority: int = 50,
    ) -> list[ReplayResult]:
        if limit <= 0:
            raise ValueError("limit must be > 0")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")

        selector = selector or ReplaySelector()
        selected_ids = list(selector.dead_letter_ids) or None
        selector_metadata = json.dumps(selector.metadata())

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT
                        dl.id,
                        dl.original_task_id,
                        dl.task_type,
                        dl.payload,
                        dl.replay_generation
                    FROM worker_dead_letters dl
                    WHERE dl.replayed_at IS NULL
                      AND ($2::bigint[] IS NULL OR dl.id = ANY($2::bigint[]))
                      AND ($3::text IS NULL OR dl.task_type = $3)
                      AND ($4::text IS NULL OR dl.failure_class = $4)
                    ORDER BY dl.failed_at ASC, dl.id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1;
                    """,
                    limit,
                    selected_ids,
                    selector.task_type,
                    selector.failure_class,
                )

                results: list[ReplayResult] = []
                for row in rows:
                    dead_letter_id = int(row["id"])
                    replay_generation = int(row["replay_generation"] or 0) + 1
                    idempotency_key = (
                        f"dead-letter:{dead_letter_id}:replay:{replay_generation}"
                    )

                    task_row = await conn.fetchrow(
                        """
                        INSERT INTO worker_tasks (
                            task_type,
                            payload,
                            status,
                            attempts,
                            max_attempts,
                            next_run_at,
                            idempotency_key,
                            priority,
                            delivery_generation,
                            origin_task_id,
                            replay_of_dead_letter_id,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            $1, $2, 'PENDING', 0, $3, NOW(), $4, $5, 0,
                            $6, $7, NOW(), NOW()
                        )
                        ON CONFLICT (idempotency_key) DO UPDATE SET
                            updated_at = worker_tasks.updated_at
                        RETURNING id, delivery_generation, next_run_at;
                        """,
                        str(row["task_type"]),
                        row["payload"],
                        max_attempts,
                        idempotency_key,
                        priority,
                        int(row["original_task_id"]),
                        dead_letter_id,
                    )
                    replay_task_id = int(task_row["id"])
                    delivery_generation = int(task_row["delivery_generation"])

                    await conn.execute(
                        """
                        INSERT INTO task_outbox (
                            task_id,
                            delivery_generation,
                            event_type,
                            payload,
                            available_at,
                            created_at
                        )
                        VALUES (
                            $1,
                            $2,
                            'TASK_READY',
                            jsonb_build_object(
                                'task_id', $1::int,
                                'generation', $2::int
                            ),
                            $3,
                            NOW()
                        )
                        ON CONFLICT (task_id, delivery_generation) DO NOTHING;
                        """,
                        replay_task_id,
                        delivery_generation,
                        task_row["next_run_at"],
                    )

                    await conn.execute(
                        """
                        INSERT INTO worker_dead_letter_replays (
                            dead_letter_id,
                            replay_generation,
                            replay_task_id,
                            requested_at,
                            selector_metadata
                        )
                        VALUES ($1, $2, $3, NOW(), $4::jsonb)
                        ON CONFLICT (dead_letter_id, replay_generation)
                        DO NOTHING;
                        """,
                        dead_letter_id,
                        replay_generation,
                        replay_task_id,
                        selector_metadata,
                    )

                    status = await conn.execute(
                        """
                        UPDATE worker_dead_letters
                        SET replayed_at = NOW(),
                            replay_task_id = $2,
                            replay_generation = $3
                        WHERE id = $1
                          AND replayed_at IS NULL;
                        """,
                        dead_letter_id,
                        replay_task_id,
                        replay_generation,
                    )
                    if status != "UPDATE 1":
                        raise RuntimeError(
                            "dead-letter replay ownership changed unexpectedly "
                            f"for dead_letter_id={dead_letter_id}"
                        )

                    results.append(
                        ReplayResult(
                            dead_letter_id=dead_letter_id,
                            replay_generation=replay_generation,
                            replay_task_id=replay_task_id,
                        )
                    )

                return results
