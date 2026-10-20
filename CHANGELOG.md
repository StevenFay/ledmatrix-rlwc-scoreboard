# Changelog

## 1.1.0 (2026-10-20)

- Team logos are half their former size, with the country's name underneath
  (its three-letter code where the full name doesn't fit). Applies to the
  switch screens and the scroll / Vegas cards.
- New settings: `logo_size_percent` (default 50) and `show_team_names` (default on).

## 1.0.0 (2026-10-20)

First release, adapted from ChuckBuilds' nrl-scoreboard 1.42.0
(ledmatrix-plugins commit a6a65d5).

- Data comes from NRL.com's Rugby League World Cup draw (`rlwc_feed.py`),
  translated into ESPN's event shape, instead of ESPN, which doesn't carry the
  World Cup. Men's (competition 131) or women's (157) tournament.
- New settings: `competition`, `season`, `logo_url_template`.
- Favourites accept a team name, three-letter code or NRL.com team id.
- Last good draw kept in LEDMatrix's cache, so scores show straight after a restart.
- Dates shown day/month.
- Odds and rankings hidden and turned off (ESPN only).
- Plugin id `rlwc-scoreboard`; display modes `rlwc_live`, `rlwc_recent`, `rlwc_upcoming`.
