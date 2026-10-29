"""PostgreSQL 检查点。所有者来自已认证服务，不从 thread_id 推断。"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from typing import Any
import json

from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple

from app.db import transaction

OWNER: ContextVar[int | None] = ContextVar("d2_checkpoint_owner", default=None)


def require_owner() -> int:
    owner = OWNER.get()
    if owner is None:
        raise PermissionError("缺少已验证用户，拒绝读写检查点")
    return owner


class OwnerCheckpointSaver(BaseCheckpointSaver):
    def get_tuple(self, config) -> CheckpointTuple | None:
        owner = require_owner()
        thread_id, namespace, checkpoint_id = _key(config)
        with transaction(user_id=owner, read_only=True, role='checkpoint') as conn:
            if checkpoint_id:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, parent_id, serde_type, blob, metadata
                    FROM agent.graph_checkpoint
                    WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s
                    """,
                    (thread_id, namespace, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT checkpoint_id, parent_id, serde_type, blob, metadata
                    FROM agent.graph_checkpoint
                    WHERE thread_id=%s AND checkpoint_ns=%s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (thread_id, namespace),
                ).fetchone()
            if row is None:
                return None
            writes = conn.execute(
                """
                SELECT task_id, channel, serde_type, blob FROM agent.checkpoint_write
                WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s ORDER BY idx
                """,
                (thread_id, namespace, row[0]),
            ).fetchall()
        checkpoint = self.serde.loads_typed((row[2], bytes(row[3])))
        metadata = row[4] or {}
        # Earlier D2 builds discarded framework metadata. A fresh user turn may
        # still read their terminal checkpoint; never crash on that legacy row.
        metadata.setdefault('step', -1)
        pending = [(item[0], item[1], self.serde.loads_typed((item[2], bytes(item[3])))) for item in writes]
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": row[0]}},
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": row[1]}}
                if row[1] else None
            ),
            pending_writes=pending,
        )

    def list(self, config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]:
        owner = require_owner()
        thread_id = config["configurable"]["thread_id"] if config else None
        with transaction(user_id=owner, read_only=True, role='checkpoint') as conn:
            rows = conn.execute(
                """
                SELECT thread_id::text, checkpoint_ns, checkpoint_id
                FROM agent.graph_checkpoint
                WHERE (%s::text IS NULL OR thread_id::text=%s)
                ORDER BY created_at DESC LIMIT %s
                """,
                (thread_id, thread_id, limit or 20),
            ).fetchall()
        for thread, namespace, checkpoint_id in rows:
            found = self.get_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": namespace, "checkpoint_id": checkpoint_id}})
            if found is not None:
                yield found

    def put(self, config, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions) -> dict:
        owner = require_owner()
        thread_id, namespace, _checkpoint_id = _key(config)
        serde_type, blob = self.serde.dumps_typed(checkpoint)
        parent = config["configurable"].get("checkpoint_id")
        with transaction(user_id=owner, role='checkpoint') as conn:
            conn.execute(
                """
                INSERT INTO agent.graph_checkpoint(
                    owner_id, thread_id, checkpoint_ns, checkpoint_id, parent_id, serde_type, blob, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (owner_id, thread_id, checkpoint_ns, checkpoint_id) DO NOTHING
                """,
                (owner, thread_id, namespace, checkpoint["id"], parent, serde_type, blob,
                 json.dumps(dict(metadata),default=str)),
            )
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        owner = require_owner()
        thread_id, namespace, checkpoint_id = _key(config)
        with transaction(user_id=owner, role='checkpoint') as conn:
            for index, (channel, value) in enumerate(writes):
                serde_type, blob = self.serde.dumps_typed(value)
                conn.execute(
                    """
                    INSERT INTO agent.checkpoint_write(
                        owner_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, serde_type, blob
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (owner, thread_id, namespace, checkpoint_id, task_id, index, channel, serde_type, blob),
                )

    def delete_thread(self, thread_id: str) -> None:
        owner = require_owner()
        with transaction(user_id=owner, role='checkpoint') as conn:
            conn.execute("DELETE FROM agent.checkpoint_write WHERE thread_id=%s", (thread_id,))
            conn.execute("DELETE FROM agent.graph_checkpoint WHERE thread_id=%s", (thread_id,))


def _key(config) -> tuple[str, str, str | None]:
    configurable = config["configurable"]
    return configurable["thread_id"], configurable.get("checkpoint_ns") or "", configurable.get("checkpoint_id")
