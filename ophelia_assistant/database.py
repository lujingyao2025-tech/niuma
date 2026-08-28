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
    status TEXT DEFAULT 'new',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    replied_at TEXT,
    generated_at TEXT,
    drafted_at TEXT,
    last_error TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    UNIQUE(profile_no, source_key, recipient_email)
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
    ) -> int:
        task_ids = self.add_local_tasks([(name, location, email)], [custom_variables])
        task_id = task_ids[0]
        if task_id is None:
            raise ValueError("该邮箱任务已经存在")
        return task_id

    def add_local_tasks(
        self,
        contacts: list[tuple[str, str, str]],
        custom_variables_list: list[dict[str, str] | None] | None = None,
    ) -> list[int | None]:
        """Insert manual rows; re-importing an email refreshes its local data."""
        if not contacts:
            return []
        created_at = now_iso()
        task_ids: list[int | None] = []
        custom_values = custom_variables_list or []
        with self.connect() as conn:
            for index, (name, location, email) in enumerate(contacts):
                custom = custom_values[index] if index < len(custom_values) else {}
                if not isinstance(custom, dict):
                    custom = {}
                serialized_custom = json.dumps(
                    custom, ensure_ascii=False, sort_keys=True
                )
                conn.execute(
                    """INSERT INTO tasks(
                        profile_no, source_key, recipient_email,
                        name_override, location_override, location,
                        location_source, custom_variables, created_at
                    ) VALUES(0, '', ?, ?, ?, ?, 'manual', ?, ?)
                    ON CONFLICT(profile_no, source_key, recipient_email) DO UPDATE SET
                        name_override = excluded.name_override,
                        location_override = excluded.location_override,
                        location = excluded.location,
                        location_source = excluded.location_source,
                        custom_variables = excluded.custom_variables""",
                    (
                        email.strip().lower(),
                        " ".join(name.strip().split()),
                        location.strip(),
                        location.strip(),
                        serialized_custom,
                        created_at,
                    ),
                )
                task_row = conn.execute(
                    """SELECT id FROM tasks
                    WHERE profile_no = 0 AND source_key = '' AND recipient_email = ?""",
                    (email.strip().lower(),),
                ).fetchone()
                task_ids.append(int(task_row[0]) if task_row is not None else None)
        return task_ids

    def list_tasks(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()

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
                    SUM(CASE WHEN status IN ('new','ready','needs_review') THEN 1 ELSE 0 END) AS pending,
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
                "AND status IN ('new', 'ready') ORDER BY id",
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
            "name_override", "location_override",
            "subject", "body", "gender_label", "gender_source", "source_urls",
            "review_reason", "status", "custom_variables",
            "generated_at", "drafted_at", "last_error", "attempts",
        }
        clean = {k: v for k, v in values.items() if k in allowed}
        if not clean:
            return
        sets = ", ".join(f"{key}=?" for key in clean)
        with self.connect() as conn:
            if "profile_no" in clean:
                current = conn.execute(
                    "SELECT profile_no, profile_locked FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
                if (
                    current is not None
                    and int(current["profile_locked"] or 0) == 1
                    and int(clean["profile_no"] or 0) != int(current["profile_no"] or 0)
                ):
                    raise ValueError("该任务的窗口编号已锁定，只有删除任务才能解除")
            conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*clean.values(), task_id))

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
        column = "replied_at" if replied else "sent_at"
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET status=?, {column}=? "
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
                "UPDATE tasks SET status='drafted', sent_at=NULL, replied_at=NULL, "
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
