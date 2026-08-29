"""Batch execution helpers shared by Studio and tests."""

from __future__ import annotations


def group_tasks_by_window(
    tasks,
) -> tuple[dict[int, list[int]], list[int]]:
    """Group task ids by positive window; return (by_window, unassigned_ids)."""
    rows = [dict(row) for row in tasks]
    by_window: dict[int, list[int]] = {}
    unassigned: list[int] = []
    for row in rows:
        task_id = int(row.get("id") or 0)
        if not task_id:
            continue
        try:
            profile = int(row.get("profile_no") or 0)
        except (TypeError, ValueError):
            profile = 0
        if profile > 0:
            by_window.setdefault(profile, []).append(task_id)
        else:
            unassigned.append(task_id)
    return by_window, unassigned


def assign_windows(
    tasks,
    open_windows: list[int],
    max_per_window: int = 1,
) -> list[dict]:
    """Manual windows reserve first; auto tasks then use remaining windows.

    Returns assignment records in original task order. Auto-assigned tasks
    (window_assignment_type == 'auto') are re-matched on every run and never
    become permanent manual windows.
    """
    rows = [dict(row) for row in tasks]
    available: list[int] = []
    for raw_window in open_windows:
        window = int(raw_window)
        if window > 0 and window not in available:
            available.append(window)
    assignments: dict[int, dict] = {}
    used: dict[int, int] = {}

    def is_manual(row: dict) -> bool:
        profile = int(row.get("profile_no") or 0)
        if profile <= 0:
            return False
        return str(row.get("window_assignment_type") or "") != "auto"

    def make(row: dict, profile, assignment_type: str, conflict: str = "", requested=None) -> dict:
        return {
            "task_id": int(row["id"]),
            "profile_no": profile,
            "type": assignment_type,
            "waiting": profile is None,
            "conflict": conflict,
            "requested_window": requested,
        }

    manual_rows = [row for row in rows if is_manual(row)]
    for row in manual_rows:
        profile = int(row["profile_no"])
        assignment_type = "manual_locked" if int(row.get("profile_locked") or 0) == 1 else "manual"
        if profile not in available:
            assignments[row["id"]] = make(row, None, assignment_type, "window_not_open", profile)
        elif used.get(profile, 0) >= max_per_window:
            assignments[row["id"]] = make(row, None, assignment_type, "window_conflict", profile)
        else:
            used[profile] = used.get(profile, 0) + 1
            assignments[row["id"]] = make(row, profile, assignment_type)

    for row in rows:
        if row["id"] in assignments:
            continue
        free = next(
            (window for window in available if used.get(window, 0) < max_per_window),
            None,
        )
        if free is None:
            assignments[row["id"]] = make(row, None, "auto")
        else:
            used[free] = used.get(free, 0) + 1
            assignments[row["id"]] = make(row, free, "auto")
    return [assignments[row["id"]] for row in rows]
