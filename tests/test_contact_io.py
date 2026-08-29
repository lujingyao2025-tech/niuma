import tempfile
import unittest
from pathlib import Path

from ophelia_assistant.studio.contact_io import (
    parse_contacts_file,
    write_import_template,
)


class ContactIOTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _write_csv(self, rows: list[list[str]]) -> Path:
        path = self.root / "contacts.csv"
        path.write_text("\n".join(",".join(row) for row in rows), encoding="utf-8-sig")
        return path

    def test_csv_parse_with_custom_variables(self) -> None:
        path = self._write_csv(
            [
                ["名字", "地区", "邮箱", "custom_1"],
                ["Alex Walker", "Seattle", "alex@example.com", "Electronics"],
            ]
        )
        entries = parse_contacts_file(str(path))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "Alex Walker")
        self.assertEqual(entries[0]["custom_1"], "Electronics")

    def test_xlsx_parse(self) -> None:
        import openpyxl

        path = self.root / "contacts.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["名字", "地区", "邮箱"])
        sheet.append(["Mia Chen", "Bellevue", "mia@example.com"])
        workbook.save(path)
        entries = parse_contacts_file(str(path))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["email"], "mia@example.com")

    def test_template_example_row_is_skipped(self) -> None:
        path = self.root / "template.xlsx"
        write_import_template(str(path))
        entries = parse_contacts_file(str(path))
        self.assertEqual(entries, [])

    def test_missing_headers_raise(self) -> None:
        path = self._write_csv([["a", "b"], ["1", "2"]])
        with self.assertRaisesRegex(ValueError, "表头"):
            parse_contacts_file(str(path))

    def test_unsupported_extension_raises(self) -> None:
        path = self.root / "contacts.txt"
        path.write_text("a,b\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "xlsx"):
            parse_contacts_file(str(path))


if __name__ == "__main__":
    unittest.main()
