#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  W O R L D   C U P   T E R M I N A L  ::  retro phosphor match-cast
================================================================================
A live, terminal-only World Cup scoreboard with CRT-style ASCII visuals,
goal/card event bursts, team & player detail, and a rotating culture/news
ticker that takes over during the slow moments of a match.

Data: ESPN's public (key-less) soccer API  ->  league "fifa.world"
UI  : rich (Live layout, 7-segment ASCII scoreboard, blinking LIVE lamp)

Usage:
  python worldcup.py            # live match-cast
  python worldcup.py --demo     # scripted demo: fakes a live match + goals
  python worldcup.py --once     # render a single frame and exit (for testing)
  python worldcup.py --plain    # disable the CRT scanline tint

Controls: Ctrl-C to quit.
================================================================================
"""
import sys, os, time, threading, random, itertools, datetime, argparse, shutil
from collections import deque

def _utcnow():
    """Naive UTC 'now' — the modern, non-deprecated spelling of the old
    datetime.utcnow(). Returns a tz-naive datetime in UTC so the existing
    '...isoformat() + "Z"' and date-range formatting keep working unchanged."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

def set_taskbar_icon():
    """On Windows, give this console window the World Cup trophy icon so it
    shows in the taskbar. Works in the classic console host (conhost); a no-op
    elsewhere. Safe to call unconditionally."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worldcup.ico")
        if not os.path.exists(ico):
            return
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        # distinct AppUserModelID so the taskbar uses the window icon, not python's
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WorldCup.Terminal")
        except Exception:
            pass
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1
        for (w, h, which) in ((16, 16, ICON_SMALL), (32, 32, ICON_BIG)):
            hicon = u32.LoadImageW(None, ico, IMAGE_ICON, w, h, LR_LOADFROMFILE)
            if hicon:
                u32.SendMessageW(hwnd, WM_SETICON, which, hicon)
    except Exception:
        pass

def set_console_font(face="Consolas", height=22):
    """Enlarge the console font for readability (classic console host only)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        LF_FACESIZE, STD_OUTPUT_HANDLE = 32, -11
        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
        class CONSOLE_FONT_INFOEX(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("nFont", ctypes.c_ulong),
                        ("dwFontSize", COORD), ("FontFamily", ctypes.c_uint),
                        ("FontWeight", ctypes.c_uint),
                        ("FaceName", ctypes.c_wchar * LF_FACESIZE)]
        k32 = ctypes.windll.kernel32
        info = CONSOLE_FONT_INFOEX()
        info.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
        info.dwFontSize = COORD(0, height)
        info.FontFamily = 54        # FF_MODERN | TMPF_TRUETYPE | FIXED_PITCH
        info.FontWeight = 400
        info.FaceName = face
        k32.SetCurrentConsoleFontEx(k32.GetStdHandle(STD_OUTPUT_HANDLE), False,
                                    ctypes.byref(info))
    except Exception:
        pass

def maximize_console():
    """Maximize the console window so the larger font still has room."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
    except Exception:
        pass

def enable_vt():
    """Turn on VT/ANSI processing for the Windows console. This lets rich draw
    each frame as one batched escape-sequence write instead of the slow,
    flicker-prone legacy Win32 cell-by-cell path. Returns True on success."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        h = k32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            return bool(k32.SetConsoleMode(
                h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        pass
    return False

try:
    import requests
except ImportError:
    sys.exit("This needs 'requests'.  Install with:  pip install requests rich")

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich.align import Align
    from rich.table import Table
    from rich.rule import Rule
    from rich import box
except ImportError:
    sys.exit("This needs 'rich'.  Install with:  pip install rich requests")

BOX = box.DOUBLE          # crisp double-line panel borders (retro CRT)

LEAGUE      = "fifa.world"
SCOREBOARD  = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LEAGUE}/scoreboard"
SUMMARY     = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LEAGUE}/summary"
UA          = {"User-Agent": "Mozilla/5.0 (retro-worldcup-terminal)"}
FETCH_EVERY  = 18         # seconds between live data pulls
FUTURE_DAYS  = 28         # how far ahead to look for fixtures
FACT_SECONDS = 300        # fact rotates every 5 minutes (ticks ~= seconds)
REDDIT_EVERY = 90         # min seconds between r/soccer RSS pulls (rate-limit safe)
REDDIT_UA    = {"User-Agent": "worldcup-terminal/1.0 (r/soccer match buzz)"}

# --------------------------------------------------------------------------- #
#  RETRO 5x5 DOT-MATRIX FONT  (scoreboard digits)                             #
# --------------------------------------------------------------------------- #
_FONT = {
    "0": ["11111", "10001", "10001", "10001", "11111"],
    "1": ["00100", "01100", "00100", "00100", "01110"],
    "2": ["11111", "00001", "11111", "10000", "11111"],
    "3": ["11111", "00001", "01111", "00001", "11111"],
    "4": ["10001", "10001", "11111", "00001", "00001"],
    "5": ["11111", "10000", "11111", "00001", "11111"],
    "6": ["11111", "10000", "11111", "10001", "11111"],
    "7": ["11111", "00001", "00010", "00100", "01000"],
    "8": ["11111", "10001", "11111", "10001", "11111"],
    "9": ["11111", "10001", "11111", "00001", "11111"],
    "-": ["00000", "00000", "11111", "00000", "00000"],
    ":": ["00000", "00100", "00000", "00100", "00000"],
    " ": ["00000", "00000", "00000", "00000", "00000"],
}

def bigdigits(s, color, wide=True):
    """Render a short string in the 5x5 block font. wide=double-cell pixels
    (crisp, roomy windows); wide=False=single-cell (half width, tight windows)."""
    on, off, gap = ("██", "  ", "  ") if wide else ("█", " ", " ")
    rows = ["", "", "", "", ""]
    for ch in str(s):
        glyph = _FONT.get(ch, _FONT[" "])
        for r in range(5):
            rows[r] += "".join(on if px == "1" else off for px in glyph[r]) + gap
    t = Text(no_wrap=True, overflow="crop")
    for r, line in enumerate(rows):
        t.append(line + ("\n" if r < 4 else ""), style=f"bold {color}")
    return t

# --------------------------------------------------------------------------- #
#  ASCII FLAGS  (approximate, 6 wide x 3 tall — horizontal stripes)           #
# --------------------------------------------------------------------------- #
# bright, saturated swatch colors — every flag is exactly 8 cells wide x 3 tall
DK = "grey30"   # readable "black" stripe on a dark terminal
_STRIPE = {
    "Netherlands": ["bright_red", "bright_white", "blue"],
    "Germany":     [DK, "bright_red", "bright_yellow"],
    "Paraguay":    ["bright_red", "bright_white", "blue"],
    "Spain":       ["bright_red", "bright_yellow", "bright_red"],
    "Italy":       ["bright_green", "bright_white", "bright_red"],
    "Belgium":     [DK, "bright_yellow", "bright_red"],
    "Colombia":    ["bright_yellow", "blue", "bright_red"],
    "Russia":      ["bright_white", "blue", "bright_red"],
    "France":      ["blue", "bright_white", "bright_red"],
    "Argentina":   ["bright_cyan", "bright_white", "bright_cyan"],
    "Croatia":     ["bright_red", "bright_white", "blue"],
    "Mexico":      ["bright_green", "bright_white", "bright_red"],
    "Hungary":     ["bright_red", "bright_white", "bright_green"],
    "Austria":     ["bright_red", "bright_white", "bright_red"],
    "Ireland":     ["bright_green", "bright_white", "orange1"],
    "Nigeria":     ["bright_green", "bright_white", "bright_green"],
    "Peru":        ["bright_red", "bright_white", "bright_red"],
}
_SPECIAL = {
    "Japan":         ["[bright_white]████████",
                      "[bright_white]███[bright_red]██[bright_white]███",
                      "[bright_white]████████"],
    "Brazil":        ["[bright_green]███[bright_yellow]██[bright_green]███",
                      "[bright_green]█[bright_yellow]██[blue]██[bright_yellow]██[bright_green]█",
                      "[bright_green]███[bright_yellow]██[bright_green]███"],
    "Morocco":       ["[bright_red]████████",
                      "[bright_red]███[bright_green]✦[bright_red]████",
                      "[bright_red]████████"],
    "England":       ["[bright_white]███[bright_red]██[bright_white]███",
                      "[bright_red]████████",
                      "[bright_white]███[bright_red]██[bright_white]███"],
    "United States": ["[blue]███[bright_red]█████",
                      "[bright_white]████████",
                      "[bright_red]████████"],
    "Portugal":      ["[bright_green]███[bright_red]█████",
                      "[bright_green]██[bright_yellow]●[bright_red]█████",
                      "[bright_green]███[bright_red]█████"],
    "Canada":        ["[bright_red]██[bright_white]████[bright_red]██",
                      "[bright_red]██[bright_white]█[bright_red]██[bright_white]█[bright_red]██",
                      "[bright_red]██[bright_white]████[bright_red]██"],
}

def flag_lines(country, color_hex):
    if country in _SPECIAL:
        return _SPECIAL[country]
    if country in _STRIPE:
        return [f"[{c}]████████" for c in _STRIPE[country]]
    col = f"#{color_hex}" if color_hex and color_hex.lower() not in ("", "ffffff") else "bright_white"
    return [f"[{col}]████████"] * 3

# --------------------------------------------------------------------------- #
#  CULTURE / TRIVIA DECK  (always-available filler for slow moments)          #
# --------------------------------------------------------------------------- #
CULTURE = {
    "Brazil": [
        "5 World Cup titles — more than any nation on Earth.",
        "The 'Seleção' play in canarinho yellow, chosen after the 1950 Maracanazo.",
        "Pelé scored an estimated 1,281 goals across his career.",
        "Joga bonito — 'the beautiful game' — is a national philosophy, not a slogan.",
    ],
    "Japan": [
        "Samurai Blue fans famously stay behind to clean the stadium after matches.",
        "Japan has reached the knockout rounds in 4 of the last 7 World Cups.",
        "The J-League (1993) transformed Asian football almost overnight.",
        "Captain tsubasa, a 1980s manga, inspired a generation of world stars.",
    ],
    "Germany": [
        "4-time champions; masters of the tournament 'turnier-mannschaft' mentality.",
        "The 2014 side beat the hosts Brazil 7–1 in a semifinal for the ages.",
        "'Die Mannschaft' simply means 'the team' — no nickname needed.",
        "Germany has reached at least the quarterfinals 16 times.",
    ],
    "Netherlands": [
        "Inventors of 'Total Football' — every outfield player interchangeable.",
        "Three World Cup finals (1974, 1978, 2010), zero titles — the great nearly-men.",
        "Oranje fans turn entire cities into a sea of orange smoke and song.",
        "Johan Cruyff's '14' is the most influential number in football thought.",
    ],
    "Paraguay": [
        "La Albirroja are famed for ferocious, never-say-die defending.",
        "Goalkeeper José Luis Chilavert scored 8 goals for the national team.",
        "Reached the quarterfinals in 2010, their best-ever run.",
        "Home matches in Asunción are played in fearsome heat and altitude swings.",
    ],
    "Morocco": [
        "First African & Arab nation to reach a World Cup semifinal (2022).",
        "The Atlas Lions stunned Belgium, Spain and Portugal on that run.",
        "Backed by some of the loudest travelling support in the world.",
        "Sofiane Boufal danced with his mother on the pitch after beating Portugal.",
    ],
}
GENERIC = [
    "WORLD CUP 2026 :: first 48-team finals, hosted across USA · Canada · Mexico.",
    "104 matches — the largest World Cup in history — spread over 16 host cities.",
    "The trophy: 18-carat gold, 6.1 kg, designed by Silvio Gazzaniga in 1974.",
    "Only 8 different nations have ever lifted the World Cup since 1930.",
    "The 'group of death' — a phrase coined by Mexican press at the 1970 finals.",
    "Estadio Azteca (Mexico City) will become the first stadium to host 3 World Cups.",
    "Penalty shootouts arrived in 1978; the agony has been ritual ever since.",
    "VAR debuted at the 2018 finals in Russia, forever changing the offside line.",
]

# --------------------------------------------------------------------------- #
#  DATA MODEL                                                                  #
# --------------------------------------------------------------------------- #
class Side:
    def __init__(self, c):
        t = c.get("team", {})
        self.id    = c.get("id")
        self.name  = t.get("displayName", t.get("name", "—"))
        self.short = t.get("shortDisplayName", self.name)
        self.abbr  = t.get("abbreviation", self.name[:3].upper())
        self.color = t.get("color", "")
        self.score = c.get("score", "0")
        # penalty-shootout tally (knockout ties decided from the spot); None if no shootout
        self.shootout = c.get("shootoutScore")
        self.winner   = bool(c.get("winner", False))
        self.home  = c.get("homeAway") == "home"
        self.form  = c.get("form", "")
        recs = c.get("records") or []
        self.record = recs[0].get("summary", "") if recs else ""

class Event:
    def __init__(self, d):
        self.type   = (d.get("type") or {}).get("text", d.get("type", "Play"))
        self.clock  = (d.get("clock") or {}).get("displayValue", "")
        self.team   = (d.get("team") or {}).get("id")
        self.scoring= bool(d.get("scoringPlay"))
        self.text   = d.get("text", "")
        ath = d.get("athletesInvolved") or []
        self.players = [a.get("displayName", "") for a in ath]

class Match:
    def __init__(self, ev):
        self.id     = ev.get("id")
        self.date   = ev.get("date", "")
        comp        = (ev.get("competitions") or [{}])[0]
        st          = ev.get("status", {}).get("type", {})
        self.state  = st.get("state", "pre")            # pre / in / post
        self.detail = st.get("shortDetail", st.get("description", ""))
        # ESPN status name, e.g. STATUS_FINAL / STATUS_FINAL_AET / STATUS_FINAL_PEN
        self.status_name = st.get("name", "")
        self.clock  = ev.get("status", {}).get("displayClock", "")
        self.period = ev.get("status", {}).get("period", 0)
        ven         = comp.get("venue", {})
        addr        = ven.get("address", {})
        self.venue  = ven.get("fullName", "")
        self.city   = ", ".join(x for x in [addr.get("city"), addr.get("country")] if x)
        comps       = comp.get("competitors", [])
        self.home   = next((Side(c) for c in comps if c.get("homeAway") == "home"), None)
        self.away   = next((Side(c) for c in comps if c.get("homeAway") == "away"), None)
        self.events = [Event(d) for d in (comp.get("details") or [])]
        bc          = comp.get("broadcasts") or []
        names       = bc[0].get("names") if bc else None
        self.tv     = ", ".join(names) if names else ""
        notes       = comp.get("notes") or []
        self.note   = (notes[0].get("headline", "") if notes else "") or \
                      ((ev.get("season") or {}).get("slug", "") if isinstance(ev.get("season"), dict) else "")

    @property
    def group(self):
        return self.note if "group" in self.note.lower() else ""

    @property
    def round(self):
        n = self.note.lower()
        if "final" in n and "quarter" not in n and "semi" not in n: return "Final"
        if "semi" in n:                         return "Semifinal"
        if "quarter" in n:                       return "Quarterfinal"
        if "round of 16" in n or "round of 32" in n or "playoff" in n: return self.note.title()
        return ""

    @property
    def shootout(self):
        """'home–away' penalty tally if this match was decided on pens, else ''."""
        h, a = self.home, self.away
        if not h or not a:                              return ""
        if h.shootout is None and a.shootout is None:   return ""
        return f"{h.shootout or 0}–{a.shootout or 0}"

    @property
    def pens(self):
        """(winner_abbr, 'W–L') for a shootout, winner's tally first; else None.

        A knockout tie level after extra time is settled from the spot, so the
        shootout — not the 1–1 scoreline — is what decided who advanced. We lead
        with the winner so a glance answers "who went through?".
        """
        h, a = self.home, self.away
        if not h or not a:                              return None
        if h.shootout is None and a.shootout is None:   return None
        hs, as_ = h.shootout or 0, a.shootout or 0
        if h.winner:        return (h.abbr, f"{hs}–{as_}")
        if a.winner:        return (a.abbr, f"{as_}–{hs}")
        # winner flag missing — fall back to the higher tally
        hi, lo = max(hs, as_), min(hs, as_)
        return (h.abbr if hs >= as_ else a.abbr, f"{hi}–{lo}")

    @property
    def aet(self):
        """True if settled in extra time without a shootout (a.e.t.)."""
        return "AET" in (self.status_name or "").upper() and not self.pens

    @property
    def live(self):  return self.state == "in"
    @property
    def done(self):  return self.state == "post"

    def kickoff_local(self):
        try:
            dt = datetime.datetime.fromisoformat(self.date.replace("Z", "+00:00"))
            return dt.astimezone()
        except Exception:
            return None

    def countdown(self):
        dt = self.kickoff_local()
        if not dt:
            return ""
        delta = dt - datetime.datetime.now(datetime.timezone.utc).astimezone()
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "KICK-OFF"
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _   = divmod(rem, 60)
        if d:   return f"in {d}d {h:02d}h {m:02d}m"
        return f"in {h:02d}h {m:02d}m"

# --------------------------------------------------------------------------- #
#  DATA STORE  (background fetch thread)                                       #
# --------------------------------------------------------------------------- #
# team table used to fabricate a realistic demo slate (name, brand color)
_DT = {
    "BRA": ("Brazil", "fee000"),      "JPN": ("Japan", "000555"),
    "ARG": ("Argentina", "75aadb"),   "MEX": ("Mexico", "006847"),
    "FRA": ("France", "002654"),      "CRO": ("Croatia", "ff0000"),
    "ESP": ("Spain", "c60b1e"),       "GER": ("Germany", "111111"),
    "ENG": ("England", "ffffff"),     "NED": ("Netherlands", "f36c21"),
    "POR": ("Portugal", "006600"),    "MAR": ("Morocco", "c1272d"),
    "BEL": ("Belgium", "111111"),     "ITA": ("Italy", "0066cc"),
}
def _demo_match(mid, h, a, hs, as_, state, dt, venue, city):
    def comp(side, abbr, score):
        name, color = _DT[abbr]
        return {"id": abbr.lower(), "homeAway": side, "score": str(score),
                "team": {"displayName": name, "shortDisplayName": name,
                         "abbreviation": abbr, "color": color}}
    detail = {"pre": "Scheduled", "post": "FT", "in": "Live"}[state]
    ev = {"id": mid, "date": dt,
          "status": {"displayClock": "0'", "period": 0,
                     "type": {"state": state, "shortDetail": detail}},
          "competitions": [{"venue": {"fullName": venue, "address": {"city": city}},
                            "broadcasts": [], "details": [],
                            "competitors": [comp("home", h, hs), comp("away", a, as_)]}]}
    return Match(ev)

# --------------------------------------------------------------------------- #
#  REDDIT  (r/soccer live match-thread buzz via RSS — the .json API is blocked) #
# --------------------------------------------------------------------------- #
import re as _re, html as _html, xml.etree.ElementTree as _ET
_ATOM = "{http://www.w3.org/2005/Atom}"
_BOTS = {"AutoModerator", "soccer-bot", "[deleted]", "[removed]"}
# best-effort profanity guard — live fan chatter is unfiltered, so we drop the
# worst of it. Not exhaustive; substring match on word boundaries.
_BADWORDS = {"fuck", "shit", "cunt", "nigg", "faggot", "retard", "bitch",
             "bastard", "wanker", "slut", "whore", "dick", "pussy"}
_URL_RE = _re.compile(r"https?://\S+")

def _reddit_get(url):
    try:
        r = requests.get(url, headers=REDDIT_UA, timeout=15)
        return r.status_code, r.content
    except Exception:
        return 0, b""

def _reddit_entries(content):
    """Yield (title, href, author, text) from an Atom feed."""
    try:
        root = _ET.fromstring(content)
    except Exception:
        return
    for e in root.findall(_ATOM + "entry"):
        title = (e.findtext(_ATOM + "title") or "").strip()
        link  = e.find(_ATOM + "link")
        href  = link.get("href") if link is not None else ""
        auth  = e.find(_ATOM + "author")
        aname = (auth.findtext(_ATOM + "name") if auth is not None else "") or "?"
        raw   = e.findtext(_ATOM + "content") or ""
        text  = _html.unescape(_re.sub("<[^>]+>", " ", raw))
        text  = _re.sub(r"\s+", " ", text).strip()
        yield title, href, aname.replace("/u/", ""), text

def _reddit_thread_in(content, home, away):
    """Find the Match/Pre-Match Thread for this fixture in already-fetched hot
    content; skip 'Post-Match Thread' so we don't surface a finished game."""
    h, a = home.lower(), away.lower()
    for title, href, _, _ in _reddit_entries(content):
        t = title.lower()
        if ("match thread" in t and "post-match" not in t and "post match" not in t
                and (h in t or a in t)):
            return href
    return None

def _reddit_team_posts(content, home, away):
    """Fallback when no thread exists yet (pre-match): recent r/soccer posts that
    mention either team — gives relevant content before the thread is created."""
    h, a = home.lower(), away.lower()
    out = []
    for title, href, author, _ in _reddit_entries(content):
        t = title.lower()
        if "match thread" in t:
            continue
        if (h in t or a in t) and not any(b in t for b in _BADWORDS):
            out.append((author, title.strip()))
    return out[:8]

def _clean_comment(author, text):
    if author in _BOTS or not text:
        return None
    low = text.lower()
    # skip the match-thread submission/bot post (match info, not a fan comment)
    if "best viewed on" in low or "old.reddit" in low:
        return None
    if "venue" in low and "competition" in low and "referee" in low:
        return None
    if any(b in low for b in _BADWORDS):
        return None
    text = _URL_RE.sub("", text).strip()
    if len(text) < 8 or text in ("[deleted]", "[removed]"):
        return None
    if len(text) > 170:
        text = text[:167].rstrip() + "…"
    return (author, text)

def _reddit_comments(thread_url):
    sc, content = _reddit_get(thread_url.rstrip("/") + "/.rss?sort=new&limit=25")
    if sc != 200:
        return []
    out = []
    for _, _, author, text in _reddit_entries(content):
        c = _clean_comment(author, text)
        if c:
            out.append(c)
    return out[:12]

class Store:
    def __init__(self, demo=False):
        self.demo      = demo
        self.lock      = threading.Lock()
        self.matches   = []
        self.news      = deque(maxlen=12)
        self.status    = "booting"
        self.last_pull = 0
        self.seen_evt  = set()      # event signatures already announced
        self.flash     = deque(maxlen=6)   # recent goal/card flashes
        self.reddit    = deque(maxlen=12)  # latest r/soccer match-thread comments
        self._reddit_thread = None
        self._reddit_key    = None
        self._reddit_last   = 0
        self._reddit_fail   = 0
        self._demo_clock = -4    # short "waiting for kick-off" pre-roll in demo

    # ---- networking ------------------------------------------------------- #
    def _get(self, url, params=None):
        r = requests.get(url, params=params, headers=UA, timeout=12)
        r.raise_for_status()
        return r.json()

    def _pull_live(self):
        try:
            today = self._get(SCOREBOARD).get("events", [])
            # widen the net for upcoming fixtures
            now = _utcnow()
            rng = f"{now:%Y%m%d}-{now + datetime.timedelta(days=FUTURE_DAYS):%Y%m%d}"
            try:
                future = self._get(SCOREBOARD, {"dates": rng}).get("events", [])
            except Exception:
                future = []
            by_id = {}
            for ev in itertools.chain(today, future):
                by_id[ev.get("id")] = ev
            matches = sorted((Match(e) for e in by_id.values()),
                             key=lambda m: ({"in": 0, "pre": 1, "post": 2}[m.state], m.date))
            self._detect_events(matches)
            with self.lock:
                self.matches = matches
                self.status  = "online"
                self.last_pull = time.time()
            self._refresh_news(matches)
            # buzz follows the live match, or the next upcoming one (pre-match thread)
            self._pull_reddit(next((m for m in matches if m.live), None)
                              or next((m for m in matches if m.state == "pre"), None))
        except Exception as e:
            with self.lock:
                self.status = f"offline ({type(e).__name__})"

    def _pull_reddit(self, focus):
        """Pull newest r/soccer match-thread comments for the live match.
        Throttled + cached so we stay well under Reddit's RSS rate limit."""
        if not focus:
            with self.lock:
                self.reddit.clear()
            self._reddit_thread = None
            self._reddit_fail = 0
            return
        # back off on repeated failures so a rate-limit penalty can clear
        interval = REDDIT_EVERY * (1 + min(self._reddit_fail, 5))   # up to ~9 min
        if time.time() - self._reddit_last < interval:
            return
        self._reddit_last = time.time()
        try:
            # already have this match's thread -> just refresh its comments
            if self._reddit_thread and self._reddit_key == focus.id:
                self._set_reddit(_reddit_comments(self._reddit_thread))
                return
            # otherwise fetch r/soccer hot once: find the thread, else team posts
            sc, content = _reddit_get(
                "https://www.reddit.com/r/soccer/hot/.rss?limit=40")
            if sc != 200:
                self._reddit_fail += 1
                return
            link = _reddit_thread_in(content, focus.home.name, focus.away.name)
            if link:
                self._reddit_thread = link
                self._reddit_key = focus.id
                time.sleep(2)   # brief gap, then fetch comments the same cycle
                self._set_reddit(_reddit_comments(link))
            else:
                # no thread yet (pre-match) -> recent posts about the teams;
                # keep thread None so we re-check for the real thread each cycle
                self._reddit_thread = None
                self._set_reddit(
                    _reddit_team_posts(content, focus.home.name, focus.away.name))
        except Exception:
            self._reddit_fail += 1

    def _set_reddit(self, items):
        if items:
            with self.lock:
                self.reddit.clear()
                for c in items:
                    self.reddit.append(c)
            self._reddit_fail = 0
        else:
            self._reddit_fail += 1

    def _refresh_news(self, matches):
        focus = next((m for m in matches if m.live), None) \
             or next((m for m in matches if not m.done), None)
        if not focus:
            return
        try:
            data = self._get(SUMMARY, {"event": focus.id})
            arts = (data.get("news") or {}).get("articles", []) if isinstance(data.get("news"), dict) else []
            heads = [a.get("headline", "") for a in arts if a.get("headline")]
            with self.lock:
                self.news.clear()
                for h in heads:
                    self.news.append(h)
        except Exception:
            pass

    def _detect_events(self, matches):
        for m in matches:
            for ev in m.events:
                sig = (m.id, ev.clock, ev.type, tuple(ev.players))
                if sig in self.seen_evt:
                    continue
                self.seen_evt.add(sig)
                if not self.last_pull:        # first load: don't replay history
                    continue
                self.flash.append((m, ev, time.time()))

    # ---- demo mode -------------------------------------------------------- #
    def _pull_demo(self):
        if not self.matches:
            ev = {
                "id": "DEMO", "date": _utcnow().isoformat() + "Z",
                "status": {"displayClock": "00:00", "period": 1,
                           "type": {"state": "in", "shortDetail": "1st Half"}},
                "competitions": [{
                    "venue": {"fullName": "Estadio Azteca",
                              "address": {"city": "Mexico City", "country": "Mexico"}},
                    "broadcasts": [{"names": ["RETRO-TV"]}],
                    "competitors": [
                        {"id": "br", "homeAway": "home", "score": "0", "form": "WWDWL",
                         "team": {"displayName": "Brazil", "shortDisplayName": "Brazil",
                                  "abbreviation": "BRA", "color": "fee000"}},
                        {"id": "jp", "homeAway": "away", "score": "0", "form": "WDWWW",
                         "team": {"displayName": "Japan", "shortDisplayName": "Japan",
                                  "abbreviation": "JPN", "color": "000555"}},
                    ],
                    "details": [],
                }],
            }
            now = _utcnow()
            iso = lambda **kw: (now + datetime.timedelta(**kw)).isoformat() + "Z"
            self.matches = [Match(ev)] + [
                # ---- upcoming ----
                _demo_match("U1", "ARG", "MEX", 0, 0, "pre", iso(hours=3),
                            "SoFi Stadium", "Los Angeles"),
                _demo_match("U2", "FRA", "CRO", 0, 0, "pre", iso(hours=6),
                            "MetLife Stadium", "New Jersey"),
                _demo_match("U3", "ESP", "GER", 0, 0, "pre", iso(days=1, hours=2),
                            "AT&T Stadium", "Dallas"),
                _demo_match("U4", "ENG", "NED", 0, 0, "pre", iso(days=1, hours=5),
                            "Mercedes-Benz Stadium", "Atlanta"),
                # ---- recently completed ----
                _demo_match("R1", "POR", "MAR", 2, 1, "post", iso(hours=-3),
                            "Hard Rock Stadium", "Miami"),
                _demo_match("R2", "BEL", "ITA", 0, 0, "post", iso(hours=-6),
                            "Levi's Stadium", "Santa Clara"),
            ]
            self.status = "DEMO"
        m = self.matches[0]
        self._demo_clock += 1
        mins = self._demo_clock
        if mins < 1:
            # pre-roll: match hasn't kicked off yet -> Event Feed shows "waiting"
            m.state = "pre"
            m.detail = "Scheduled"
            m.date = (_utcnow()
                      + datetime.timedelta(seconds=max(1, (1 - mins) * 2))).isoformat() + "Z"
            return
        m.state = "in"
        m.clock = f"{mins}'"
        m.detail = "1st Half" if mins <= 45 else "2nd Half"
        script = {7: ("jp", "Goal", "Kubo"), 19: ("br", "Yellow Card", "Casemiro"),
                  23: ("br", "Goal", "Vinícius Jr"), 41: ("br", "Goal", "Rodrygo"),
                  58: ("jp", "Goal", "Mitoma"), 66: ("jp", "Red Card", "Endo")}
        if mins in script:
            tid, typ, who = script[mins]
            ev = Event({"type": {"text": typ}, "clock": {"displayValue": f"{mins}'"},
                        "team": {"id": tid}, "scoringPlay": typ == "Goal",
                        "athletesInvolved": [{"displayName": who}]})
            m.events.append(ev)
            if typ == "Goal":
                side = m.home if tid == "br" else m.away
                side.score = str(int(side.score) + 1)
            self.flash.append((m, ev, time.time()))
        if mins == 1:
            for f in GENERIC[:6]:
                self.news.append(f)
            for c in [
                ("samba_dreamer", "Vini just glided past two defenders like they weren't there 😮‍💨"),
                ("nippon_ultra", "Japan's pressing is RELENTLESS, Brazil can't build out 🔥"),
                ("tactics_nerd", "That 4-2-3-1 is leaving Casemiro horribly exposed in transition"),
                ("neutral_fan99", "Best match of the tournament so far and it's only the 1st half"),
                ("selecao_til_i_die", "Why are we defending so deep with a one-goal lead?!"),
                ("xG_enjoyer", "Japan shading this on the underlying numbers, 1.4 xG to 0.9"),
                ("aussie_neutral", "Mitoma vs that fullback is the best duel on the pitch rn"),
            ]:
                self.reddit.append(c)
            self._reddit_thread = "https://www.reddit.com/r/soccer/comments/demo/match_thread_brazil_vs_japan/"

    # ---- loop ------------------------------------------------------------- #
    def run(self):
        while True:
            if self.demo:
                with self.lock:
                    self._pull_demo()
                time.sleep(1.4)
            else:
                self._pull_live()
                time.sleep(FETCH_EVERY)

    def snapshot(self):
        with self.lock:
            return (list(self.matches), list(self.news), self.status,
                    list(self.flash), list(self.reddit), self._reddit_thread)

# --------------------------------------------------------------------------- #
#  RENDERING                                                                   #
# --------------------------------------------------------------------------- #
PHOSPHOR = "bright_green"
AMBER    = "yellow"

TROPHY = [
    r"   ___________   ",
    r"  '._==_==_=_.'  ",
    r"  .-\:      /-.  ",
    r" | (|:.     |) | ",
    r"  '-|:.     |-'  ",
    r"    \::.    /    ",
    r"     '::. .'     ",
    r"       ) (       ",
    r"     _.' '._     ",
    r"    `-------`    ",
]

def boot_sequence(console, plain):
    if plain:
        return
    lines = [
        "INITIALIZING SATELLITE UPLINK . . . . . . . . . . OK",
        "ACQUIRING FIFA WORLD FEED  [ fifa.world ] . . . . OK",
        "CALIBRATING PHOSPHOR TUBE  ( 60Hz / amber ) . . . OK",
        "LOADING CULTURE & NEWS DECK . . . . . . . . . . . OK",
        "RETRO WORLD CUP TERMINAL — ONLINE",
    ]
    console.clear()
    for ln in lines:
        console.print(Text(ln, style=PHOSPHOR))
        time.sleep(0.28)
    time.sleep(0.4)

def header(status, blink):
    lamp = "[bold red]● LIVE[/]" if blink else "[red]○ LIVE[/]"
    now  = datetime.datetime.now().strftime("%a %d %b  %H:%M:%S")
    bar  = Text("⚽ ", style="bold yellow")
    bar.append("WORLD CUP TERMINAL", style="bold bright_green")
    bar.append("  ::  2026 GLOBAL FEED", style="green")
    line = Table.grid(expand=True)
    line.add_column(justify="left", no_wrap=True, overflow="ellipsis")
    line.add_column(justify="right", no_wrap=True, overflow="ellipsis")
    line.add_row(bar, Text.from_markup(f"{lamp}  [grey85]{status.upper()}  {now}[/]"))
    return Panel(line, style="green", box=BOX, border_style="bright_green", padding=(0, 1))

def crest(side, color):
    fl = flag_lines(side.name, side.color)
    t = Text()
    t.append(f"{side.abbr}\n", style=f"bold {color}")
    for ln in fl:
        t.append_text(Text.from_markup(ln + "\n"))
    if side.record:
        t.append(side.record, style="grey85")
    return t

def scoreboard_panel(m):
    hc = "#" + (m.home.color or "ffffff")
    ac = "#" + (m.away.color or "ffffff")
    if (m.home.color or "").lower() in ("", "000000", "000555"): hc = "bright_white"
    if (m.away.color or "").lower() in ("", "000000", "000555"): ac = "bright_white"

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(justify="center", ratio=3)
    grid.add_column(justify="center", ratio=4)
    grid.add_column(justify="center", ratio=3)


    if m.live:
        midtxt = Text()
        midtxt.append("●  ", style="bold red")
        midtxt.append(f"{m.clock or m.detail}\n", style="bold yellow")
        midtxt.append(m.detail, style="grey85")
        mid = Align.center(midtxt, vertical="middle")
    elif m.done:
        mid = Align.center(Text("FULL\nTIME", style="bold grey85", justify="center"),
                           vertical="middle")
    else:
        midtxt = Text(justify="center")
        midtxt.append("KICK-OFF\n", style="bold green")
        midtxt.append((m.countdown() or "") + "\n", style="bold yellow")
        ko = m.kickoff_local()
        if ko:
            midtxt.append(ko.strftime("%a %H:%M"), style="grey85")
        mid = Align.center(midtxt, vertical="middle")

    # everything centred on one axis: ABBR -> flag -> SCORE label -> number
    def block(side):
        col = hc if side is m.home else ac
        sc  = side.score if side.score != "" else "0"
        t = Text(justify="center", no_wrap=True)
        t.append(f"{side.abbr}\n", style=f"bold {col}")
        for ln in flag_lines(side.name, side.color):
            t.append_text(Text.from_markup(ln))
            t.append("\n")
        t.append("\nSCORE\n", style="bold grey85")
        t.append(sc, style="bold bright_yellow")
        if side.shootout is not None:
            tag = "  ✓" if side.winner else ""
            t.append(f"\npens {side.shootout}{tag}", style="bold magenta")
        return t
    grid.add_row(block(m.home), mid, block(m.away))

    sub = Text(no_wrap=True, overflow="ellipsis")
    sub.append(f"{m.home.name}  v  {m.away.name}", style="bold white")
    loc = "   ".join(x for x in [m.venue, m.city] if x)
    if loc:
        sub.append(f"\n📍 {loc}", style="grey85")
    if m.tv:
        sub.append(f"    📺 {m.tv}", style="grey85")

    return Panel(Group(grid, Rule(style="green"), Align.center(sub)),
                 title="[bold yellow]◤ MATCH-CAST ◢[/]", box=BOX, border_style="bright_green",
                 padding=(0, 2))

def event_icon(ev):
    t = ev.type.lower()
    if "goal" in t and "own" in t:        return "⚽", "red",    "OWN GOAL"
    if "goal" in t:                       return "⚽", "bright_green", "G O A L"
    if "yellow" in t:                     return "🟨", "yellow", "BOOKING"
    if "red" in t:                        return "🟥", "red",    "SENT OFF"
    if "sub" in t:                        return "🔄", "cyan",   "SUB"
    if "penalty" in t:                    return "⊙",  "magenta","PENALTY"
    return "•", "grey85", ev.type.upper()

GOAL_ART = [
    "  ★ ░░░ G O A L ░░░ ★  ",
    "  ╲  ⚽  ╱   ╲  ⚽  ╱  ",
    "   ¡¡¡  GOOOOOOAL  !!!  ",
]

def feed_panel(matches, flashes):
    live = next((x for x in matches if x.live), None)

    # ---- no live match: show a clear "waiting for kick-off" card ----------- #
    if not live:
        nxt = next((x for x in matches if not x.done), None)
        w = Text(justify="center")
        w.append("\n\n")
        w.append("⏳   WAITING FOR KICK-OFF\n\n", style="bold yellow")
        if nxt:
            w.append(f"NEXT UP\n", style="grey85")
            w.append(f"{nxt.home.name}  v  {nxt.away.name}\n", style="bold bright_white")
            cd = nxt.countdown()
            w.append((cd + "\n") if cd else "\n", style="bold green")
            ko = nxt.kickoff_local()
            if ko:
                w.append(ko.strftime("%a %d %b · %H:%M") + "\n", style="grey85")
            if nxt.venue:
                w.append(f"📍 {nxt.venue}\n", style="grey85")
        else:
            w.append("No matches scheduled right now.\n", style="grey85")
        w.append("\nLive incidents will appear here once play begins.", style="grey74")
        return Panel(Align.center(w, vertical="middle"),
                     title="[bold yellow]◤ EVENT FEED ◢[/]",
                     box=BOX, border_style="green", padding=(0, 1))

    # ---- live: goal/card burst + chronological incident log ---------------- #
    body = []
    recent = [f for f in flashes if time.time() - f[2] < 14]
    if recent:
        m, ev, ts = recent[-1]
        icon, col, label = event_icon(ev)
        who = ", ".join(p for p in ev.players if p) or ev.text or ""
        side = m.home if ev.team == m.home.id else m.away

        # LEFT: scoring nation's flag + name, so you instantly see who scored
        flag_t = Text(justify="center")
        for fl in flag_lines(side.name, side.color):
            flag_t.append_text(Text.from_markup(fl + "\n"))
        flag_t.append(f"{side.abbr}\n", style=f"bold {col}")
        flag_t.append(side.name, style=col)

        # RIGHT: the GOAL animation + scorer + running score
        banner = Text(justify="center")
        if "goal" in ev.type.lower():
            for ln in GOAL_ART:
                banner.append(ln + "\n", style="bold yellow")
        banner.append(f"\n{icon}  {label}  {icon}\n\n", style=f"bold {col}")
        banner.append(f"{who}  ·  {ev.clock}\n", style="bold white")
        banner.append(f"{m.home.abbr} {m.home.score} – {m.away.score} {m.away.abbr}",
                      style="bold bright_green")

        row = Table.grid(padding=(0, 3))
        row.add_column(justify="center")
        row.add_column(justify="center")
        row.add_row(Align.center(flag_t, vertical="middle"), banner)
        body.append(Align.center(row))
        body.append(Rule(style="green"))

    log = Text(no_wrap=True, overflow="ellipsis")
    if live.events:
        for ev in live.events[-10:]:
            icon, col, label = event_icon(ev)
            who = ", ".join(p for p in ev.players if p) or ev.text
            log.append(f" {ev.clock:>7} ", style="grey74")
            log.append(f"{icon} ", style=col)
            side = live.home if ev.team == live.home.id else live.away
            log.append(f"{side.abbr} ", style="bold white")
            log.append(f"{who}\n", style=col)
    else:
        log.append("\n   under way — no incidents yet.\n", style="grey74")
    body.append(log)

    return Panel(Group(*body), title="[bold yellow]◤ EVENT FEED ◢[/]",
                 box=BOX, border_style="green", padding=(0, 1))

_culture_cycle = itertools.count()

def buzz_panel(matches, news, reddit, thread, tick):
    """During a live match: rotating r/soccer fan comments. Otherwise: rotating
    team / FIFA facts. (Replaces the old 'fun facts' tile.)"""
    # --- 1) r/soccer buzz: live thread comments, or pre-match team posts ------ #
    if reddit:
        n = len(reddit)
        author, txt = reddit[(tick // 12) % n]
        body = Text(justify="center", overflow="fold")
        body.append(f"u/{author}\n\n", style="bold dark_orange")
        body.append(txt + "\n", style="bright_white")
        dots = " ".join("●" if i == (tick // 12) % n else "·" for i in range(min(n, 10)))
        body.append("\n" + dots, style="dark_orange")
        return Panel(Align.center(body, vertical="middle"),
                     title="[bold dark_orange]◤ MATCH BUZZ · r/soccer ◢[/]",
                     box=BOX, border_style="dark_orange", padding=(1, 2))

    # --- 2) no buzz yet -> ESPN match news (reliable, not rate-limited) ------- #
    if news:
        head = news[(tick // 8) % len(news)]
        body = Text(justify="center", overflow="fold")
        body.append("ESPN\n", style="bold bright_cyan")
        body.append(head, style="bright_white")
        return Panel(body, title="[bold bright_cyan]◤ MATCH NEWS · ESPN ◢[/]",
                     box=BOX, border_style="cyan", padding=(1, 2))

    # --- 3) otherwise: team / FIFA facts, rotating every 5 minutes ------------ #
    focus = next((x for x in matches if x.live), None) \
         or next((x for x in matches if not x.done), None)
    deck = []
    if focus:
        for s in (focus.home, focus.away):
            for fact in CULTURE.get(s.name, []):
                deck.append((s.name.upper(), fact))
    for g in GENERIC:
        deck.append(("FIFA · WORLD CUP", g))
    if not deck:
        deck = [("FIFA · WORLD CUP", g) for g in GENERIC]

    tag, txt = deck[(tick // FACT_SECONDS) % len(deck)]
    body = Text(justify="center", overflow="fold")
    body.append(f"{tag}\n", style="bold bright_green")
    body.append(txt, style="bright_white")
    return Panel(body, title="[bold yellow]◤ MATCHDAY NOTES ◢[/]",
                 box=BOX, border_style="green", padding=(1, 2))

def fixtures_panel(matches, budget=12):
    tbl = Table.grid(expand=True, padding=(0, 1))
    tbl.add_column(justify="left", ratio=5, no_wrap=True, overflow="ellipsis")
    tbl.add_column(justify="center", ratio=2, no_wrap=True, overflow="ellipsis")
    tbl.add_column(justify="right", ratio=3, no_wrap=True, overflow="ellipsis")

    # limit to today + tomorrow (local)
    win = {datetime.date.today(), datetime.date.today() + datetime.timedelta(days=1)}
    def in_window(m):
        ko = m.kickoff_local()
        return ko is not None and ko.date() in win

    live     = [m for m in matches if m.live]
    upcoming = [m for m in matches if m.state == "pre" and in_window(m)]
    # most-recently-finished first; always keep a few pinned at the bottom
    completed = sorted((m for m in matches if m.done and in_window(m)),
                       key=lambda m: m.date, reverse=True)
    recent = completed[:4]
    # rows we always keep: live + divider + recent (+ 1 spare for the "more" cue)
    reserved = len(live) + (1 if recent else 0) + len(recent) + 1
    up_slots = max(0, budget - reserved)
    up = upcoming[:up_slots]
    hidden = (len(upcoming) - len(up)) + max(0, len(completed) - len(recent))

    def add(m):
        if m.live:
            badge = Text("● LIVE", style="bold red")
            score = Text(f"{m.home.score}–{m.away.score}", style="bold bright_green")
            extra = Text(m.clock or m.detail, style="yellow")
        elif m.done:
            score = Text(f"{m.home.score}–{m.away.score}", style="bold grey85")
            pens = m.pens
            if pens:
                # Settled on penalties. Keep the scoreline clean and put the
                # shootout in the roomy right column (winner first) so it can't
                # get truncated out of the cramped name/score column.
                win_abbr, tally = pens
                badge = Text("PENS", style="bold magenta")
                extra = Text(f"{win_abbr} {tally}", style="bold magenta")
            elif m.aet:
                badge = Text("AET", style="bold cyan")
                extra = Text("a.e.t.", style="cyan")
            else:
                badge = Text("FT", style="grey74")
                extra = Text("", style="grey74")
        else:
            badge = Text("◷", style="green")
            score = Text("v", style="grey74")
            ko = m.kickoff_local()
            extra = Text(ko.strftime("%a %H:%M") if ko else m.countdown(), style="cyan")
        names = Text()
        names.append(f"{m.home.abbr}", style="bold white")
        names.append("  "); names.append_text(score); names.append("  ")
        names.append(f"{m.away.abbr}", style="bold white")
        tbl.add_row(names, badge, extra)

    for m in live: add(m)
    for m in up:   add(m)
    if hidden > 0:
        tbl.add_row(Text(f"▾ +{hidden} more · enlarge ↕",
                         style="italic yellow"), Text(""), Text(""))
    if recent and (live or up):
        tbl.add_row(Text("── recent results ──", style="grey50"), Text(""), Text(""))
    for m in recent: add(m)

    return Panel(tbl, title="[bold yellow]◤ RECENT & UPCOMING ◢[/]",
                 box=BOX, border_style="green", padding=(0, 1))

def _intval(s):
    try:    return int(s)
    except Exception: return None

def standings_panel(matches):
    """Live group tables computed from finished results (W=3, D=1)."""
    groups = {}
    for m in matches:
        if not m.done:
            continue
        hs, as_ = _intval(m.home.score), _intval(m.away.score)
        if hs is None or as_ is None:
            continue
        gk = m.group or "FINALS"
        tbl = groups.setdefault(gk, {})
        for side, gf, ga in ((m.home, hs, as_), (m.away, as_, hs)):
            row = tbl.setdefault(side.abbr, {"name": side.name, "P": 0, "W": 0,
                                             "D": 0, "L": 0, "GF": 0, "GA": 0, "Pts": 0})
            row["P"]  += 1
            row["GF"] += gf
            row["GA"] += ga
            if   gf > ga: row["W"] += 1; row["Pts"] += 3
            elif gf == ga: row["D"] += 1; row["Pts"] += 1
            else:          row["L"] += 1

    if not groups:
        body = Align.center(Text(
            "\n  Group tables publish after the first results.\n"
            "  Standings build live from full-time scores.\n",
            style="grey85", justify="center"), vertical="middle")
        return Panel(body, title="[bold yellow]◤ GROUP STANDINGS ◢[/]",
                     box=BOX, border_style="green", padding=(1, 1))

    blocks = []
    for gk in sorted(groups):
        t = Table(expand=True, box=None, padding=(0, 1))
        t.add_column(gk[:14] or "GROUP", style="bold white", no_wrap=True)
        for c in ("P", "W", "D", "L", "GF", "GA", "GD", "Pts"):
            t.add_column(c, justify="right",
                         style="bold bright_green" if c == "Pts" else "grey93")
        rows = sorted(groups[gk].values(),
                      key=lambda r: (r["Pts"], r["GF"] - r["GA"], r["GF"]), reverse=True)
        for i, r in enumerate(rows):
            mark = "[green]▲[/] " if i < 2 else "  "
            t.add_row(Text.from_markup(mark) + Text(r["name"][:12]),
                      str(r["P"]), str(r["W"]), str(r["D"]), str(r["L"]),
                      str(r["GF"]), str(r["GA"]),
                      f"{r['GF']-r['GA']:+d}", str(r["Pts"]))
        blocks.append(t)
    return Panel(Group(*blocks), title="[bold yellow]◤ GROUP STANDINGS ◢[/]",
                 box=BOX, border_style="green", padding=(0, 1))

_BRACKET_TEMPLATE = [
    "   ROUND OF 16        QUARTERS       SEMIS      FINAL",
    "  {r16_0:<14}┐",
    "                ├ {qf_0:<11}┐",
    "  {r16_1:<14}┘              │",
    "                              ├ {sf_0:<9}┐",
    "  {r16_2:<14}┐              │            │",
    "                ├ {qf_1:<11}┘            │",
    "  {r16_3:<14}┘                           ├══ 🏆 {champ}",
    "  {r16_4:<14}┐                           │",
    "                ├ {qf_2:<11}┐            │",
    "  {r16_5:<14}┘              │            │",
    "                              ├ {sf_1:<9}┘",
    "  {r16_6:<14}┐              │",
    "                ├ {qf_3:<11}┘",
    "  {r16_7:<14}┘",
]

def bracket_panel(matches):
    ko = {"Round Of 16": [], "Quarterfinal": [], "Semifinal": [], "Final": []}
    for m in matches:
        r = m.round
        key = "Round Of 16" if r.lower().startswith("round of 16") else r
        if key in ko:
            ko[key].append(f"{m.home.abbr} v {m.away.abbr}")
    slots = {}
    r16 = ko["Round Of 16"]
    for i in range(8):
        slots[f"r16_{i}"] = r16[i] if i < len(r16) else "·  TBD  ·"
    for i in range(4):
        slots[f"qf_{i}"] = (ko["Quarterfinal"][i] if i < len(ko["Quarterfinal"]) else "TBD")
    for i in range(2):
        slots[f"sf_{i}"] = (ko["Semifinal"][i] if i < len(ko["Semifinal"]) else "TBD")
    slots["champ"] = ko["Final"][0] if ko["Final"] else "FINAL"

    lines = Text()
    for i, raw in enumerate(_BRACKET_TEMPLATE):
        ln = raw.format(**slots)
        style = "bold yellow" if i == 0 else ("bold bright_green" if "🏆" in ln else "green")
        lines.append(ln + "\n", style=style)
    foot = Text("\n  FINAL · Sun Jul 19, 2026 · MetLife Stadium, New Jersey",
                style="grey85")
    return Panel(Group(lines, foot), title="[bold yellow]◤ ROAD TO THE FINAL ◢[/]",
                 box=BOX, border_style="bright_green", padding=(0, 1))

def news_wire_panel(news, tick):
    t = Text()
    t.append("\n")
    items = list(news) or ["Awaiting the wire — culture deck active.",
                           "RETRO WORLD CUP TERMINAL standing by."]
    n = len(items)
    start = (tick // 8) % n
    for k in range(min(n, 7)):
        head = items[(start + k) % n]
        bullet = "[bold red]▸[/] " if k == 0 else "[green]·[/] "
        t.append_text(Text.from_markup(bullet))
        t.append(head[:70] + ("…" if len(head) > 70 else "") + "\n\n", style="grey93")
    return Panel(t, title="[bold yellow]◤ NEWS WIRE ◢[/]",
                 box=BOX, border_style="green", padding=(0, 1))

def footer():
    t = Text(justify="center")
    t.append(" Ctrl-C quit ", style="black on bright_green")
    t.append("   data: ESPN fifa.world   ", style="grey74")
    t.append(" ▓▒░ retro phosphor build ░▒▓ ", style="green")
    return Align.center(t)

def build_layout(store, tick):
    matches, news, status, flashes, reddit, reddit_thread = store.snapshot()
    # rows available to the fixtures panel (top half of the right column)
    term_h = shutil.get_terminal_size((120, 40)).lines
    fix_budget = max(5, (term_h - 4) // 2 - 2)
    focus = next((x for x in matches if x.live), None) \
         or next((x for x in matches if not x.done), None) \
         or (matches[0] if matches else None)

    layout = Layout()
    layout.split_column(
        Layout(name="head", size=3),
        Layout(name="main", ratio=1),
        Layout(name="foot", size=1),
    )
    layout["main"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=1),
    )
    layout["left"].split_column(
        Layout(name="board", ratio=3),
        Layout(name="lower", ratio=2),
    )

    layout["head"].update(header(status, tick % 2 == 0))
    layout["foot"].update(footer())

    if focus:
        layout["board"].update(scoreboard_panel(focus))
    else:
        layout["board"].update(Panel(Align.center(
            Text("\n\nNo World Cup fixtures on the wire right now.\n"
                 "Showing culture deck — check back near match day.\n",
                 style="grey85", justify="center")),
            title="[bold yellow]◤ MATCH-CAST ◢[/]", box=BOX, border_style="bright_green"))

    # Stable 4-quadrant layout:
    #   lower-left  = Event Feed (live incidents, or a "waiting for kick-off" card)
    #   right-top   = Recent & Upcoming
    #   right-bottom= Fun Facts (always visible, rotating)
    layout["lower"].update(feed_panel(matches, flashes))
    layout["right"].split_column(
        Layout(fixtures_panel(matches, fix_budget), name="fix", ratio=1),
        Layout(buzz_panel(matches, news, reddit, reddit_thread, tick), name="rail", ratio=1),
    )
    return layout

# --------------------------------------------------------------------------- #
#  MAIN                                                                        #
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Retro World Cup terminal match-cast")
    ap.add_argument("--demo",  action="store_true", help="scripted fake live match")
    ap.add_argument("--once",  action="store_true", help="render one frame and exit")
    ap.add_argument("--plain", action="store_true", help="skip boot animation")
    args = ap.parse_args()

    set_taskbar_icon()
    if not args.once:
        set_console_font(height=22)  # a bit larger / easier to read (no maximize)

    # VT mode + non-legacy rendering = smooth, batched frames (kills conhost flicker)
    vt = enable_vt()
    console = Console(legacy_windows=False) if vt else Console()
    store = Store(demo=args.demo)

    if args.once:
        if args.demo:
            store._pull_demo()
        else:
            store._pull_live()
        console.print(build_layout(store, 0))
        return

    boot_sequence(console, args.plain)

    t = threading.Thread(target=store.run, daemon=True)
    t.start()
    # give the first fetch a beat so we don't flash an empty frame
    deadline = time.time() + 6
    while time.time() < deadline and store.status == "booting":
        time.sleep(0.2)

    tick = 0
    belled = set()
    try:
        # auto_refresh=False -> we control redraws (one per second). This stops
        # rich's refresh thread from competing with our updates, which is the
        # main cause of flicker in the classic console host.
        with Live(build_layout(store, tick), console=console, screen=True,
                  auto_refresh=False, transient=False) as live:
            while True:
                tick += 1
                # ring the terminal bell on a freshly detected goal
                for m, ev, ts in store.snapshot()[3]:
                    sig = (m.id, ev.clock, ev.type, tuple(ev.players))
                    if "goal" in ev.type.lower() and sig not in belled:
                        belled.add(sig)
                        if time.time() - ts < 5:
                            live.console.bell()
                live.update(build_layout(store, tick), refresh=True)
                time.sleep(1.0)
    except KeyboardInterrupt:
        console.print("\n[bright_green]FEED CLOSED — full time. ⚽[/]")

if __name__ == "__main__":
    main()
