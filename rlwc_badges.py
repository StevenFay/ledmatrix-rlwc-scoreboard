"""
Team badges for the RLWC scoreboard: the team logo, shrunk, with the
country's name underneath.

The NRL plugin's layouts size each logo to the panel and pin it to the edge.
Rather than rewrite those layouts, the plugin hands them a finished badge
(logo plus name, no taller than the panel) wherever they ask for a logo, so
the switch screens and the scroll / Vegas cards all show the same thing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageDraw

import rlwc_feed

#: Team id -> display name, filled in as games are read (see rlwc_managers)
#: so both renderers can name a team from the id they are given.
TEAM_NAMES: Dict[str, str] = dict(rlwc_feed.KNOWN_TEAM_IDS)

#: Defaults, mirrored in config_schema.json.
DEFAULT_LOGO_SIZE_PERCENT = 50
DEFAULT_SHOW_NAMES = True

#: The NRL layouts size a logo at 1.5x the panel height; 100% keeps that.
_BASE_LOGO_HEIGHT = 1.5
_SIDE_PAD = 3        # the switch layouts shift logos 2px past the panel edge; 1px stays visible
_GAP = 1             # pixels between logo and name
_TEXT_COLOUR = (255, 255, 255, 255)

_cache: Dict[Tuple, Image.Image] = {}
_CACHE_MAX = 64


def remember(team_id: Any, name: str) -> None:
    if team_id not in (None, "") and name:
        TEAM_NAMES[str(team_id)] = name


def settings(config: Optional[Dict[str, Any]]) -> Tuple[int, bool]:
    """(logo size percent, show names) from a plugin or manager config."""
    config = config or {}
    try:
        percent = int(config.get("logo_size_percent", DEFAULT_LOGO_SIZE_PERCENT))
    except (TypeError, ValueError):
        percent = DEFAULT_LOGO_SIZE_PERCENT
    percent = max(10, min(100, percent))
    return percent, bool(config.get("show_team_names", DEFAULT_SHOW_NAMES))


def _text_box(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, left, top


def badge(logo: Image.Image, team_id: Any, abbr: str, panel_height: int,
          font, percent: int = DEFAULT_LOGO_SIZE_PERCENT, show_name: bool = True,
          max_width: Optional[int] = None) -> Image.Image:
    """``logo`` at ``percent`` of the NRL layout's size, name centred below.

    The result is RGBA, at most ``panel_height`` tall, and ``max_width`` wide
    when given. The name is the country's full name if it fits that width,
    else its three-letter code.
    """
    name = (TEAM_NAMES.get(str(team_id)) or abbr or "").upper() if show_name else ""
    # The logo's pixels, not its identity: a freed image's id can be reused
    # by another team's, and a refreshed logo file must replace the badge.
    key = (hash(logo.tobytes()), logo.size, logo.mode, str(team_id), abbr, panel_height,
           percent, name, max_width, id(font))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text, text_w, text_h, text_left, text_top = "", 0, 0, 0, 0
    if name and font is not None:
        limit = (max_width - 2 * _SIDE_PAD) if max_width else None
        for candidate in (name, (abbr or "").upper()):
            if not candidate:
                continue
            w, h, left, top = _text_box(probe, candidate, font)
            if limit is None or w <= limit:
                text, text_w, text_h, text_left, text_top = candidate, w, h, left, top
                break

    room = panel_height - (text_h + _GAP if text else 0)
    target_h = max(4, min(room, round(panel_height * _BASE_LOGO_HEIGHT * percent / 100)))
    target_w = (max_width - 2 * _SIDE_PAD) if max_width else target_h * 2
    mark = logo.convert("RGBA")
    bbox = mark.getbbox()
    if bbox:
        mark = mark.crop(bbox)
    mark.thumbnail((max(4, target_w), target_h), Image.Resampling.LANCZOS)

    width = max(mark.width, text_w) + 2 * _SIDE_PAD
    height = mark.height + (text_h + _GAP if text else 0)
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste(mark, ((width - mark.width) // 2, 0), mark)
    if text:
        draw = ImageDraw.Draw(out)
        draw.fontmode = "1"       # crisp 1-bit text on an LED panel
        draw.text(((width - text_w) // 2 - text_left, mark.height + _GAP - text_top),
                  text, font=font, fill=_TEXT_COLOUR)

    _cache[key] = out
    while len(_cache) > _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    return out
