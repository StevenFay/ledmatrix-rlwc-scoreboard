"""
Rugby League World Cup managers for LEDMatrix.

Adapted from ChuckBuilds' NRL scoreboard (nrl_managers.py). The NRL plugin
reads ESPN; the World Cup is not on ESPN, so these managers read NRL.com's
draw through ``rlwc_feed``, which hands back ESPN-shaped events. Everything
else -- game extraction, live/recent/upcoming selection, rendering -- is the
NRL plugin's code, unchanged.
"""

import logging
from typing import Any, Dict, Optional

from sports import (SportsCore, SportsLive, SportsRecent, SportsUpcoming,
                    _team_logo_filename)
import rlwc_badges
import rlwc_feed

#: Display label per competition key.
LEAGUE_NAMES = {
    "mens": "RLWC",
    "womens": "RLWWC",
}
#: The league key every game carries (used by the scroll and Vegas display).
RLWC_LEAGUE_KEY = "rlwc"

#: Config section ("rlwc_scoreboard") and logo cache directory name.
RLWC_SPORT_KEY = "rlwc"


class BaseRlwcManager(SportsCore):
    """Base class for World Cup managers with common functionality."""

    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60
    _shared_data = None
    _last_shared_update = 0

    def _logo_filename(self, abbr: str, team_id: Any) -> str:
        """``<ABBR>_<id>.png``: the men's and women's teams share codes."""
        return _team_logo_filename(abbr, team_id)

    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        self.logger = logging.getLogger("RLWC")
        super().__init__(
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            logger=self.logger,
            sport_key=RLWC_SPORT_KEY,  # config lives under "rlwc_scoreboard"
        )
        # Kept for the shared code, which names the sport and league; nothing
        # here asks ESPN for them.
        self.sport = "rugby-league"
        self.league = RLWC_LEAGUE_KEY

        competition_key = str(self.mode_config.get("competition", "mens")).lower()
        if competition_key not in rlwc_feed.COMPETITIONS:
            self.logger.warning(
                f"Unknown competition '{competition_key}'; using the men's World Cup")
            competition_key = "mens"
        self.league_key = RLWC_LEAGUE_KEY
        self.league_name = LEAGUE_NAMES[competition_key]
        try:
            season = int(self.mode_config.get("season", rlwc_feed.DEFAULT_SEASON))
        except (TypeError, ValueError):
            season = rlwc_feed.DEFAULT_SEASON
        template = (self.mode_config.get("logo_url_template")
                    or rlwc_feed.DEFAULT_LOGO_URL_TEMPLATE)
        self.feed = rlwc_feed.RlwcFeed.shared(
            rlwc_feed.COMPETITIONS[competition_key], season, self.league_name, template)

        # Odds and rankings come from ESPN by team id, which these teams
        # don't have there, so both are off.
        self.show_odds = False
        self.show_ranking = False

        # Favourites by NRL.com team id ("AUS", "Australia" and "500149" all work).
        index = rlwc_feed.team_index()
        self.favorite_teams = rlwc_feed.resolve_teams(
            self.mode_config.get("favorite_teams", []), index)
        self.exclude_teams = rlwc_feed.resolve_teams(
            self.mode_config.get("exclude_teams", []), index)

        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("rlwc_recent", False)
        self.upcoming_enabled = display_modes.get("rlwc_upcoming", False)
        self.live_enabled = display_modes.get("rlwc_live", False)

        self.logger.info(
            f"Initialized {self.league_name} manager ({season}) with display "
            f"dimensions: {self.display_width}x{self.display_height}; "
            f"favourites {self.favorite_teams}")

    # -- data: NRL.com instead of ESPN ----------------------------------------
    def _fetch_data(self) -> Optional[Dict]:
        """Every World Cup fixture as ESPN events.

        The live manager re-reads the current round every few seconds; the
        recent and upcoming managers share the full draw, refreshed every few
        minutes. All three share one feed object, so one display tick makes at
        most one request.
        """
        events = self.feed.events(
            self.session, headers=self.headers,
            live=isinstance(self, RlwcLiveManager), logger=self.logger,
            cache_manager=self.cache_manager)
        return {"events": events}

    def _fetch_todays_games(self) -> Optional[Dict]:
        return self._fetch_data()

    def _fetch_team_rankings(self) -> Dict[str, int]:
        return {}

    def _league_has_rankings(self) -> bool:
        return False

    def _fetch_odds(self, game: Dict) -> None:
        return None

    def _load_and_resize_logo(self, team_id, team_abbrev, logo_path, logo_url):
        """The team's badge: its logo at ``logo_size_percent`` of the NRL
        layout's size, with the country's name underneath (rlwc_badges)."""
        logo = super()._load_and_resize_logo(team_id, team_abbrev, logo_path, logo_url)
        if logo is None:
            return None
        percent, show_names = rlwc_badges.settings(self.mode_config)
        return rlwc_badges.badge(
            logo, team_id, team_abbrev, self.display_height,
            (getattr(self, "fonts", None) or {}).get("detail"), percent, show_names,
            max_width=max(16, self.display_width // 3))

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Game details, with rugby league halves as in the NRL plugin."""
        details, home_team, away_team, status, situation = self._extract_game_details_common(game_event)
        if details is None or home_team is None or away_team is None or status is None:
            return None
        for competitor in (home_team, away_team):
            team = competitor.get("team") or {}
            rlwc_badges.remember(competitor.get("id") or team.get("id"),
                                 team.get("displayName") or team.get("name"))
        try:
            period = status.get("period", 0)
            status_state = status["type"]["state"]
            status_name = status["type"]["name"]
            is_halftime = status_state == "halftime" or status_name == "STATUS_HALFTIME"
            if is_halftime:
                period_text = "HALF"
            elif status_state == "in":
                period_text = {0: "Start", 1: "1H", 2: "2H"}.get(
                    period, "ET" if period >= 3 else f"P{period}")
            elif status_state == "post":
                period_text = ("Final" if details.get("is_final")
                               else status["type"].get("shortDetail") or "")
            else:
                period_text = details.get("game_time", "")

            clock = status.get("displayClock", "")
            if clock and status_state == "in" and not is_halftime:
                period_text = f"{period_text} {clock}" if period_text else clock

            details.update({
                "period": period,
                "period_text": period_text,
                "clock": clock,
                "league": self.league_key,
            })
            # Day/month, as written in Australia and the other host and
            # competing nations (the shared code writes month/day).
            start = details.get("start_time_utc")
            if start:
                local = start.astimezone(self._get_timezone())
                details["game_date"] = f"{local.day}/{local.month}"
            if not details["home_abbr"] or not details["away_abbr"]:
                self.logger.warning(f"Missing team abbreviation in event: {details['id']}")
                return None
            return details
        except Exception as e:
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True)
            return None


class RlwcLiveManager(BaseRlwcManager, SportsLive):
    """Manager for live World Cup games."""

    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("RLWCLive")


class RlwcRecentManager(BaseRlwcManager, SportsRecent):
    """Manager for recently completed World Cup games."""

    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("RLWCRecent")


class RlwcUpcomingManager(BaseRlwcManager, SportsUpcoming):
    """Manager for upcoming World Cup games."""

    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger("RLWCUpcoming")


def create_rlwc_managers(config, display_manager, cache_manager):
    """(RlwcLiveManager, RlwcRecentManager, RlwcUpcomingManager)."""
    return (
        RlwcLiveManager(config, display_manager, cache_manager),
        RlwcRecentManager(config, display_manager, cache_manager),
        RlwcUpcomingManager(config, display_manager, cache_manager),
    )
