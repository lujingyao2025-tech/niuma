import unittest

from ophelia_assistant.mail_content import render_email


class RenderEmailVariableTests(unittest.TestCase):
    def test_missing_custom_variable_renders_empty(self):
        subject, body = render_email(
            "Alice",
            "Seattle",
            "Anna Lee",
            "Hello {custom_1} in {location}",
            "Hi {first_name}, value={custom_1}",
            {},
        )
        self.assertEqual(subject, "Hello  in Seattle")
        self.assertEqual(body, "Hi Alice, value=")

    def test_unknown_placeholder_reports_exact_token(self):
        with self.assertRaises(ValueError) as ctx:
            render_email(
                "Alice",
                "Seattle",
                "Anna Lee",
                "Hello {first_name}",
                "Hi {frist_name}",
                {},
            )
        self.assertIn("frist_name", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
