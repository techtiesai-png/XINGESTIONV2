BEGIN;

ALTER TABLE worker_tasks
    ADD COLUMN IF NOT EXISTS origin_task_id INT,
    ADD COLUMN IF NOT EXISTS replay_of_dead_letter_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_worker_tasks_origin_task'
    ) THEN
        ALTER TABLE worker_tasks
            ADD CONSTRAINT fk_worker_tasks_origin_task
            FOREIGN KEY (origin_task_id)
            REFERENCES worker_tasks(id)
            ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_worker_tasks_replay_dead_letter'
    ) THEN
        ALTER TABLE worker_tasks
            ADD CONSTRAINT fk_worker_tasks_replay_dead_letter
            FOREIGN KEY (replay_of_dead_letter_id)
            REFERENCES worker_dead_letters(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_worker_tasks_origin_task
    ON worker_tasks (origin_task_id);
CREATE INDEX IF NOT EXISTS idx_worker_tasks_replay_dead_letter
    ON worker_tasks (replay_of_dead_letter_id);

CREATE TABLE IF NOT EXISTS worker_dead_letter_replays (
    replay_id BIGSERIAL PRIMARY KEY,
    dead_letter_id BIGINT NOT NULL
        REFERENCES worker_dead_letters(id) ON DELETE CASCADE,
    replay_generation INT NOT NULL CHECK (replay_generation > 0),
    replay_task_id INT NOT NULL
        REFERENCES worker_tasks(id) ON DELETE RESTRICT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    selector_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (dead_letter_id, replay_generation),
    UNIQUE (replay_task_id)
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_replays_dead_letter
    ON worker_dead_letter_replays (dead_letter_id, replay_generation DESC);
CREATE INDEX IF NOT EXISTS idx_dead_letter_replays_requested_at
    ON worker_dead_letter_replays (requested_at DESC);

COMMIT;
