import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("OWNER_ID", "8163432539")

from Basit.config import normalize_rarity, rarity_meta, SPAWN_RARITIES
from Basit.services.characters import name_matches
from Basit.migrate_legacy import legacy_bonus_fields
from Basit.handlers.gameplay import WAIFU_RARITY_WEIGHTS, PROPOSE_RARITY_WEIGHTS
from datetime import datetime, timedelta


class CoreHelpersTest(unittest.TestCase):
    def test_legacy_rarity_aliases(self):
        self.assertEqual(normalize_rarity("Common"), "starter")
        self.assertEqual(normalize_rarity("Legendary"), "shiny")

    def test_group_spawn_pool_uses_only_requested_rarities(self):
        self.assertEqual(len(SPAWN_RARITIES), 15)
        self.assertNotIn("limitedarc", SPAWN_RARITIES)
        self.assertNotIn("specialarc", SPAWN_RARITIES)
        self.assertNotIn("divinearc", SPAWN_RARITIES)
        self.assertNotIn("cinemaarc", SPAWN_RARITIES)

    def test_rarity_metadata(self):
        self.assertEqual(rarity_meta("RoyalArc")["name"], "RoyalArc")
        self.assertGreater(rarity_meta("RoyalArc")["weight"], 0)

    def test_hunt_accepts_one_correct_name_word(self):
        self.assertTrue(name_matches("Son Goku", "Goku"))
        self.assertTrue(name_matches("Son Goku", "Son"))
        self.assertFalse(name_matches("Son Goku", "Naruto"))

    def test_legacy_bonus_timestamps_migrate_to_live_cooldowns(self):
        last_daily = 1_700_000_000
        last_weekly = 1_700_000_000
        last_claim = 1_700_000_000
        fields = legacy_bonus_fields({
            "daily": last_daily,
            "weekly": last_weekly,
            "last_claim": last_claim,
            "streak": 7,
            "highest_streak": 14,
        })
        expected_last = datetime.utcfromtimestamp(last_daily)
        self.assertEqual(fields["daily_bonus_next_at"], expected_last + timedelta(days=1))
        self.assertEqual(fields["weekly_bonus_next_at"], expected_last + timedelta(days=7))
        self.assertEqual(fields["last_daily_claim_at"], expected_last)
        self.assertEqual(fields["daily_streak"], 7)
        self.assertEqual(fields["highest_streak"], 14)
        self.assertIn("legacy_bonus", fields)

    def test_reward_specific_rarity_weights_match_specification(self):
        self.assertEqual(
            WAIFU_RARITY_WEIGHTS,
            {"starter": 50, "rising": 50, "shiny": 50, "ultimate": 50, "royalarc": 5},
        )
        self.assertEqual(
            PROPOSE_RARITY_WEIGHTS,
            {"christmasarc": 35, "royalarc": 60, "neonarc": 35, "celestialarc": 55},
        )

    def test_application_registers_all_handlers(self):
        from Basit.main import build_application
        app = build_application()
        handlers = [handler for group in app.handlers.values() for handler in group]
        self.assertGreaterEqual(len(handlers), 35)
        self.assertTrue(any(handler.__class__.__name__ == "InlineQueryHandler" for handler in handlers))


if __name__ == "__main__":
    unittest.main()
