import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ophelia_assistant.database import Database


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "ophelia_assistant.database.app_data_dir",
            return_value=Path(self._temp.name),
        )
        self._patch.start()
        self.db = Database()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def test_migration_assigns_existing_tasks_to_default_campaign(self) -> None:
        task_id = self.db.add_local_task("Alex", "Seattle", "alex@example.com")
        campaigns = self.db.list_campaigns()
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(campaigns[0]["name"], "默认批次")
        self.assertEqual(campaigns[0]["task_count"], 1)
        task = self.db.get_task(task_id)
        self.assertEqual(task["campaign_id"], campaigns[0]["id"])

    def test_campaign_crud_and_counts(self) -> None:
        campaign_id = self.db.create_campaign("八月跟进", "德国经销商")
        self.db.add_local_task(
            "Ben", "Munich", "ben@example.com", campaign_id=campaign_id
        )
        self.db.add_local_task(
            "Cara", "Berlin", "cara@example.com", campaign_id=campaign_id
        )
        self.db.update_campaign(campaign_id, name="九月跟进")
        campaigns = {item["id"]: item for item in self.db.list_campaigns()}
        campaign = campaigns[campaign_id]
        self.assertEqual(campaign["name"], "九月跟进")
        self.assertEqual(campaign["note"], "德国经销商")
        self.assertEqual(campaign["task_count"], 2)

        rows = self.db.tasks_by_campaign(campaign_id)
        self.assertEqual(len(rows), 2)
        matched = self.db.tasks_by_campaign(campaign_id, search="Munich")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["recipient_email"], "ben@example.com")

    def test_tasks_filtered_by_status(self) -> None:
        campaign_id = self.db.create_campaign("Seattle 客户")
        first = self.db.add_local_task(
            "Alex", "Seattle", "alex@example.com", campaign_id=campaign_id
        )
        second = self.db.add_local_task(
            "Dana", "Seattle", "dana@example.com", campaign_id=campaign_id
        )
        self.db.update_task(first, status="sent", sent_at="2026-08-29T00:00:00+00:00")
        self.db.update_task(second, status="drafted", drafted_at="2026-08-29T00:00:00+00:00")
        sent = self.db.tasks_by_campaign(campaign_id, statuses=["sent"])
        drafted = self.db.tasks_by_campaign(campaign_id, statuses=["drafted"])
        self.assertEqual([int(row["id"]) for row in sent], [first])
        self.assertEqual([int(row["id"]) for row in drafted], [second])

    def test_move_tasks_between_campaigns(self) -> None:
        source = self.db.create_campaign("A 批次")
        target = self.db.create_campaign("B 批次")
        task_id = self.db.add_local_task(
            "Eve", "Hamburg", "eve@example.com", campaign_id=source
        )
        affected = self.db.move_tasks_to_campaign([task_id], target)
        self.assertEqual(affected, 1)
        self.assertEqual(self.db.get_task(task_id)["campaign_id"], target)

    def test_delete_campaign_moves_or_deletes_tasks(self) -> None:
        source = self.db.create_campaign("旧批次")
        target = self.db.create_campaign("新批次")
        task_id = self.db.add_local_task(
            "Finn", "Cologne", "finn@example.com", campaign_id=source
        )
        moved = self.db.delete_campaign(source, move_to=target)
        self.assertEqual(moved, 1)
        self.assertEqual(self.db.get_task(task_id)["campaign_id"], target)

        second = self.db.create_campaign("待删除")
        task_id = self.db.add_local_task(
            "Grace", "Frankfurt", "grace@example.com", campaign_id=second
        )
        removed = self.db.delete_campaign(second)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.db.get_task(task_id))

    def test_last_campaign_cannot_be_deleted(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少保留"):
            self.db.delete_campaign(self.db.list_campaigns()[0]["id"])


if __name__ == "__main__":
    unittest.main()
