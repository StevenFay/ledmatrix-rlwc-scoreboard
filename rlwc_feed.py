"""
Rugby League World Cup data for the RLWC scoreboard.

ESPN does not carry the World Cup, so this module reads NRL.com's draw feed
(the data behind https://www.nrl.com/draw/?competition=131) and rewrites each
fixture as an ESPN-shaped scoreboard event. Everything downstream -- the
shared sports managers, renderers, scroll and Vegas modes copied from the
NRL scoreboard -- then works unchanged, because it only ever sees ESPN's
shape.

The module name is plugin-unique (``rlwc_feed``) so it can never bind another
plugin's module (see the LEDMatrix plugin rules on module collisions).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

DRAW_URL = "https://www.nrl.com/draw/data"
SITE = "https://www.nrl.com"

#: NRL.com competition ids.
COMPETITIONS = {
    "mens": 131,     # Rugby League World Cup
    "womens": 157,   # Women's Rugby League World Cup
}
DEFAULT_SEASON = 2026

#: Where NRL.com serves a team's badge: ``theme.key`` plus the file name, with
#: the version stamp NRL.com gives for that file as a cache-buster.
DEFAULT_LOGO_URL_TEMPLATE = SITE + "/.theme/{key}/{file}?bust={version}"
#: Badge files in order of preference. ``badge.png`` is drawn for dark
#: backgrounds, which an LED panel is.
LOGO_FILES = ("badge.png", "badge-light.png")

#: Three-letter codes for the 2026 teams. NRL.com gives only full names;
#: anything not listed falls back to the first three letters.
TEAM_ABBREVIATIONS = {
    "australia": "AUS",
    "new zealand": "NZL",
    "england": "ENG",
    "france": "FRA",
    "samoa": "SAM",
    "tonga": "TON",
    "fiji": "FIJ",
    "papua new guinea": "PNG",
    "lebanon": "LEB",
    "cook islands": "COK",
    "wales": "WAL",
    "ireland": "IRE",
    "scotland": "SCO",
    "italy": "ITA",
    "jamaica": "JAM",
    "greece": "GRE",
    "brazil": "BRA",
    "canada": "CAN",
    "netherlands": "NED",
}

#: Seconds a schedule (every round) or live poll is reused before asking again.
SCHEDULE_TTL = 300
LIVE_TTL = 15

#: matchState values that mean the game was not played to a result.
_CALLED_OFF = {
    "postponed": "STATUS_POSTPONED",
    "cancelled": "STATUS_CANCELED",
    "canceled": "STATUS_CANCELED",
    "abandoned": "STATUS_ABANDONED",
    "suspended": "STATUS_SUSPENDED",
}

_log = logging.getLogger("RLWC.feed")


def abbreviation(name: str) -> str:
    """Three-letter code for a team name ("Papua New Guinea" -> "PNG")."""
    key = (name or "").strip().lower()
    if key in TEAM_ABBREVIATIONS:
        return TEAM_ABBREVIATIONS[key]
    letters = re.sub(r"[^A-Za-z]", "", name or "")
    return (letters[:3] or "TBC").upper()


def logo_url(team: Dict[str, Any], template: str = DEFAULT_LOGO_URL_TEMPLATE) -> Optional[str]:
    """The badge URL for an NRL.com team, or None when it lists no badge."""
    theme = team.get("theme") or {}
    key, logos = theme.get("key"), theme.get("logos") or {}
    if not key or not isinstance(logos, dict):
        return None
    for file in LOGO_FILES:
        if file in logos:
            return template.format(key=key, file=file, version=logos[file])
    return None


def _minutes(game_time: Any) -> Optional[int]:
    """Whole minutes from NRL.com's "52:13" game clock."""
    match = re.match(r"^\s*(\d+)", str(game_time or ""))
    return int(match.group(1)) if match else None


def _status(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """ESPN ``status`` for a fixture's matchMode / matchState / clock."""
    mode = str(fixture.get("matchMode") or "").lower()
    state = str(fixture.get("matchState") or "")
    state_l = re.sub(r"[^a-z]", "", state.lower())
    clock = fixture.get("clock") or {}
    minutes = _minutes(clock.get("gameTime"))

    def status(name, espn_state, short, completed=False, period=0, display_clock=""):
        return {
            "clock": float((minutes or 0) * 60),
            "displayClock": display_clock,
            "period": period,
            "type": {
                "name": name,
                "state": espn_state,
                "completed": completed,
                "description": short if espn_state != "pre" else (state or short),
                "detail": short,
                "shortDetail": short,
            },
        }

    for word, name in _CALLED_OFF.items():
        if word in state_l:
            return status(name, "post", state or word.title())
    if state_l in ("fulltime", "final", "finished") or mode == "post":
        return status("STATUS_FINAL", "post", "Final", completed=True, period=2,
                      display_clock=f"{minutes}'" if minutes else "")
    if mode == "pre" or state_l in ("upcoming", "scheduled", "prematch"):
        return status("STATUS_SCHEDULED", "pre", "Scheduled")
    # Anything else is a game in progress ("Current" mode).
    if "half" in state_l and "time" in state_l:          # HalfTime
        return status("STATUS_HALFTIME", "in", "HALF", period=1)
    if "extra" in state_l or "golden" in state_l:
        period = 3
    elif "second" in state_l:
        period = 2
    elif "first" in state_l:
        period = 1
    elif minutes is not None:
        period = 1 if minutes < 40 else 2 if minutes < 80 else 3
    else:
        period = 1
    shown = f"{minutes}'" if minutes is not None else ""
    return status("STATUS_IN_PROGRESS", "in", shown or "LIVE", period=period,
                  display_clock=shown)


def to_espn_event(fixture: Dict[str, Any], label: str = "RLWC",
                  logo_template: str = DEFAULT_LOGO_URL_TEMPLATE) -> Optional[Dict[str, Any]]:
    """One NRL.com fixture as an ESPN scoreboard event, or None if it is not a match."""
    if fixture.get("type", "Match") != "Match":
        return None
    home, away = fixture.get("homeTeam") or {}, fixture.get("awayTeam") or {}
    kickoff = (fixture.get("clock") or {}).get("kickOffTimeLong")
    if not home or not away or not kickoff:
        return None
    status = _status(fixture)
    played = status["type"]["state"] != "pre"

    def competitor(team, side):
        name = team.get("nickName") or team.get("name") or "TBC"
        team_id = str(team.get("teamId") or abbreviation(name))
        entry = {
            "id": team_id,
            "homeAway": side,
            "score": str(team.get("score", 0) if played else 0),
            "team": {
                "id": team_id,
                "abbreviation": abbreviation(name),
                "name": name,
                "displayName": name,
                "shortDisplayName": name,
                "location": name,
            },
        }
        url = logo_url(team, logo_template)
        if url:
            entry["team"]["logo"] = url
        if team.get("teamPosition"):
            # Pool position, shown where ESPN would show a win-loss record.
            entry["records"] = [{"type": "total", "summary": str(team["teamPosition"])}]
        return entry

    slug = str(fixture.get("matchCentreUrl") or "").strip("/")
    # "/draw/rugby-league-world-cup/2026/round-1/australia-v-new-zealand/"
    # -> "2026-round-1-australia-v-new-zealand": stable, readable, no slashes.
    parts = [p for p in slug.split("/") if p]
    event_id = ("-".join(parts[-3:]) if len(parts) >= 3
                else f"{kickoff}-{home.get('teamId')}-{away.get('teamId')}")
    home_c, away_c = competitor(home, "home"), competitor(away, "away")
    notes = [n for n in (fixture.get("roundTitle"), fixture.get("groupName")) if n]
    competition = {
        "id": event_id,
        "date": kickoff,
        "status": status,
        "competitors": [home_c, away_c],
        "venue": {"fullName": fixture.get("venue") or "",
                  "address": {"city": fixture.get("venueCity") or ""}},
        "notes": [{"type": "event", "headline": " - ".join(notes)}] if notes else [],
    }
    return {
        "id": event_id,
        "uid": f"rlwc:{event_id}",
        "date": kickoff,
        "name": f"{away_c['team']['name']} at {home_c['team']['name']}",
        "shortName": f"{away_c['team']['abbreviation']} @ {home_c['team']['abbreviation']}",
        "league": label,
        "season": {"slug": fixture.get("roundTitle") or ""},
        "links": [{"href": SITE + "/" + slug + "/"}] if slug else [],
        "competitions": [competition],
        "status": status,
    }


class RlwcFeed:
    """Fetches and caches the draw for one competition and season.

    One instance is shared by the Live, Recent and Upcoming managers (see
    ``shared``), so a display tick asks NRL.com once, not three times. A failed
    request keeps the last good answer, so a network blip never empties the
    screen.
    """

    _instances: Dict[Tuple[int, int, str], "RlwcFeed"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, competition: int, season: int, label: str,
                 logo_template: str = DEFAULT_LOGO_URL_TEMPLATE):
        self.competition, self.season, self.label = competition, season, label
        self.logo_template = logo_template
        self._lock = threading.Lock()
        self._fixtures: Dict[str, Dict[str, Any]] = {}   # event id -> fixture
        self._schedule_at = 0.0
        self._live_at = 0.0
        self._loaded_cache = False

    @classmethod
    def shared(cls, competition: int, season: int, label: str,
               logo_template: str = DEFAULT_LOGO_URL_TEMPLATE) -> "RlwcFeed":
        key = (int(competition), int(season), logo_template)
        with cls._instances_lock:
            feed = cls._instances.get(key)
            if feed is None:
                feed = cls._instances[key] = cls(competition, season, label, logo_template)
            feed.label = label
            return feed

    # -- network -----------------------------------------------------------
    def _get(self, session, headers, round_id: Optional[int] = None) -> Dict[str, Any]:
        params = {"competition": self.competition, "season": self.season}
        if round_id is not None:
            params["round"] = round_id
        response = session.get(DRAW_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected NRL.com draw response")
        return data

    def _store(self, fixtures: Iterable[Dict[str, Any]]) -> None:
        for fixture in fixtures or []:
            if not isinstance(fixture, dict):
                continue
            event = to_espn_event(fixture, self.label, self.logo_template)
            if event:
                self._fixtures[event["id"]] = fixture

    def _refresh_schedule(self, session, headers) -> None:
        current = self._get(session, headers)
        self._store(current.get("fixtures"))
        current_round = current.get("selectedRoundId")
        for entry in current.get("filterRounds") or []:
            round_id = entry.get("value") if isinstance(entry, dict) else None
            if round_id is None or round_id == current_round:
                continue
            try:
                self._store(self._get(session, headers, round_id).get("fixtures"))
            except Exception as e:  # one round failing keeps the others
                _log.warning("RLWC round %s fetch failed: %s", round_id, e)

    @property
    def cache_key(self) -> str:
        """LEDMatrix cache key for this competition's fixtures."""
        return f"rlwc_draw_{self.competition}_{self.season}"

    def _load_cached(self, cache_manager) -> None:
        """Seed from LEDMatrix's cache (last good fetch, any age) on first use,
        so the last scores show straight after a restart."""
        if self._loaded_cache or cache_manager is None:
            return
        self._loaded_cache = True
        try:
            cached = cache_manager.get(self.cache_key, max_age=None)
        except TypeError:   # an older cache API without max_age=None
            cached = cache_manager.get(self.cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict) and isinstance(cached.get("fixtures"), list):
            self._store(cached["fixtures"])
            fetched_at = float(cached.get("fetched_at") or 0)
            self._schedule_at = self._live_at = fetched_at

    def _save_cached(self, cache_manager, now: float) -> None:
        if cache_manager is None:
            return
        try:
            cache_manager.set(self.cache_key, {
                "fixtures": list(self._fixtures.values()), "fetched_at": now})
        except Exception as e:
            _log.debug("Could not cache the RLWC draw: %s", e)

    def events(self, session, headers=None, live: bool = False,
               logger: Optional[logging.Logger] = None,
               cache_manager=None) -> List[Dict[str, Any]]:
        """Every known fixture as ESPN events.

        ``live=True`` re-asks for the current round (where live games are)
        every ``LIVE_TTL`` seconds; otherwise every round is re-read every
        ``SCHEDULE_TTL`` seconds.
        """
        log = logger or _log
        now = time.time()
        with self._lock:
            self._load_cached(cache_manager)
            try:
                if not self._fixtures or now - self._schedule_at > SCHEDULE_TTL:
                    self._refresh_schedule(session, headers)
                    self._schedule_at = self._live_at = now
                    self._save_cached(cache_manager, now)
                elif live and now - self._live_at > LIVE_TTL:
                    self._store(self._get(session, headers).get("fixtures"))
                    self._live_at = now
                    self._save_cached(cache_manager, now)
            except Exception as e:
                log.warning("RLWC draw fetch failed (%s); showing the last data", e)
                # Back off rather than asking again on every tick.
                self._live_at = now
                self._schedule_at = now - SCHEDULE_TTL + 60
            fixtures = list(self._fixtures.values())
        events = [to_espn_event(f, self.label, self.logo_template) for f in fixtures]
        events = [e for e in events if e]
        events.sort(key=lambda e: e["date"])
        return events


def resolve_teams(teams: Iterable[Any], known: Dict[str, str]) -> List[str]:
    """Favourite/exclude entries ("Australia", "AUS", "500149") as team ids.

    ``known`` maps lower-case name and abbreviation to team id. Unknown
    entries are kept as written, upper-cased, so they still match a team id
    the feed may add later.
    """
    resolved = []
    for team in teams or []:
        raw = str(team).strip()
        if not raw:
            continue
        resolved.append(known.get(raw.lower(), raw.upper() if not raw.isdigit() else raw))
    return list(dict.fromkeys(resolved))


#: The 2026 men's teams' NRL.com ids, so favourites resolve before the feed
#: has loaded.
KNOWN_TEAM_IDS = {
    "500149": "Australia", "500150": "New Zealand", "500159": "Samoa",
    "500319": "France", "500213": "Papua New Guinea", "500341": "Lebanon",
    "500201": "England", "500214": "Tonga", "500158": "Fiji",
    "500340": "Cook Islands",
}


def team_index(events: Iterable[Dict[str, Any]] = ()) -> Dict[str, str]:
    """Lower-case name / abbreviation / id -> team id, from the known teams and
    any teams seen in ``events``."""
    index: Dict[str, str] = {}

    def add(team_id, name):
        index[str(team_id).lower()] = str(team_id)
        index[name.lower()] = str(team_id)
        index[abbreviation(name).lower()] = str(team_id)

    for team_id, name in KNOWN_TEAM_IDS.items():
        add(team_id, name)
    for event in events:
        for c in event["competitions"][0]["competitors"]:
            add(c["team"]["id"], c["team"]["name"])
    return index
