"""Batch execution helpers shared by Studio and tests."""

from __future__ import annotations


def group_tasks_by_window(
    tasks,
) -> tuple[dict[int, list[int]], list[int]]:
    """Group task ids by positive window; return (by_window, unassigned_ids)."""
    by_window: dict[int, list[int]] = {}
    unassigned: list[int] = []
    for row in tasks:
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
