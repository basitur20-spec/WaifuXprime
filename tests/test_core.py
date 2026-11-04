import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("OWNER_ID", "8163432539")

from Basit.config import normalize_rarity, rarity_meta
from Basit.services.characters import name_matches


class CoreHelpersTest(unittest.TestCase):
    def test_legacy_rarity_aliases(self):
        self.assertEqual(normalize_rarity("Common"), "starter")
        self.assertEqual(normalize_rarity("Legendary"), "shiny")

    def test_rarity_metadata(self):
        self.assertEqual(rarity_meta("RoyalArc")["name"], "RoyalArc")
        self.assertGreater(rarity_meta("RoyalArc")["weight"], 0)

    def test_hunt_accepts_one_correct_name_word(self):
        self.assertTrue(name_matches("Son Goku", "Goku"))
        self.assertTrue(name_matches("Son Goku", "Son"))
        self.assertFalse(name_matches("Son Goku", "Naruto"))

    def test_application_registers_all_handlers(self):
        from Basit.main import build_application
        app = build_application()
        handlers = [handler for group in app.handlers.values() for handler in group]
        self.assertGreaterEqual(len(handlers), 35)
        self.assertTrue(any(handler.__class__.__name__ == "InlineQueryHandler" for handler in handlers))


if __name__ == "__main__":
    unittest.main()
