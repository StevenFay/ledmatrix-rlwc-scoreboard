#!/usr/bin/env python3
"""
Tests for the Rugby League World Cup scoreboard.

The plugin is ChuckBuilds' NRL scoreboard with its data source swapped from
ESPN to NRL.com's World Cup draw. These tests cover what changed:

- the static artifacts (manifest, schema, harness) name the RLWC plugin;
- rlwc_feed turns NRL.com fixtures (real 2026 Round 1 and 2 data in
  test/fixtures/rlwc_draw_2026.json) into the ESPN events the shared code reads,
  for every match state, with logos, codes and pool positions;
- RlwcFeed fetches every round once, reuses it, polls only the current round for
  live games, keeps the last data on a failure, and survives a restart through
  the cache;
- end to end, the real managers sort those games into live, recent and upcoming.

Run: python3 test_rlwc_plugin.py   (end-to-end tests skip without a LEDMatrix core)
"""

import copy
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rlwc_feed  # noqa: E402

DRAW = json.loads((HERE / "test" / "fixtures" / "rlwc_draw_2026.json").read_text(encoding="utf-8"))


def _load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def _find_core():
    for c in (os.environ.get("LEDMATRIX_CORE", ""), str(HERE.parents[1] / "LEDMatrix"),
              str(HERE.parent / "LEDMatrix"), str(Path.home() / "LEDMatrix")):
        if c and (Path(c) / "src" / "plugin_system").is_dir():
            return Path(c)
    return None


class FakeResponse:
    def __init__(self, payload, fail=False):
        self.payload, self.fail = payload, fail

    def raise_for_status(self):
        if self.fail:
            raise IOError("HTTP 503")

    def json(self):
        return self.payload


class FakeSession:
    """Answers draw/data requests from the fixture file, round by round."""

    def __init__(self, draw=None):
        self.draw = draw or copy.deepcopy(DRAW)
        self.calls = []
        self.fail = False

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self.fail:
            raise IOError("network down")
        round_id = (params or {}).get("round")
        page = self.draw["round2" if round_id in (None, 2) else f"round{round_id}"] \
            if round_id in (None, 1, 2) else {"fixtures": [], "filterRounds": [],
                                              "selectedRoundId": round_id}
        return FakeResponse(copy.deepcopy(page))


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=300):
        return copy.deepcopy(self.store.get(key))

    def set(self, key, value, ttl=None):
        self.store[key] = copy.deepcopy(value)


def _fixture(round_name, home):
    page = DRAW[round_name]
    return copy.deepcopy(next(f for f in page["fixtures"] if f["homeTeam"]["nickName"] == home))


def _status(event):
    return event["competitions"][0]["status"]


def _side(event, side):
    return next(c for c in event["competitions"][0]["competitors"] if c["homeAway"] == side)


class TestArtifacts(unittest.TestCase):
    def test_manifest(self):
        m = _load("manifest.json")
        self.assertEqual(m["id"], "rlwc-scoreboard")
        self.assertEqual(m["class_name"], "RlwcScoreboardPlugin")
        self.assertEqual(m["entry_point"], "manager.py")
        self.assertEqual(m["display_modes"], ["rlwc_live", "rlwc_recent", "rlwc_upcoming"])
        self.assertEqual(m["version"], m["versions"][0]["version"])
        self.assertIn("rugby-league", m["tags"])

    def test_schema(self):
        s = _load("config_schema.json")
        props = s["properties"]
        for key in ("enabled", "competition", "season", "favorite_teams", "exclude_teams",
                    "display_modes", "live_priority", "dynamic_duration", "background_service"):
            self.assertIn(key, props)
        self.assertEqual(props["competition"]["enum"], ["mens", "womens"])
        self.assertEqual(props["season"]["default"], rlwc_feed.DEFAULT_SEASON)
        # Odds and rankings come from ESPN, which has no World Cup: hidden, off.
        self.assertEqual(props["show_odds"]["x-display"], "hidden")
        self.assertFalse(props["show_odds"]["default"])
        self.assertEqual(props["show_ranking"]["x-display"], "hidden")
        order = s["x-propertyOrder"]
        self.assertEqual(sorted(order), sorted(props))
        blob = json.dumps(s)
        for bad in ("Newcastle Knights", "Show live NRL games", "Premier League"):
            self.assertNotIn(bad, blob)

    def test_harness_seeds_every_state(self):
        h = _load("test/harness.json")
        cached = _load(h["mock_data"])
        feed = rlwc_feed.RlwcFeed(131, 2026, "RLWC")
        self.assertIn(feed.cache_key, cached, "mock must be keyed as RlwcFeed reads it")
        events = [rlwc_feed.to_espn_event(f) for f in cached[feed.cache_key]["fixtures"]]
        states = {_status(e)["type"]["state"] for e in events}
        self.assertEqual(states, {"in", "post", "pre"})
        playing = {c["team"]["abbreviation"] for e in events
                   for c in e["competitions"][0]["competitors"]}
        self.assertTrue(set(h["config"]["favorite_teams"]) <= playing)


class TestTranslation(unittest.TestCase):
    def test_full_time_result(self):
        event = rlwc_feed.to_espn_event(_fixture("round1", "Australia"))
        st = _status(event)["type"]
        self.assertEqual((st["state"], st["name"], st["completed"]), ("post", "STATUS_FINAL", True))
        home, away = _side(event, "home"), _side(event, "away")
        self.assertEqual((home["team"]["abbreviation"], home["score"]), ("AUS", "24"))
        self.assertEqual((away["team"]["abbreviation"], away["score"]), ("NZL", "22"))
        self.assertEqual(home["id"], "500149")
        self.assertEqual(event["date"], "2026-10-15T09:05:00Z")
        self.assertEqual(event["id"], "2026-round-1-australia-v-new-zealand")

    def test_upcoming_has_no_score_and_shows_pool_position(self):
        event = rlwc_feed.to_espn_event(_fixture("round2", "Australia"))
        self.assertEqual(_status(event)["type"]["state"], "pre")
        home = _side(event, "home")
        self.assertEqual(home["score"], "0")
        self.assertEqual(home["records"][0]["summary"], "2nd")
        self.assertIn("Pool A", event["competitions"][0]["notes"][0]["headline"])

    def test_live_states(self):
        fx = _fixture("round2", "Papua New Guinea")
        fx.update(matchMode="Current", matchState="SecondHalf")
        fx["clock"]["gameTime"] = "65:12"
        fx["homeTeam"]["score"], fx["awayTeam"]["score"] = 18, 12
        st = _status(rlwc_feed.to_espn_event(fx))
        self.assertEqual((st["type"]["state"], st["period"], st["displayClock"]), ("in", 2, "65'"))
        self.assertEqual(_side(rlwc_feed.to_espn_event(fx), "home")["score"], "18")

        fx.update(matchState="FirstHalf"); fx["clock"]["gameTime"] = "12:40"
        self.assertEqual(_status(rlwc_feed.to_espn_event(fx))["period"], 1)

        fx.update(matchState="HalfTime"); fx["clock"]["gameTime"] = "40:00"
        st = _status(rlwc_feed.to_espn_event(fx))
        self.assertEqual((st["type"]["state"], st["type"]["name"]), ("in", "STATUS_HALFTIME"))

        fx.update(matchState="GoldenPoint"); fx["clock"]["gameTime"] = "83:05"
        self.assertEqual(_status(rlwc_feed.to_espn_event(fx))["period"], 3)

        # An unrecognised in-play state still reads as live, period from the clock.
        fx.update(matchState="SomethingNew"); fx["clock"]["gameTime"] = "47:00"
        st = _status(rlwc_feed.to_espn_event(fx))
        self.assertEqual((st["type"]["state"], st["period"]), ("in", 2))

    def test_called_off_is_never_a_result(self):
        fx = _fixture("round2", "England")
        fx.update(matchMode="Post", matchState="Postponed")
        st = _status(rlwc_feed.to_espn_event(fx))["type"]
        self.assertEqual((st["state"], st["name"], st["completed"]),
                         ("post", "STATUS_POSTPONED", False))

    def test_logo_urls(self):
        event = rlwc_feed.to_espn_event(_fixture("round1", "England"))
        self.assertEqual(_side(event, "home")["team"]["logo"],
                         "https://www.nrl.com/.theme/england/badge.png?bust=202610191107")
        # Tonga's theme key differs from its name; badge.png is preferred.
        self.assertEqual(_side(event, "away")["team"]["logo"],
                         "https://www.nrl.com/.theme/tonga-new/badge.png?bust=202408291056")
        fx = _fixture("round1", "England")
        fx["homeTeam"]["theme"]["logos"] = {"badge-light.png": "1"}
        self.assertTrue(_side(rlwc_feed.to_espn_event(fx), "home")["team"]["logo"]
                        .endswith("/badge-light.png?bust=1"))
        fx["homeTeam"]["theme"]["logos"] = {"badge.svg": "1"}
        self.assertNotIn("logo", _side(rlwc_feed.to_espn_event(fx), "home")["team"])
        custom = rlwc_feed.to_espn_event(_fixture("round1", "England"),
                                         logo_template="https://x.test/{key}.png")
        self.assertEqual(_side(custom, "home")["team"]["logo"], "https://x.test/england.png")

    def test_abbreviations(self):
        for name, code in (("Papua New Guinea", "PNG"), ("Cook Islands", "COK"),
                           ("New Zealand", "NZL"), ("Lebanon", "LEB"), ("Wales", "WAL"),
                           ("Mystery Island", "MYS"), ("", "TBC")):
            self.assertEqual(rlwc_feed.abbreviation(name), code)

    def test_byes_and_incomplete_rows_are_skipped(self):
        self.assertIsNone(rlwc_feed.to_espn_event({"type": "Bye", "teamName": "Fiji"}))
        fx = _fixture("round2", "Tonga"); fx.pop("clock")
        self.assertIsNone(rlwc_feed.to_espn_event(fx))

    def test_favourites_resolve_to_team_ids(self):
        index = rlwc_feed.team_index()
        self.assertEqual(rlwc_feed.resolve_teams(["AUS", "new zealand", "500213", "aus", ""], index),
                         ["500149", "500150", "500213"])
        self.assertEqual(rlwc_feed.resolve_teams(["Narnia"], index), ["NARNIA"])


class TestFeed(unittest.TestCase):
    def setUp(self):
        self.session, self.cache = FakeSession(), FakeCache()
        self.feed = rlwc_feed.RlwcFeed(131, 2026, "RLWC")
        self.clock = 1_000_000.0
        patcher = mock.patch.object(rlwc_feed.time, "time", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def events(self, live=False):
        return self.feed.events(self.session, live=live, cache_manager=self.cache)

    def test_first_call_reads_every_round_once(self):
        events = self.events()
        self.assertEqual(len(events), 10)
        self.assertEqual(events, sorted(events, key=lambda e: e["date"]))
        asked = [c.get("round") for c in self.session.calls]
        self.assertEqual(asked[0], None)                    # current round first
        self.assertEqual(sorted(asked[1:]), [1, 3, 4, 5])   # then each other round
        self.assertTrue(all(c["competition"] == 131 and c["season"] == 2026
                            for c in self.session.calls))

    def test_reuses_the_draw_and_polls_only_the_current_round_live(self):
        self.events()
        n = len(self.session.calls)
        self.events(); self.events(live=True)
        self.assertEqual(len(self.session.calls), n, "fresh data is reused")
        self.clock += rlwc_feed.LIVE_TTL + 1
        self.events()                                       # recent/upcoming: still fresh
        self.assertEqual(len(self.session.calls), n)
        self.events(live=True)                              # live: one current-round poll
        self.assertEqual(self.session.calls[n:], [{"competition": 131, "season": 2026}])
        self.clock += rlwc_feed.SCHEDULE_TTL + 1
        self.events()
        self.assertEqual(len(self.session.calls), n + 1 + 5, "whole draw re-read")

    def test_live_poll_updates_a_score(self):
        self.events()
        page = self.session.draw["round2"]["fixtures"][1]
        page.update(matchMode="Current", matchState="SecondHalf")
        page["homeTeam"]["score"], page["awayTeam"]["score"] = 18, 12
        self.clock += rlwc_feed.LIVE_TTL + 1
        live = [e for e in self.events(live=True) if _status(e)["type"]["state"] == "in"]
        self.assertEqual(len(live), 1)
        self.assertEqual((_side(live[0], "home")["score"], _side(live[0], "away")["score"]),
                         ("18", "12"))

    def test_a_failure_keeps_the_last_data_and_backs_off(self):
        self.assertEqual(len(self.events()), 10)
        self.session.fail = True
        self.clock += rlwc_feed.SCHEDULE_TTL + 1
        self.assertEqual(len(self.events()), 10)
        n = len(self.session.calls)
        self.events(); self.events(live=True)
        self.assertEqual(len(self.session.calls), n, "no retry on every tick")

    def test_restart_shows_the_cached_draw_without_the_network(self):
        self.events()
        restarted = rlwc_feed.RlwcFeed(131, 2026, "RLWC")
        offline = FakeSession(); offline.fail = True
        events = restarted.events(offline, cache_manager=self.cache)
        self.assertEqual(len(events), 10)
        self.assertEqual(offline.calls, [], "cache is fresh, so nothing is asked")

    def test_shared_instance_per_competition(self):
        a = rlwc_feed.RlwcFeed.shared(131, 2026, "RLWC")
        self.assertIs(a, rlwc_feed.RlwcFeed.shared(131, 2026, "RLWC"))
        self.assertIsNot(a, rlwc_feed.RlwcFeed.shared(157, 2026, "RLWWC"))


CORE = _find_core()


@unittest.skipIf(CORE is None, "no LEDMatrix core checkout (set LEDMATRIX_CORE)")
class TestEndToEnd(unittest.TestCase):
    """The real managers, fed NRL.com data through a fake session."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(CORE))
        import logging
        logging.disable(logging.CRITICAL)
        from src.plugin_system.testing.harness import _instantiate
        from src.plugin_system.testing.loading import (build_full_config, load_harness_spec,
                                                       load_manifest)
        from src.plugin_system.testing.visual_display_manager import VisualTestDisplayManager
        cls._instantiate = staticmethod(_instantiate)
        cls._dm = VisualTestDisplayManager
        cls._config = build_full_config(HERE, load_harness_spec(HERE), {"favorite_teams": ["AUS"]})
        cls._manifest = load_manifest(HERE)

    def test_managers_sort_games_into_live_recent_upcoming(self):
        import tempfile
        draw = copy.deepcopy(DRAW)
        done = draw["round2"]["fixtures"][0]          # Tonga v Lebanon, played the night before
        done.update(matchMode="Post", matchState="FullTime")
        done["clock"]["gameTime"] = "80:00"
        done["homeTeam"]["score"], done["awayTeam"]["score"] = 26, 16
        live = draw["round2"]["fixtures"][1]          # Papua New Guinea v Samoa
        live.update(matchMode="Current", matchState="SecondHalf")
        live["clock"]["gameTime"] = "65:12"
        live["homeTeam"]["score"], live["awayTeam"]["score"] = 18, 12
        session = FakeSession(draw)
        frozen = 1792819800.0                         # 2026-10-24 05:30 UTC
        rlwc_feed.RlwcFeed._instances.clear()
        with mock.patch("time.time", return_value=frozen), tempfile.TemporaryDirectory() as tmp:
            plugin = self._instantiate("rlwc-scoreboard", self._manifest, HERE,
                                       copy.deepcopy(self._config), {},
                                       self._dm(width=128, height=32))
            for mode in ("live", "recent", "upcoming"):
                manager = plugin._get_manager(mode)
                manager.session = session
                manager.logo_dir = Path(tmp)
                manager.feed._fixtures.clear()
                manager.feed._schedule_at = manager.feed._live_at = 0
                manager.feed._loaded_cache = True
                manager.last_update = 0
            for mode in ("live", "recent", "upcoming"):
                plugin._get_manager(mode).update()
            live_games = plugin._get_manager("live").live_games
            recent = plugin._get_manager("recent").games_list
            upcoming = plugin._get_manager("upcoming").games_list

        self.assertEqual([(g["home_abbr"], g["away_abbr"]) for g in live_games], [("PNG", "SAM")])
        game = live_games[0]
        self.assertEqual((game["home_score"], game["away_score"]), ("18", "12"))
        self.assertTrue(game["period_text"].startswith("2H"))
        self.assertTrue(recent and all(g["is_final"] for g in recent))
        self.assertTrue(upcoming and all(g["is_upcoming"] for g in upcoming))
        # Most recent result first; next game first.
        self.assertEqual((recent[0]["home_abbr"], recent[0]["away_abbr"]), ("TON", "LEB"))
        self.assertEqual((upcoming[0]["home_abbr"], upcoming[0]["away_abbr"]), ("ENG", "FRA"))
        # The favourite (AUS) is resolved to its NRL.com id, and its games get a
        # slot of their own on both screens (the NRL plugin's favourites rule).
        self.assertEqual(plugin._get_manager("recent").favorite_teams, ["500149"])
        pairs = lambda games: {(g["home_abbr"], g["away_abbr"]) for g in games}
        self.assertIn(("AUS", "NZL"), pairs(recent))
        self.assertIn(("AUS", "FIJ"), pairs(upcoming))
        self.assertFalse(plugin._get_manager("live").show_odds)
        # Every request went to NRL.com's World Cup draw.
        self.assertTrue(session.calls)
        self.assertTrue(all(c["competition"] == 131 for c in session.calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
