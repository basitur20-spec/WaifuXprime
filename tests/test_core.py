import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
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


if __name__ == "__main__":
    unittest.main()
