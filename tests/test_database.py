import os
import unittest
from unittest.mock import patch

from core.database import get_database_url


class DatabaseConfigTests(unittest.TestCase):
    def test_defaults_to_sqlite_when_no_postgres_config_is_available(self):
        with patch.dict(os.environ, {}, clear=True):
            url = get_database_url()
        self.assertTrue(url.startswith("sqlite+aiosqlite:///"))


if __name__ == "__main__":
    unittest.main()
