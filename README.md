# RLWC Scoreboard

Live, recent and upcoming **Rugby League World Cup** games on your
[LEDMatrix](https://github.com/ChuckBuilds/LEDMatrix) display.

This is ChuckBuilds' [NRL Scoreboard](https://github.com/ChuckBuilds/ledmatrix-plugins/tree/main/plugins/nrl-scoreboard)
(version 1.42.0) with one change: its data comes from **NRL.com's World Cup
draw** instead of ESPN, because ESPN doesn't carry the World Cup. Everything
else is his plugin: switch and scroll displays, live-game priority, try and win
celebrations, Vegas ticker support and the settings page.

## Install

1. In the LEDMatrix web interface, open **Plugin Manager**.
2. Under **Install from GitHub**, choose **Install Single Plugin** and paste
   `https://github.com/StevenFay/ledmatrix-rlwc-scoreboard`.
3. Accept the unverified-plugin warning, then restart the display.
4. Open **RLWC Scoreboard** in the plugin list, add favourite teams, save.

## Settings

The World Cup settings sit at the top of the plugin's settings page:

| Setting | Default | What it does |
| --- | --- | --- |
| `competition` | `mens` | `mens` for the Rugby League World Cup (NRL.com competition 131), `womens` for the Women's (157). |
| `season` | `2026` | Tournament year. |
| `favorite_teams` | none | Teams to prioritise: a name (`Australia`), a code (`AUS`) or an NRL.com team id. |
| `exclude_teams` | none | Teams never to show (spoiler protection). |
| `logo_size_percent` | `50` | Logo size, as a percentage of the NRL scoreboard's full-height logos. |
| `show_team_names` | on | Country name under each logo (three-letter code if the name doesn't fit). |
| `logo_url_template` | blank | Advanced. Where badges are downloaded from; blank uses NRL.com. |
| `timezone` | LEDMatrix's | Kick-off times are shown in this zone. |

Everything else (display modes, durations, scroll speed, celebrations, colours)
works exactly as in the NRL plugin; its
[README](https://github.com/ChuckBuilds/ledmatrix-plugins/blob/main/plugins/nrl-scoreboard/README.md)
describes each one. Odds and rankings are hidden: both come from ESPN.

Team codes for 2026: Australia `AUS`, New Zealand `NZL`, England `ENG`,
France `FRA`, Samoa `SAM`, Tonga `TON`, Fiji `FIJ`, Papua New Guinea `PNG`,
Lebanon `LEB`, Cook Islands `COK`.

Example `config/config.json` entry:

```json
{
  "rlwc-scoreboard": {
    "enabled": true,
    "competition": "mens",
    "favorite_teams": ["AUS", "PNG"],
    "display_modes": {
      "live": true, "recent": true, "upcoming": true
    }
  }
}
```

## How it works

`rlwc_feed.py` reads `https://www.nrl.com/draw/data?competition=131&season=2026`,
the feed behind NRL.com's [World Cup draw](https://www.nrl.com/draw/?competition=131),
and rewrites each fixture in the shape ESPN's scoreboard API uses. The managers
in `rlwc_managers.py` hand those events to the NRL plugin's shared code, which
never knows the difference.

- **Refreshing:** every round is read when the plugin starts and every 5
  minutes after. While a game is live, the current round is polled every 15
  seconds. One request serves the live, recent and upcoming screens together.
- **Restarts and outages:** the last good draw is kept in LEDMatrix's cache, so
  scores show immediately after a reboot. If NRL.com can't be reached, the last
  data stays on screen and the plugin retries a minute later.
- **Match states:** NRL.com's `Pre`, `Current` and `Post` modes map to
  upcoming, live and final. First half, half-time, second half and golden point
  show as `1H`, `HALF`, `2H` and `ET` with the minute. Postponed or cancelled
  games are never shown as results.
- **Team badges:** downloaded once from
  `https://www.nrl.com/.theme/<team>/badge.png` and cached. If a badge can't be
  downloaded, a grey placeholder with the team code is shown instead.
- **Dates** are day/month (24/10).
- **Pool position** (1st, 2nd ...) shows where the NRL plugin shows a team's
  record, when **Show Records** is on.

NRL.com's draw feed is public but not an official API, so it may change
without notice. If the board goes blank, check the plugin's log first.

## Development

The tests run against a LEDMatrix core checkout:

```bash
LEDMATRIX_CORE=~/LEDMatrix python3 test_rlwc_plugin.py
```

`test/fixtures/rlwc_draw_2026.json` holds real Round 1 and 2 data from NRL.com.
`test/harness.json` and `test/fixtures/mock.json` drive LEDMatrix's safety
harness (`scripts/check_plugin.py`), frozen 65 minutes into Papua New Guinea v
Samoa so the live, recent and upcoming screens all render.

## Credits and licence

Built on [ChuckBuilds' NRL Scoreboard](https://github.com/ChuckBuilds/ledmatrix-plugins/tree/main/plugins/nrl-scoreboard)
and licensed, like it, under the GNU GPL v3 (see `LICENSE`). Match data and
team badges come from NRL.com.
