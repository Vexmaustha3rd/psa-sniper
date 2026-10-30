"""Offline tests: fake eBay responses, no network. Run: python -m unittest test_sniper"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sniper

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
CFG = json.loads((Path(__file__).parent / "config.json").read_text())


def item(iid, title, price, ship=4.0, auction=False, bids=0, ends_in_min=None, fb=500, pct="99.8"):
    d = {
        "itemId": iid, "title": title, "itemWebUrl": f"https://www.ebay.com/itm/{iid}",
        "buyingOptions": ["AUCTION"] if auction else ["FIXED_PRICE"],
        "price": {"value": str(price), "currency": "USD"},
        "shippingOptions": [{"shippingCost": {"value": str(ship), "currency": "USD"}}],
        "seller": {"username": f"seller{iid}", "feedbackScore": fb, "feedbackPercentage": pct},
        "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
    }
    if auction:
        d["currentBidPrice"] = {"value": str(price), "currency": "USD"}
        d["bidCount"] = bids
    if ends_in_min is not None:
        d["itemEndDate"] = (NOW + timedelta(minutes=ends_in_min)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return d


class FakeEbay(sniper.Ebay):
    """Returns canned listings; comp searches (FIXED_PRICE with a specific card query) get comps."""
    def __init__(self, auctions, bins, comps_by_word):
        super().__init__("id", "secret")
        self.auctions, self.bins, self.comps_by_word = auctions, bins, comps_by_word

    def search(self, q, category, auction, max_price, end_before=None, limit=100):
        self.calls += 1
        if q in [s["q"] for s in CFG["searches"]]:
            return self.auctions if auction else self.bins
        for word, rows in self.comps_by_word.items():
            if word.lower() in q.lower():
                return rows
        return []


def charizard_comps():
    t = "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10"
    return [item(f"c{i}", t, p) for i, p in enumerate([60, 62, 65, 70, 72, 75, 80])]


class TitleRules(unittest.TestCase):
    def test_grade(self):
        self.assertEqual(sniper.parse_grade("Charizard PSA 10 GEM MINT"), "10")
        self.assertEqual(sniper.parse_grade("1952 Topps Mantle PSA 1 POOR"), "1")
        self.assertIsNone(sniper.parse_grade("Pikachu PSA 9 MINT"))
        self.assertIsNone(sniper.parse_grade("Pikachu PSA 1.5"))
        self.assertIsNone(sniper.parse_grade("PSA 10 and PSA 9 Pikachu"))

    def test_exclusions(self):
        for t in ["Lot of 5 PSA 10 Pokemon", "BGS 10 Charizard", "Pick your card PSA 10",
                  "PSA Ready Charizard 10?", "Pokemon booster box PSA 10 slab"]:
            self.assertTrue(sniper.is_excluded(t), t)
        self.assertFalse(sniper.is_excluded("2023 Pokemon 151 Charizard ex #199 PSA 10"))

    def test_grail(self):
        r = CFG["grail_rules"]
        g, why = sniper.grail_check("1999 Pokemon Base Set 1st Edition Charizard Holo #4/102 PSA 1", r)
        self.assertTrue(g); self.assertIn("1st edition", why)
        g, why = sniper.grail_check("2023 Prizm Silver Rookie RC 12/25 PSA 10", r, sports=True)
        self.assertTrue(g); self.assertIn("/25 serial", why); self.assertIn("key rookie", why)
        g, _ = sniper.grail_check("2023 Pokemon Paldea Evolved Tinkaton 58/64 PSA 10", r)
        self.assertFalse(g)  # TCG set number, not a serial
        g, _ = sniper.grail_check("2024 Topps Chrome Random Player Base PSA 10", r, sports=True)
        self.assertFalse(g)
        g, why = sniper.grail_check("2023 Pokemon 151 Charizard ex Special Illustration Rare PSA 10", r)
        self.assertTrue(g)

    def test_card_numbers(self):
        self.assertEqual(sniper.card_numbers("Charizard #199 PSA 10"), {"199"})
        self.assertEqual(sniper.card_numbers("Charizard 4/102 PSA 10"), {"4"})
        self.assertEqual(sniper.card_numbers("Prizm 12/25 PSA 10", sports=True), set())


class Scan(unittest.TestCase):
    def run_scan(self, auctions, bins, comps_by_word=None, state=None):
        ebay = FakeEbay(auctions, bins, comps_by_word or {})
        comps = sniper.Comps(ebay, CFG, {}, None)
        state = state if state is not None else {}
        return sniper.scan(CFG, ebay, comps, state, NOW), state

    def test_auction_ending_in_10_min_is_alerted(self):
        a = item("a1", "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10",
                 30, ship=4, auction=True, bids=3, ends_in_min=10)
        deals, _ = self.run_scan([a], [], {"Charizard": charizard_comps()})
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertTrue(d["final"])
        self.assertTrue(d["pg"])                  # under $100 + grail -> PG tag
        self.assertGreaterEqual(d["discount"], 0.30)
        # comp = median of lower-half asks (64,66,69,74 -> 67.5); ceiling = 0.7*67.5 - 4
        self.assertAlmostEqual(d["comp"].value, 67.5)
        self.assertAlmostEqual(d["ceiling"], round(0.7 * 67.5 - 4, 2))

    def test_not_cheap_enough_is_skipped(self):
        a = item("a2", "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10",
                 55, auction=True, bids=8, ends_in_min=45)
        deals, _ = self.run_scan([a], [], {"Charizard": charizard_comps()})
        self.assertEqual(deals, [])

    def test_auction_outside_window_skipped(self):
        a = item("a3", "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10",
                 20, auction=True, ends_in_min=60 * 30)
        deals, _ = self.run_scan([a], [], {"Charizard": charizard_comps()})
        self.assertEqual(deals, [])

    def test_bad_seller_skipped(self):
        b = item("b1", "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10",
                 25, fb=3)
        deals, _ = self.run_scan([], [b], {"Charizard": charizard_comps()})
        self.assertEqual(deals, [])

    def test_non_grail_over_100_skipped(self):
        t = "2024 Topps Chrome Random Player Base #55 PSA 10"
        b = item("b2", t, 150)
        comps = [item(f"x{i}", t, p) for i, p in enumerate([400, 420, 450, 480, 500])]
        deals, _ = self.run_scan([], [b], {"Random": comps})
        self.assertEqual(deals, [])

    def test_grail_tier_100_to_500(self):
        t = "1999 Pokemon Base Set 1st Edition Machamp Holo #8/102 PSA 10"
        b = item("b3", t, 180)
        comps = [item(f"m{i}", t, p) for i, p in enumerate([350, 360, 380, 400, 420, 450])]
        deals, _ = self.run_scan([], [b], {"Machamp": comps})
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["tier"], "grail")
        self.assertFalse(deals[0]["pg"])

    def test_dedupe_and_final_call(self):
        t = "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10"
        comps = {"Charizard": charizard_comps()}
        state = {"alerted": {}}
        a = item("a4", t, 25, auction=True, bids=1, ends_in_min=120)
        deals, state = self.run_scan([a], [], comps, state)
        self.assertEqual(len(deals), 1); self.assertFalse(deals[0]["final"])
        for d in deals:
            state["alerted"][d["key"]] = 1e12
            state["alerted"][f'{d["listing"].item_id}|found'] = 1e12
        deals, state = self.run_scan([a], [], comps, state)
        self.assertEqual(deals, [])               # no repeat
        a_late = item("a4", t, 28, auction=True, bids=2, ends_in_min=12)
        deals, _ = self.run_scan([a_late], [], comps, state)
        self.assertEqual(len(deals), 1); self.assertTrue(deals[0]["final"])  # one final-call alert

    def test_mismatched_card_numbers_not_used_as_comps(self):
        t = "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10"
        wrong = [item(f"w{i}", "2023 Pokemon 151 Charizard ex #006 Holo PSA 10", 300) for i in range(6)]
        b = item("b4", t, 50)
        deals, _ = self.run_scan([], [b], {"Charizard": wrong})
        self.assertEqual(deals, [])

    def test_discord_payload(self):
        a = item("a5", "2023 Pokemon 151 Charizard ex #199 Special Illustration Rare PSA 10",
                 30, auction=True, bids=3, ends_in_min=10)
        deals, _ = self.run_scan([a], [], {"Charizard": charizard_comps()})
        e = sniper.embed_for(deals[0], NOW)
        self.assertIn("FINAL CALL", e["title"])
        self.assertIn("PG", e["title"])
        self.assertIn("bid up to", e["description"])
        self.assertIn("11:10 AM PT", e["description"])

    def test_sample_deal_payload(self):
        e = sniper.embed_for(sniper.sample_deal(NOW), NOW)
        self.assertIn("TEST ALERT", e["title"])
        self.assertIn("bid up to", e["description"])
        self.assertIn("11:20 AM PT", e["description"])


class Main(unittest.TestCase):
    def test_missing_ebay_keys_skips_quietly(self):
        import os
        from unittest import mock
        env = {"DISCORD_WEBHOOK_URL": "https://example.invalid/hook"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sniper, "scan", side_effect=AssertionError("should not scan")):
            self.assertEqual(sniper.main(), 0)

    def test_no_secrets_at_all_skips_quietly(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(sniper, "scan", side_effect=AssertionError("should not scan")):
            self.assertEqual(sniper.main(), 0)

    def test_ebay_keys_without_webhook_is_an_error(self):
        import os
        from unittest import mock
        env = {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sniper, "scan", side_effect=AssertionError("should not scan")):
            self.assertEqual(sniper.main(), 2)

    def run_backup(self, last_scan_age):
        import os
        import tempfile
        import time
        from unittest import mock
        env = {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret", "DRY_RUN": "1",
               "BACKUP_RUN": "1"}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text(json.dumps({"last_scan": time.time() - last_scan_age}))
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(sniper, "STATE_PATH", path), \
                    mock.patch.object(sniper, "scan", return_value=[]) as scan:
                self.assertEqual(sniper.main(), 0)
            return scan.called, json.loads(path.read_text())["last_scan"]

    def test_backup_run_skips_after_recent_scan(self):
        scanned, _ = self.run_backup(5 * 60)
        self.assertFalse(scanned)

    def test_backup_run_scans_when_timer_is_quiet(self):
        import time
        scanned, last = self.run_backup(45 * 60)
        self.assertTrue(scanned)
        self.assertAlmostEqual(last, time.time(), delta=5)

    def test_discord_test_without_webhook_is_an_error(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"DISCORD_TEST": "1"}, clear=True):
            self.assertEqual(sniper.main(), 2)


if __name__ == "__main__":
    unittest.main()
