from __future__ import annotations

import unittest
from pathlib import Path


class WorkerHealthcheckTests(unittest.TestCase):
    def test_compose_injects_required_secrets_without_physical_env_file(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text()
        self.assertNotIn("env_file:", compose)
        self.assertIn("SECRET_KEY: \"${SECRET_KEY:?SECRET_KEY is required}\"", compose)
        self.assertIn(
            "ENCRYPTION_KEY: \"${ENCRYPTION_KEY:?ENCRYPTION_KEY is required}\"",
            compose,
        )
        self.assertIn(
            "ADMIN_PASSWORD_HASH: \"${ADMIN_PASSWORD_HASH:?ADMIN_PASSWORD_HASH is required}\"",
            compose,
        )

    def test_non_web_services_disable_image_http_healthcheck(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text()

        scheduler = compose.split("  scheduler:\n", 1)[1].split("\n  inventory-worker:\n", 1)[0]
        worker = compose.split("  inventory-worker:\n", 1)[1].split("\nvolumes:\n", 1)[0]

        for service in (scheduler, worker):
            self.assertIn("healthcheck:\n      disable: true", service)
            self.assertNotIn("http://127.0.0.1:8080/healthz", service)


if __name__ == "__main__":
    unittest.main()
