from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .config import app_data_dir


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_no INTEGER NOT NULL,
    profile_locked INTEGER DEFAULT 0,
    source_key TEXT NOT NULL DEFAULT '',
    recipient_email TEXT NOT NULL,
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    location TEXT DEFAULT '',
    location_source TEXT DEFAULT '',
    name_override TEXT DEFAULT '',
    location_override TEXT DEFAULT '',
    sender_name_override TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    gender_label TEXT DEFAULT 'unspecified',
    gender_source TEXT DEFAULT '',
    source_urls TEXT DEFAULT '[]',
    review_reason TEXT DEFAULT '',
    custom_variables TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    replied_at TEXT,
    generated_at TEXT,
    drafted_at TEXT,
    last_error TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    UNIQUE(profile_no, source_key, recipient_email)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self) -> None:
        self.path = app_data_dir() / "ophelia.db"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "gender_label" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN gender_label TEXT DEFAULT 'unspecified'")
            if "gender_source" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN gender_source TEXT DEFAULT ''")
            if "source_urls" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN source_urls TEXT DEFAULT '[]'")
            if "review_reason" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN review_reason TEXT DEFAULT ''")
            if "name_override" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN name_override TEXT DEFAULT ''")
            if "location_override" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN location_override TEXT DEFAULT ''")
            if "sender_name_override" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN sender_name_override TEXT DEFAULT ''")
            if "profile_locked" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN profile_locked INTEGER DEFAULT 0")
            if "custom_variables" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN custom_variables TEXT DEFAULT '{}'")
            if "generated_at" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN generated_at TEXT")
            if "drafted_at" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN drafted_at TEXT")
            if "last_error" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN last_error TEXT DEFAULT ''")
            if "attempts" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN attempts INTEGER DEFAULT 0")
            if "linkedin_url" in columns and "source_key" not in columns:
                conn.execute("ALTER TABLE tasks RENAME TO tasks_legacy_source")
                conn.executescript(SCHEMA)
                conn.execute(
                    """INSERT INTO tasks(
                        id, profile_no, profile_locked, source_key, recipient_email,
                        first_name, last_name, location, location_source,
                        name_override, location_override, subject, body,
                        gender_label, gender_source, source_urls, review_reason,
                        status, created_at, sent_at, replied_at
                    )
                    SELECT
                        id, profile_no, profile_locked, 'legacy-' || id, recipient_email,
                        first_name, last_name, location, location_source,
                        name_override, location_override, subject, body,
                        gender_label, gender_source, source_urls, review_reason,
                        status, created_at, sent_at, replied_at
                    FROM tasks_legacy_source"""
                )
                conn.execute("DROP TABLE tasks_legacy_source")
            if "campaign_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN campaign_id INTEGER")
            final_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(tasks)")
            }
            for column, definition in (
                ("assigned_at", "TEXT"),
                ("fill_started_at", "TEXT"),
                ("validation_at", "TEXT"),
                ("send_attempt_started_at", "TEXT"),
                ("send_clicked_at", "TEXT"),
                ("failed_at", "TEXT"),
                ("last_failed_at", "TEXT"),
                ("failure_stage", "TEXT DEFAULT ''"),
                ("browser_type", "TEXT DEFAULT ''"),
                ("window_assignment_type", "TEXT DEFAULT ''"),
                ("resolved_sender_name", "TEXT DEFAULT ''"),
                ("sender_name_source", "TEXT DEFAULT ''"),
                ("sent_method", "TEXT DEFAULT ''"),
                ("needs_manual_review", "INTEGER DEFAULT 0"),
                ("render_context_hash", "TEXT DEFAULT ''"),
            ):
                if column not in final_columns:
                    conn.execute(
                        f"ALTER TABLE tasks ADD COLUMN {column} {definition}"
                    )
            default_id = self._default_campaign_id(conn)
            conn.execute(
                "UPDATE tasks SET campaign_id = ? "
                "WHERE campaign_id IS NULL OR campaign_id = 0",
                (default_id,),
            )
            conn.execute("UPDATE tasks SET status='pending' WHERE status='new'")
            conn.execute("UPDATE tasks SET status='generated' WHERE status='ready'")
            conn.execute(
                "UPDATE tasks SET status='needs_review', needs_manual_review=1 "
                "WHERE status='sending' AND send_clicked_at IS NOT NULL "
                "AND send_clicked_at <> ''"
            )
            conn.execute(
                "UPDATE tasks SET status=CASE "
                "WHEN drafted_at IS NULL THEN 'generated' ELSE 'drafted' END "
                "WHERE status IN ('assigned', 'filling', 'validating') "
                "OR (status='sending' AND (send_clicked_at IS NULL OR send_clicked_at=''))"
            )
            conn.execute(
                "UPDATE tasks SET sent_method='confirmed' "
                "WHERE status='sent' AND send_clicked_at IS NOT NULL "
                "AND (sent_method IS NULL OR sent_method='')"
            )
            conn.execute(
                "UPDATE tasks SET sent_method='manual' "
                "WHERE status='sent' AND (sent_method IS NULL OR sent_method='')"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_local_task(
        self,
        name: str,
        location: str,
        email: str,
        custom_variables: dict[str, str] | None = None,
        campaign_id: int | None = None,
    ) -> int:
        task_ids = self.add_local_tasks(
            [(name, location, email)],
            [custom_variables],
            campaign_id=campaign_id,
        )
        task_id = task_ids[0]
        if task_id is None:
            raise ValueError("该邮箱任务已经存在")
        return task_id

    def add_local_tasks(
        self,
        contacts: list[tuple[str, str, str]],
        custom_variables_list: list[dict[str, str] | None] | None = None,
        campaign_id: int | None = None,
    ) -> list[int | None]:
        """Insert manual rows; re-importing an email refreshes its local data."""
        if not contacts:
            return []
        created_at = now_iso()
        task_ids: list[int | None] = []
        custom_values = custom_variables_list or []
        with self.connect() as conn:
            resolved_campaign = campaign_id or self._default_campaign_id(conn)
            for index, (name, location, email) in enumerate(contacts):
                custom = custom_values[index] if index < len(custom_values) else {}
                if not isinstance(custom, dict):
                    custom = {}
                serialized_custom = json.dumps(
                    custom, ensure_ascii=False, sort_keys=True
                )
                normalized_email = email.strip().lower()
                normalized_name = " ".join(name.strip().split())
                normalized_location = location.strip()
                existing = conn.execute(
                    """SELECT id FROM tasks
                    WHERE source_key = '' AND recipient_email = ?
                    ORDER BY id LIMIT 1""",
                    (normalized_email,),
                ).fetchone()
                if existing is None:
                    cursor = conn.execute(
                        """INSERT INTO tasks(
                            profile_no, source_key, recipient_email,
                            name_override, location_override, location,
                            location_source, custom_variables, campaign_id,
                            status, created_at
                        ) VALUES(0, '', ?, ?, ?, ?, 'manual', ?, ?, 'pending', ?)""",
                        (
                            normalized_email,
                            normalized_name,
                            normalized_location,
                            normalized_location,
                            serialized_custom,
                            resolved_campaign,
                            created_at,
                        ),
                    )
                    task_ids.append(int(cursor.lastrowid))
                    continue
                task_id = int(existing["id"])
                conn.execute(
                    """UPDATE tasks SET
                        name_override=?, location_override=?, location=?,
                        location_source='manual', custom_variables=?
                    WHERE id=?""",
                    (
                        normalized_name,
                        normalized_location,
                        normalized_location,
                        serialized_custom,
                        task_id,
                    ),
                )
                task_ids.append(task_id)
        return task_ids

    def list_tasks(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

    def stats(self) -> dict[str, int]:
        """Global mutually-exclusive task counts for the studio stats strip."""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'generated' THEN 1 ELSE 0 END) AS generated,
                    SUM(CASE WHEN status = 'waiting_window' THEN 1 ELSE 0 END) AS waiting,
                    SUM(CASE WHEN status IN ('assigned', 'filling', 'validating',
                        'drafted', 'sending')
                        THEN 1 ELSE 0 END) AS processing,
                    SUM(CASE WHEN status = 'sent' AND sent_method = 'confirmed'
                        THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN (status = 'sent' AND sent_method = 'manual')
                        OR status = 'replied'
                        THEN 1 ELSE 0 END) AS sent_manual,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'needs_review' THEN 1 ELSE 0 END) AS review,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS other
                FROM tasks"""
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "pending": int(row["pending"] or 0),
            "generated": int(row["generated"] or 0),
            "waiting": int(row["waiting"] or 0),
            "processing": int(row["processing"] or 0),
            "sent": int(row["sent"] or 0),
            "sent_manual": int(row["sent_manual"] or 0),
            "failed": int(row["failed"] or 0),
            "review": int(row["review"] or 0),
            "other": int(row["other"] or 0),
        }

    @staticmethod
    def _default_campaign_id(conn) -> int:
        row = conn.execute(
            "SELECT id FROM campaigns WHERE name = ? ORDER BY id LIMIT 1",
            ("默认批次",),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        now = now_iso()
        cursor = conn.execute(
            "INSERT INTO campaigns(name, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("默认批次", "", now, now),
        )
        return int(cursor.lastrowid)

    def list_campaigns(self) -> list[dict[str, object]]:
        """Return campaigns with task counts for the batch list."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.note, c.created_at, c.updated_at,
                        COUNT(t.id) AS task_count,
                        SUM(CASE WHEN t.status IN ('sent', 'replied')
                            THEN 1 ELSE 0 END) AS sent_count,
                        SUM(CASE WHEN t.status NOT IN ('sent', 'replied', 'failed', 'cancelled')
                            THEN 1 ELSE 0 END) AS pending_count,
                        SUM(CASE WHEN t.status = 'drafted'
                            THEN 1 ELSE 0 END) AS drafted_count,
                        SUM(CASE WHEN t.last_error <> ''
                            THEN 1 ELSE 0 END) AS failed_count
                    FROM campaigns c
                    LEFT JOIN tasks t ON t.campaign_id = c.id
                    GROUP BY c.id
                    ORDER BY c.created_at DESC, c.id DESC"""
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "note": str(row["note"] or ""),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "task_count": int(row["task_count"] or 0),
                "sent_count": int(row["sent_count"] or 0),
                "pending_count": int(row["pending_count"] or 0),
                "drafted_count": int(row["drafted_count"] or 0),
                "failed_count": int(row["failed_count"] or 0),
            }
            for row in rows
        ]

    def create_campaign(self, name: str, note: str = "") -> int:
        clean_name = " ".join(str(name or "").strip().split())
        if not clean_name:
            raise ValueError("活动/批次名称不能为空")
        now = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO campaigns(name, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (clean_name, str(note or ""), now, now),
            )
            return int(cursor.lastrowid)

    def update_campaign(
        self, campaign_id: int, name: str | None = None, note: str | None = None
    ) -> None:
        values: dict[str, object] = {}
        if name is not None:
            clean_name = " ".join(str(name).strip().split())
            if not clean_name:
                raise ValueError("活动/批次名称不能为空")
            values["name"] = clean_name
        if note is not None:
            values["note"] = str(note)
        if not values:
            return
        values["updated_at"] = now_iso()
        sets = ", ".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE campaigns SET {sets} WHERE id=?",
                (*values.values(), campaign_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("活动/批次不存在")

    def delete_campaign(self, campaign_id: int, move_to: int | None = None) -> int:
        """Delete a campaign. Tasks are moved to move_to or deleted with it."""
        with self.connect() as conn:
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"]
            )
            if total <= 1:
                raise ValueError("至少保留一个活动/批次")
            target = conn.execute(
                "SELECT id FROM campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if target is None:
                raise ValueError("活动/批次不存在")
            if move_to is not None:
                destination = conn.execute(
                    "SELECT id FROM campaigns WHERE id=?",
                    (move_to,),
                ).fetchone()
                if destination is None:
                    raise ValueError("目标活动/批次不存在")
                affected = int(
                    conn.execute(
                        "UPDATE tasks SET campaign_id=? WHERE campaign_id=?",
                        (move_to, campaign_id),
                    ).rowcount
                )
            else:
                affected = int(
                    conn.execute(
                        "DELETE FROM tasks WHERE campaign_id=?", (campaign_id,)
                    ).rowcount
                )
            conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
            return affected

    def tasks_by_campaign(
        self,
        campaign_id: int,
        statuses: list[str] | None = None,
        search: str = "",
    ) -> list[sqlite3.Row]:
        """Return tasks for one campaign, optionally filtered by status/text."""
        where = ["campaign_id = ?"]
        params: list[object] = [campaign_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
        keyword = " ".join(str(search or "").strip().split())
        if keyword:
            where.append(
                "(name_override LIKE ? OR first_name LIKE ? OR last_name LIKE ? "
                "OR location_override LIKE ? OR location LIKE ? "
                "OR recipient_email LIKE ? OR subject LIKE ?)"
            )
            pattern = f"%{keyword}%"
            params.extend([pattern] * 7)
        clause = " AND ".join(where)
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM tasks WHERE {clause} ORDER BY id DESC",
                params,
            ).fetchall()

    def move_tasks_to_campaign(
        self, task_ids: list[int], campaign_id: int
    ) -> int:
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return 0
        with self.connect() as conn:
            target = conn.execute(
                "SELECT id FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if target is None:
                raise ValueError("活动/批次不存在")
            placeholders = ",".join("?" for _ in ids)
            cursor = conn.execute(
                f"UPDATE tasks SET campaign_id=? WHERE id IN ({placeholders})",
                (campaign_id, *ids),
            )
            return int(cursor.rowcount)

    def backup(self, keep: int = 10) -> Path | None:
        """Snapshot the database into backups/, keeping the newest copies."""
        backups_dir = self.path.parent / "backups"
        try:
            backups_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        target = backups_dir / f"ophelia_{stamp}.db"
        if target.exists():
            return target
        try:
            source = sqlite3.connect(self.path)
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        except sqlite3.Error:
            return None
        backups = sorted(backups_dir.glob("ophelia_*.db"))
        for old in backups[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    def list_backups(self) -> list[Path]:
        backups_dir = self.path.parent / "backups"
        if not backups_dir.exists():
            return []
        return sorted(backups_dir.glob("ophelia_*.db"), reverse=True)

    def restore_backup(self, backup_path) -> bool:
        import shutil

        backup_path = Path(backup_path)
        if not backup_path.exists():
            return False
        with self.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        shutil.copy2(backup_path, self.path)
        return True

    def daily_stats(self) -> dict[str, object]:
        local_now = datetime.now()
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        utc_start = local_start.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        utc_end = local_end.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        with self.connect() as conn:
            def count(column: str) -> int:
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM tasks "
                    f"WHERE {column} >= ? AND {column} < ?",
                    (utc_start, utc_end),
                ).fetchone()
                return int(row["n"])

            generated_today = count("generated_at")
            drafted_today = count("drafted_at")
            sent_today = count("sent_at")
            failed_total = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM tasks WHERE last_error <> ''"
                ).fetchone()["n"]
            )
            by_window = conn.execute(
                "SELECT profile_no, COUNT(*) AS n FROM tasks "
                "WHERE sent_at IS NOT NULL AND profile_no > 0 "
                "GROUP BY profile_no ORDER BY n DESC"
            ).fetchall()
        return {
            "generated_today": generated_today,
            "drafted_today": drafted_today,
            "sent_today": sent_today,
            "failed_total": failed_total,
            "sent_by_window": {
                int(row["profile_no"]): int(row["n"]) for row in by_window
            },
        }

    def window_status(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT profile_no,
                    SUM(CASE WHEN status = 'drafted' THEN 1 ELSE 0 END) AS drafted,
                    SUM(CASE WHEN status NOT IN ('sent','replied','failed','cancelled')
                        THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status IN ('sent','replied') THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN last_error <> '' THEN 1 ELSE 0 END) AS failed
                FROM tasks
                WHERE profile_no > 0
                GROUP BY profile_no
                ORDER BY profile_no"""
            ).fetchall()
        return [
            {
                "window": int(row["profile_no"]),
                "drafted": int(row["drafted"]),
                "pending": int(row["pending"]),
                "sent": int(row["sent"]),
                "failed": int(row["failed"]),
            }
            for row in rows
        ]

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def get_tasks(self, task_ids: list[int]) -> list[sqlite3.Row]:
        """Load several tasks with one connection while preserving input order."""
        ids = [int(task_id) for task_id in task_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE id IN ({placeholders})", ids
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        return [by_id[task_id] for task_id in ids if task_id in by_id]

    def pending_tasks_by_profiles(
        self, profile_numbers: list[int]
    ) -> list[sqlite3.Row]:
        """Return unsent tasks locked to the given windows for template sync."""
        numbers = [int(number) for number in profile_numbers if int(number) > 0]
        if not numbers:
            return []
        placeholders = ",".join("?" for _ in numbers)
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM tasks WHERE profile_no IN (" + placeholders + ") "
                "AND status IN ('pending', 'generated', 'assigned') ORDER BY id",
                numbers,
            ).fetchall()

    def pending_counts_by_window(self) -> dict[int, int]:
        """Count unsent tasks per window for balanced generation assignment."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT profile_no, COUNT(*) AS n FROM tasks "
                "WHERE profile_no > 0 AND status NOT IN ('sent', 'replied') "
                "GROUP BY profile_no"
            ).fetchall()
        return {int(row["profile_no"]): int(row["n"]) for row in rows}

    def update_task(self, task_id: int, **values: object) -> None:
        allowed = {
            "profile_no", "profile_locked", "first_name", "last_name", "location", "location_source",
            "name_override", "location_override", "sender_name_override",
            "subject", "body", "gender_label", "gender_source", "source_urls",
            "review_reason", "status", "custom_variables",
            "campaign_id", "generated_at", "assigned_at", "fill_started_at",
            "validation_at", "drafted_at", "send_attempt_started_at",
            "send_clicked_at", "sent_at", "replied_at",
            "failed_at", "last_failed_at", "failure_stage", "browser_type",
            "window_assignment_type", "resolved_sender_name",
            "sender_name_source", "sent_method", "needs_manual_review",
            "render_context_hash", "last_error", "attempts",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"不允许更新任务字段：{', '.join(unknown)}")
        if not values:
            return
        sets = ", ".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            current = conn.execute(
                "SELECT profile_no, profile_locked FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if current is None:
                raise ValueError("任务不存在")
            if "profile_no" in values:
                if (
                    int(current["profile_locked"] or 0) == 1
                    and int(values["profile_no"] or 0) != int(current["profile_no"] or 0)
                ):
                    raise ValueError("该任务的窗口编号已锁定，只有删除任务才能解除")
            conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*values.values(), task_id))

    def lock_task_profile(self, task_id: int, profile_no: int) -> int:
        """Set and permanently lock a task's browser profile until deletion."""
        if int(profile_no) <= 0:
            raise ValueError("浏览器窗口编号必须大于 0")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT profile_no, profile_locked FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ValueError("任务不存在")
            if int(row["profile_locked"] or 0) == 1:
                return int(row["profile_no"] or 0)
            conn.execute(
                "UPDATE tasks SET profile_no=?, profile_locked=1 WHERE id=?",
                (int(profile_no), task_id),
            )
            return int(profile_no)

    def delete_task(self, task_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def delete_tasks(self, task_ids: list[int]) -> int:
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            cur = conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", ids)
            return int(cur.rowcount)

    def mark_sent(self, task_ids: list[int], replied: bool = False) -> int:
        """Mark tasks as sent/replied and timestamp the transition."""
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return 0
        status = "replied" if replied else "sent"
        stamp = now_iso()
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            if replied:
                cur = conn.execute(
                    "UPDATE tasks SET status=?, replied_at=?, "
                    "sent_at=COALESCE(sent_at, ?), sent_method='manual' "
                    f"WHERE id IN ({placeholders})",
                    (status, stamp, stamp, *ids),
                )
            else:
                cur = conn.execute(
                    f"UPDATE tasks SET status=?, sent_at=?, sent_method='manual' "
                    f"WHERE id IN ({placeholders})",
                    (status, stamp, *ids),
                )
            return int(cur.rowcount)

    def unmark_sent(self, task_ids: list[int]) -> int:
        """Undo a sent/replied mark and return the task to draft state."""
        ids = list(dict.fromkeys(int(task_id) for task_id in task_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status=CASE "
                "WHEN drafted_at IS NULL THEN 'generated' ELSE 'drafted' END, "
                "sent_at=NULL, replied_at=NULL, "
                "sent_method='', "
                f"last_error='' WHERE id IN ({placeholders}) "
                "AND status IN ('sent', 'replied')",
                ids,
            )
            return int(cur.rowcount)

    def clear_tasks(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            conn.execute("DELETE FROM tasks")
            return int(row["n"])
