import json
from http import HTTPStatus

from django.test import SimpleTestCase


class ReportsTest(SimpleTestCase):
    def post(self, data, content_type="application/reports+json"):
        return self.client.post("/reports", data, content_type=content_type)

    def test_reports(self):
        self.assertEqual(
            self.client.get("/reports").status_code, HTTPStatus.METHOD_NOT_ALLOWED
        )

        with self.assertLogs("busstops.views") as logs:
            response = self.post(
                json.dumps(
                    [
                        {
                            "type": "intervention",
                            "url": "https://bustimes.org/map",
                            "body": {
                                "id": "HeavyAdIntervention",
                                "message": "Ad was removed",
                            },
                        },
                        {"type": "deprecation", "body": {"id": "Foo"}},
                        {
                            "type": "crash",
                            "url": "https://bustimes.org/",
                            "body": {"reason": "oom"},
                        },
                        "nonsense",
                    ]
                )
            )
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(logs.records[0].levelname, "WARNING")
        self.assertIn("HeavyAdIntervention", logs.records[0].getMessage())
        self.assertIn("oom", logs.records[1].getMessage())

        with self.assertLogs("busstops.views") as logs:
            response = self.post(
                json.dumps(
                    {
                        "type": "ad-crash-suspect",
                        "url": "/map#15/52.6/1.3",
                        "body": {"ads": []},
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, HTTPStatus.NO_CONTENT)
        self.assertIn("/map#15/52.6/1.3", logs.records[0].getMessage())

        self.assertEqual(self.post("{").status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.post("3").status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            self.post(json.dumps([{"type": "x" * 70000}])).status_code,
            HTTPStatus.BAD_REQUEST,
        )
