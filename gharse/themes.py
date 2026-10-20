# themes.py — every colour GharSe paints, as data.
#
# A theme is a set of tokens. css_for() pours those tokens into one stylesheet
# template, so the template never names a colour and adding a theme is adding a dict.
# The default, "saffron-night", reproduces the original amber-on-near-black look
# exactly: the template is that stylesheet with each literal colour replaced by the
# token it stood for.
#
# Token reference (every theme carries every one of these):
#   bg, bg2, glow        page ground: base, gradient middle, top-right glow
#   sidebar, sidebar2    sidebar ground, top and bottom of its gradient
#   sb_txt, sb_txt2      sidebar primary / secondary text
#   sb_input, sb_input2  input ground and step/radio ground inside the sidebar
#   sb_line, sb_accent   rule colour and checked-mark colour inside the sidebar
#   sb_accent2, sb_brand_color  highlight text and brand-mark colour inside the sidebar
#   sb_btn_bg, sb_btn_txt  button fill and label inside the sidebar
#   panel, panel2        bordered-container ground, table ground (may be translucent)
#   line                 hairlines and borders
#   accent, accent_deep  primary colour; deep end of the button gradient
#   accent2              highlight text: brand mark, pills, links, step arrows
#   accent_rgb, hi_rgb   "r,g,b" for accent tints (hover, glows) and highlight tints
#                        (pills, KPI wash, table header, bar track)
#   txt, txt2, muted     primary, secondary (labels, subtitles), muted text
#   strong               big numbers (.price, KPI values)
#   input, input2, popover  input ground, step-button/radio ground, dropdown ground
#   btn_bg, btn_txt      button fill (any CSS background) and its label colour
#   bar_bg               fill of the horizontal bar chart
#   brand_fill, brand_color  brand-mark accent: background (clipped to text) + colour
#   ok, ok_rgb, warn, warn_rgb  status colours (text) and their tint channels
#   shadow, shadow2      card shadow colour, KPI shadow colour

from urllib.parse import quote

import streamlit as st

DEFAULT_THEME = "saffron-night"
GROUPS = ["Dark", "Light", "Rainbow", "Two-tone", "Flowers", "Mono"]

GROUP_BLURBS = {
    "Dark": "Easy on the eyes after sunset.",
    "Light": "Bright and clear in daylight.",
    "Rainbow": "Every colour of Holi at once.",
    "Two-tone": "Two colours that share the work.",
    "Flowers": "A soft scatter of your favourite flower behind the page.",
    "Mono": "Pure black and white, nothing else.",
}

TOKEN_KEYS = [
    "bg", "bg2", "glow", "sidebar", "sidebar2",
    "sb_txt", "sb_txt2", "sb_input", "sb_input2", "sb_line", "sb_accent", "sb_accent2", "sb_brand_color",
    "sb_btn_bg", "sb_btn_txt",
    "panel", "panel2", "line",
    "accent", "accent_deep", "accent2", "accent_rgb", "hi_rgb",
    "txt", "txt2", "muted", "strong",
    "input", "input2", "popover",
    "btn_bg", "btn_txt", "bar_bg", "brand_fill", "brand_color",
    "ok", "ok_rgb", "warn", "warn_rgb", "shadow", "shadow2",
]

RAINBOW = ["#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c", "#4dabf7", "#b197fc"]


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def _theme(key, name, group, dark, sticker=None, sticker_tint=None, **t):
    """Fill the derivable tokens so every theme ends up with the complete set."""
    t.setdefault("bg2", t["bg"])
    t.setdefault("glow", t["bg2"])
    t.setdefault("sidebar2", t["bg"])
    t.setdefault("accent_rgb", _rgb(t["accent"]))
    t.setdefault("hi_rgb", t["accent_rgb"])
    t.setdefault("muted", t["txt2"])
    t.setdefault("popover", t["sidebar"])
    t.setdefault("btn_bg", f"linear-gradient(135deg, {t['accent_deep']} 0%, {t['accent']} 100%)")
    t.setdefault("bar_bg", f"linear-gradient(90deg,{t['accent_deep']},{t['accent2']})")
    t.setdefault("brand_fill", "none")
    t.setdefault("brand_color", t["accent2"])
    t.setdefault("ok_rgb", _rgb(t["ok"]))
    t.setdefault("warn_rgb", _rgb(t["warn"]))
    t.setdefault("shadow", "rgba(0,0,0,0.4)" if dark else "rgba(30,25,20,0.10)")
    t.setdefault("shadow2", "rgba(0,0,0,.35)" if dark else "rgba(30,25,20,.08)")
    t.setdefault("panel2", "rgba(255,255,255,.03)" if dark else "rgba(255,255,255,.7)")
    # The sidebar is a sub-theme; by default it simply repeats the page's own tokens.
    t.setdefault("sb_txt", t["txt"])
    t.setdefault("sb_txt2", t["txt2"])
    t.setdefault("sb_input", t["input"])
    t.setdefault("sb_input2", t["input2"])
    t.setdefault("sb_line", t["line"])
    t.setdefault("sb_accent", t["accent"])
    t.setdefault("sb_accent2", t["accent2"])
    t.setdefault("sb_brand_color", t["brand_color"])
    t.setdefault("sb_btn_bg", t["btn_bg"])
    t.setdefault("sb_btn_txt", t["btn_txt"])
    missing = [k for k in TOKEN_KEYS if k not in t]
    if missing:
        raise ValueError(f"theme {key} is missing tokens: {missing}")
    theme = {"key": key, "name": name, "group": group, "dark": dark,
             "tokens": {k: t[k] for k in TOKEN_KEYS}, "sticker": sticker}
    if sticker:
        theme["sticker_tint"] = sticker_tint
    return theme


THEMES = [
    # ------------------------------------------------------------------ Dark
    _theme("saffron-night", "Saffron Night", "Dark", True,
           bg="#0d0a07", bg2="#14100a", glow="#2a1a08", sidebar="#171008", sidebar2="#0d0a07",
           panel="rgba(255,255,255,0.04)", line="rgba(255,166,43,0.22)",
           accent="#ffa62b", accent_deep="#f08b1d", accent2="#ffc46b",
           txt="#f6efe6", txt2="#c2a883", strong="#fff",
           input="#1b1409", input2="#241a0c", popover="#171008",
           btn_txt="#20160a", bar_bg="linear-gradient(90deg,#f08b1d,#ffc46b)",
           ok="#3ddc97", warn="#ff8b8b", warn_rgb="255,99,99"),
    _theme("indigo-dusk", "Indigo Dusk", "Dark", True,
           bg="#0b0c16", bg2="#11132a", glow="#1f2352", sidebar="#13163a", sidebar2="#0b0c16",
           panel="rgba(255,255,255,0.04)", line="rgba(139,147,255,0.24)",
           accent="#8b93ff", accent_deep="#6c72f5", accent2="#b9bdff",
           txt="#eceefe", txt2="#a9aedc", strong="#ffffff",
           input="#161936", input2="#1e2246", popover="#13163a",
           btn_txt="#0b0c24", ok="#4ade80", warn="#fb8f8f", warn_rgb="248,113,113"),
    _theme("emerald-grove", "Emerald Grove", "Dark", True,
           bg="#06100c", bg2="#0a1812", glow="#0f3022", sidebar="#0c1b14", sidebar2="#06100c",
           panel="rgba(255,255,255,0.04)", line="rgba(52,211,153,0.22)",
           accent="#34d399", accent_deep="#10b981", accent2="#7ee8c0",
           txt="#e8f6ef", txt2="#9cc7b3", strong="#ffffff",
           input="#0f2018", input2="#15291f", popover="#0c1b14",
           btn_txt="#03140c", ok="#a3e635", warn="#fb8f8f", warn_rgb="248,113,113"),
    _theme("plum-velvet", "Plum Velvet", "Dark", True,
           bg="#110a12", bg2="#1a0f1c", glow="#3a1840", sidebar="#1d0f20", sidebar2="#110a12",
           panel="rgba(255,255,255,0.04)", line="rgba(232,121,249,0.22)",
           accent="#e879f9", accent_deep="#c94fe0", accent2="#f3b4fc",
           txt="#f9eefa", txt2="#cda9d0", strong="#ffffff",
           input="#221325", input2="#2c1830", popover="#1d0f20",
           btn_txt="#1e0622", ok="#4ade80", warn="#fb8f8f", warn_rgb="248,113,113"),
    _theme("teal-tide", "Teal Tide", "Dark", True,
           bg="#061012", bg2="#0a181b", glow="#0c3036", sidebar="#0b1c1f", sidebar2="#061012",
           panel="rgba(255,255,255,0.04)", line="rgba(45,212,191,0.22)",
           accent="#2dd4bf", accent_deep="#14a89a", accent2="#99f6e4",
           txt="#e6f7f6", txt2="#93c4c0", strong="#ffffff",
           input="#0e2225", input2="#132c30", popover="#0b1c1f",
           btn_txt="#031413", ok="#86efac", warn="#fb8f8f", warn_rgb="248,113,113"),

    # ------------------------------------------------------------------ Light
    _theme("morning-chai", "Morning Chai", "Light", False,
           bg="#fbf6ee", bg2="#f6eee1", glow="#fde4bd", sidebar="#f3e8d6", sidebar2="#fbf6ee",
           panel="rgba(255,255,255,0.78)", line="rgba(154,82,0,0.22)",
           accent="#b45309", accent_deep="#92400e", accent2="#9a3412",
           txt="#2a1d10", txt2="#6b5440", strong="#1a1008",
           input="#ffffff", input2="#f3e8d6", popover="#ffffff",
           btn_txt="#ffffff", ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("monsoon-mist", "Monsoon Mist", "Light", False,
           bg="#f3f6fa", bg2="#eaf0f7", glow="#d8e6fb", sidebar="#e4ecf6", sidebar2="#f3f6fa",
           panel="rgba(255,255,255,0.8)", line="rgba(29,78,216,0.18)",
           accent="#1d4ed8", accent_deep="#1e40af", accent2="#1e3a8a",
           txt="#0f1b2d", txt2="#475a73", strong="#0b1424",
           input="#ffffff", input2="#e4ecf6", popover="#ffffff",
           btn_txt="#ffffff", ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("mint-leaf", "Mint Leaf", "Light", False,
           bg="#f2f8f4", bg2="#e8f3ec", glow="#d2eedc", sidebar="#e2f0e7", sidebar2="#f2f8f4",
           panel="rgba(255,255,255,0.8)", line="rgba(4,120,87,0.20)",
           accent="#047857", accent_deep="#065f46", accent2="#065f46",
           txt="#0f2318", txt2="#476153", strong="#08170f",
           input="#ffffff", input2="#e2f0e7", popover="#ffffff",
           btn_txt="#ffffff", ok="#14532d", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("rose-quartz", "Rose Quartz", "Light", False,
           bg="#fcf4f6", bg2="#f8e9ee", glow="#fbd6e1", sidebar="#f6e3ea", sidebar2="#fcf4f6",
           panel="rgba(255,255,255,0.8)", line="rgba(190,24,93,0.18)",
           accent="#be185d", accent_deep="#9d174d", accent2="#9d174d",
           txt="#2b1119", txt2="#6e4854", strong="#1f0a11",
           input="#ffffff", input2="#f6e3ea", popover="#ffffff",
           btn_txt="#ffffff", ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("lavender-sky", "Lavender Sky", "Light", False,
           bg="#f6f4fc", bg2="#ece8f8", glow="#e0d8fb", sidebar="#eae5f8", sidebar2="#f6f4fc",
           panel="rgba(255,255,255,0.8)", line="rgba(109,40,217,0.18)",
           accent="#6d28d9", accent_deep="#5b21b6", accent2="#5b21b6",
           txt="#1c1530", txt2="#574d72", strong="#120c22",
           input="#ffffff", input2="#eae5f8", popover="#ffffff",
           btn_txt="#ffffff", ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),

    # ------------------------------------------------------------------ Rainbow
    _theme("holi", "Holi", "Rainbow", True,
           bg="#141317", bg2="#1a1920", glow="#2b2233", sidebar="#1b1a21", sidebar2="#141317",
           panel="rgba(255,255,255,0.045)", line="rgba(255,255,255,0.14)",
           accent="#ff8fab", accent_deep="#ff6b6b", accent2="#ffd43b",
           hi_rgb="177,151,252",
           txt="#f5f3f7", txt2="#bbb5c6", strong="#ffffff",
           input="#1f1d25", input2="#28252f", popover="#1b1a21",
           btn_txt="#16121c",
           btn_bg="linear-gradient(135deg, " + ", ".join(
               f"{c} {i * 20}%" for i, c in enumerate(RAINBOW)) + ")",
           bar_bg="linear-gradient(90deg," + ",".join(RAINBOW) + ")",
           brand_fill="linear-gradient(90deg," + ",".join(RAINBOW) + ")",
           brand_color="transparent",
           ok="#69db7c", warn="#ff8f8f", warn_rgb="255,107,107"),

    # ------------------------------------------------------------------ Two-tone
    # Buttons carry the first colour; pills, the brand mark, KPI washes, table
    # headers and the bar chart carry the second.
    _theme("teal-coral", "Teal × Coral", "Two-tone", False,
           bg="#f4f8f8", bg2="#ecf4f3", glow="#ffe1d8", sidebar="#0f3d3e", sidebar2="#0b2e2f",
           sb_txt="#e9f5f4", sb_txt2="#a9cfcc", sb_input="#14504f", sb_input2="#1a5c5b",
           sb_line="rgba(255,138,112,0.35)", sb_accent="#ff8a70", sb_accent2="#ffb3a3",
           sb_brand_color="#ff9d85",
           sb_btn_bg="linear-gradient(135deg, #ff7a5c 0%, #ff9d85 100%)", sb_btn_txt="#2a0c05",
           panel="rgba(255,255,255,0.85)", line="rgba(15,118,110,0.22)",
           accent="#0f766e", accent_deep="#115e59", accent2="#b9381c", hi_rgb="255,111,89",
           txt="#102a2b", txt2="#45605f", strong="#0a1f20",
           input="#ffffff", input2="#e3efee", popover="#ffffff",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#0f766e,#ff6f59)",
           ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("navy-gold", "Navy × Gold", "Two-tone", True,
           bg="#0a1020", bg2="#0e1730", glow="#1b2c5c", sidebar="#0e1834", sidebar2="#0a1020",
           panel="rgba(255,255,255,0.04)", line="rgba(245,197,66,0.24)",
           accent="#f5c542", accent_deep="#d9a514", accent2="#a9c1ff", hi_rgb="120,150,255",
           txt="#eef2fb", txt2="#a7b3d1", strong="#ffffff",
           input="#121d3c", input2="#18254a", popover="#0e1834",
           btn_txt="#1a1405", bar_bg="linear-gradient(90deg,#f5c542,#7896ff)",
           brand_color="#f5c542",
           ok="#4ade80", warn="#fb8f8f", warn_rgb="248,113,113"),
    _theme("indigo-lime", "Indigo × Lime", "Two-tone", True,
           bg="#0c0b1d", bg2="#131232", glow="#25215e", sidebar="#151340", sidebar2="#0c0b1d",
           panel="rgba(255,255,255,0.04)", line="rgba(129,124,255,0.28)",
           accent="#6f6cf6", accent_deep="#5149e0", accent2="#c3f169", hi_rgb="190,242,100",
           txt="#efeffd", txt2="#aeacd6", strong="#ffffff",
           input="#17153d", input2="#1f1c4c", popover="#151340",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#5149e0,#bef264)",
           ok="#4ade80", warn="#fb8f8f", warn_rgb="248,113,113"),
    _theme("purple-orange", "Purple × Orange", "Two-tone", False,
           bg="#faf7fd", bg2="#f3edfa", glow="#ffe3cc", sidebar="#3b1361", sidebar2="#2a0d47",
           sb_txt="#f6eefe", sb_txt2="#cdb6e6", sb_input="#4a1d75", sb_input2="#552284",
           sb_line="rgba(251,146,60,0.38)", sb_accent="#fb923c", sb_accent2="#fdba74",
           sb_brand_color="#fdba74",
           sb_btn_bg="linear-gradient(135deg, #f97316 0%, #fb923c 100%)", sb_btn_txt="#2a1003",
           panel="rgba(255,255,255,0.85)", line="rgba(126,34,206,0.18)",
           accent="#7e22ce", accent_deep="#6b21a8", accent2="#b93d0b", hi_rgb="249,115,22",
           txt="#1f1230", txt2="#5b4a70", strong="#150a22",
           input="#ffffff", input2="#efe7f8", popover="#ffffff",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#7e22ce,#f97316)",
           ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),

    # ------------------------------------------------------------------ Flowers
    _theme("mallige", "Mallige (Jasmine)", "Flowers", False,
           sticker="💮", sticker_tint="#f4a6c8",
           bg="#fbfcf7", bg2="#f4f7ec", glow="#ffffff", sidebar="#edf3e2", sidebar2="#fbfcf7",
           panel="rgba(255,255,255,0.85)", line="rgba(63,98,18,0.20)",
           accent="#3f6212", accent_deep="#365314", accent2="#3f6212",
           txt="#1c2413", txt2="#4f5d40", strong="#141a0d",
           input="#ffffff", input2="#edf3e2", popover="#ffffff",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#3f6212,#a3c46b)",
           ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("marigold", "Marigold", "Flowers", False,
           sticker="🏵️", sticker_tint="#f59e0b",
           bg="#fff9ef", bg2="#fff1d8", glow="#ffdf9e", sidebar="#fdebc8", sidebar2="#fff9ef",
           panel="rgba(255,255,255,0.82)", line="rgba(194,94,0,0.24)",
           accent="#b45309", accent_deep="#9a4405", accent2="#9a3c00",
           txt="#2d1a05", txt2="#6a4a26", strong="#1f1203",
           input="#ffffff", input2="#fdebc8", popover="#ffffff",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#c25e00,#ffb627)",
           ok="#065f46", warn="#b91c1c", warn_rgb="220,38,38"),
    _theme("lotus", "Lotus", "Flowers", True,
           sticker="🪷", sticker_tint="#f472b6",
           bg="#0b1210", bg2="#0f1916", glow="#3a1530", sidebar="#0f1a17", sidebar2="#0b1210",
           panel="rgba(255,255,255,0.04)", line="rgba(244,114,182,0.24)",
           accent="#f472b6", accent_deep="#ec4899", accent2="#f9b4d9",
           txt="#f5eef2", txt2="#b6c8bf", strong="#ffffff",
           input="#13201c", input2="#192a25", popover="#0f1a17",
           btn_txt="#1f0614", bar_bg="linear-gradient(90deg,#ec4899,#86efac)",
           ok="#86efac", warn="#fca5a5", warn_rgb="248,113,113"),
    _theme("hibiscus", "Hibiscus", "Flowers", True,
           sticker="🌺", sticker_tint="#e11d48",
           bg="#14070a", bg2="#1c0a0e", glow="#3d0c18", sidebar="#200b10", sidebar2="#14070a",
           panel="rgba(255,255,255,0.04)", line="rgba(255,77,109,0.24)",
           accent="#ff4d6d", accent_deep="#e11d48", accent2="#ffb3c1",
           txt="#fbeef0", txt2="#d4a9b0", strong="#ffffff",
           input="#251015", input2="#30141b", popover="#200b10",
           btn_txt="#1f0007", bar_bg="linear-gradient(90deg,#e11d48,#ffb3c1)",
           ok="#86efac", warn="#fdba74", warn_rgb="251,146,60"),

    # ------------------------------------------------------------------ Mono
    _theme("ink-and-paper", "Ink & Paper", "Mono", False,
           bg="#ffffff", bg2="#fafafa", glow="#f0f0f0", sidebar="#000000", sidebar2="#0a0a0a",
           sb_txt="#ffffff", sb_txt2="#bdbdbd", sb_input="#1a1a1a", sb_input2="#262626",
           sb_line="rgba(255,255,255,0.24)", sb_accent="#ffffff", sb_accent2="#ffffff",
           sb_brand_color="#ffffff",
           sb_btn_bg="linear-gradient(135deg, #e6e6e6 0%, #ffffff 100%)", sb_btn_txt="#000000",
           panel="rgba(0,0,0,0.02)", panel2="rgba(0,0,0,.015)", line="rgba(0,0,0,0.16)",
           accent="#000000", accent_deep="#2b2b2b", accent2="#000000", hi_rgb="0,0,0",
           txt="#0a0a0a", txt2="#4d4d4d", muted="#595959", strong="#000000",
           input="#ffffff", input2="#efefef", popover="#ffffff",
           btn_txt="#ffffff", bar_bg="linear-gradient(90deg,#000000,#7a7a7a)",
           ok="#111111", ok_rgb="0,0,0", warn="#595959", warn_rgb="90,90,90",
           shadow="rgba(0,0,0,0.08)", shadow2="rgba(0,0,0,.06)"),
]

_BY_KEY = {t["key"]: t for t in THEMES}


def get(key):
    """The theme for `key`; the default for an unknown, empty or None key."""
    return _BY_KEY.get(key or "", _BY_KEY[DEFAULT_THEME])


# --------------------------------------------------------------------------- stylesheet
# Ported rule for rule from app.py's original inject_css(); each literal colour became
# the token it stood for. Order and selectors are unchanged.
_BASE_CSS = """
.stApp {
    background: radial-gradient(1100px 560px at 82% -12%, var(--glow) 0%, transparent 55%),
                linear-gradient(160deg, var(--bg) 0%, var(--bg2) 52%, var(--bg) 100%);
    color: var(--txt);
}
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--sidebar) 0%, var(--sidebar2) 100%);
    border-right: 1px solid var(--line);
}
section[data-testid="stSidebar"] * { color: var(--txt); }
h1,h2,h3,h4,h5,h6, p, span, label, div { color: var(--txt); }
.block-container { padding-top: 1.4rem; }

/* The app header sits above everything and ships white — paint it out. */
header[data-testid="stHeader"] { background:transparent !important; }
[data-testid="stToolbar"] { display:none !important; }

.stTextInput input, .stTextArea textarea, .stDateInput input,
div[data-baseweb="select"] > div {
    background:var(--input) !important; color:var(--txt) !important;
    border:1px solid var(--line) !important; border-radius:10px !important;
}
.stTextInput input:focus, .stTextArea textarea:focus { border-color:var(--accent) !important; }

/* Current Streamlit builds render selects, number inputs and toggles through
   react-aria rather than baseweb, so these need their own rules. */
[data-testid="stSelectbox"] div[role="group"],
[data-testid="stMultiSelect"] div[role="group"],
[data-testid="stNumberInputContainer"],
[data-testid="stDateInput"] div[role="group"] {
    background:var(--input) !important; border:1px solid var(--line) !important;
    border-radius:10px !important; color:var(--txt) !important;
}
[data-testid="stSelectbox"] input, [data-testid="stMultiSelect"] input,
[data-testid="stNumberInputField"], [data-testid="stDateInput"] input {
    background:transparent !important; color:var(--txt) !important; border:0 !important;
}
[data-testid="stSelectbox"] div[role="group"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within { border-color:var(--accent) !important; }
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] {
    background:var(--input2) !important; color:var(--accent2) !important; border:0 !important;
}
/* Dropdown menus render in a portal, outside .stApp */
.react-aria-Popover, [role="listbox"], [data-testid="stSelectboxVirtualDropdown"] {
    background:var(--popover) !important; border:1px solid var(--line) !important; color:var(--txt) !important;
}
[role="option"] { color:var(--txt) !important; }
[role="option"][data-focused="true"], [role="option"]:hover {
    background:rgba(var(--accent-rgb),.16) !important;
}
/* Checkbox / radio marks default to Streamlit red; make them the theme accent */
[data-testid="stCheckbox"] label[data-selected="true"] div:has(> svg),
[data-testid="stCheckbox"] label[data-checked="true"] div:has(> svg) {
    background:var(--accent) !important; border-color:var(--accent) !important;
}
[data-testid="stCheckbox"] label div:has(> svg) svg polyline { stroke:var(--btn-txt) !important; }
/* Radio: the dot is label > div > div > div:first-child, with the fill inside it */
[data-testid="stRadioOption"] { background:transparent !important; }
[data-testid="stRadioOption"] > div > div > div:first-child {
    background:var(--input2) !important; border-color:var(--line) !important;
}
[data-testid="stRadioOption"][data-selected="true"] > div > div > div:first-child {
    border-color:var(--accent) !important;
}
[data-testid="stRadioOption"][data-selected="true"] > div > div > div:first-child > div {
    background:var(--accent) !important;
}
/* Streamlit's heading anchor-link icons */
[data-testid="stHeaderActionElements"] { display:none !important; }
/* The text input's border lives on the wrapper, not the input */
[data-testid="stTextInputRootElement"] {
    background:var(--input) !important; border:1px solid var(--line) !important; border-radius:10px !important;
}
[data-testid="stTextInputRootElement"]:focus-within { border-color:var(--accent) !important; }
[data-testid="stTextInputRootElement"] input { background:transparent !important; border:0 !important; }

.stButton>button, .stDownloadButton>button, .stForm button[kind="primaryFormSubmit"],
[data-testid="stFormSubmitButton"] button, [data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-secondaryFormSubmit"],
[data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--btn-bg);
    color:var(--btn-txt); border:0; border-radius:10px; font-weight:700;
    box-shadow:0 6px 18px rgba(var(--accent-rgb),0.22); transition:.15s;
}
.stButton>button:hover, .stDownloadButton>button:hover { filter:brightness(1.08); transform:translateY(-1px); }

/* Link buttons are anchors, not buttons, and ship unstyled */
[data-testid="stLinkButton"] a, [data-testid="stBaseLinkButton-secondary"],
[data-testid="stBaseLinkButton-primary"] {
    background: var(--btn-bg) !important;
    border:0 !important; border-radius:10px !important; font-weight:700 !important;
    box-shadow:0 6px 18px rgba(var(--accent-rgb),0.22);
}
/* Streamlit paints the label in a nested element, so set it there too */
.stButton button p, .stButton button div, [data-testid="stFormSubmitButton"] button p,
[data-testid="stLinkButton"] a p, [data-testid="stLinkButton"] a div,
[data-testid="stBaseButton-secondary"] p, [data-testid="stBaseButton-primary"] p {
    color:var(--btn-txt) !important; font-weight:700 !important;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--panel); border:1px solid var(--line) !important;
    border-radius:14px; box-shadow:0 8px 26px var(--shadow);
}
.stTabs [data-baseweb="tab-list"] { gap:6px; }
.stTabs [data-baseweb="tab"] { background:var(--input); border-radius:8px 8px 0 0; padding:8px 16px; }
.streamlit-expanderHeader, details summary { color:var(--txt) !important; }

.brand { font-size:1.35rem; font-weight:800; letter-spacing:.3px; }
.brand .accent { color:var(--brand-color); background:var(--brand-fill);
                 -webkit-background-clip:text; background-clip:text; }
.brand-sub { font-size:.7rem; letter-spacing:3px; color:var(--txt2); margin-top:-4px; }
.page-title { font-size:1.7rem; font-weight:800; margin-bottom:.2rem; }
.page-sub { color:var(--txt2); margin-bottom:1rem; }
.kpi { background:linear-gradient(160deg, rgba(var(--hi-rgb),.14), rgba(var(--hi-rgb),.03));
       border:1px solid var(--line); border-radius:16px; padding:16px 18px;
       box-shadow:0 8px 26px var(--shadow2); height:100%; }
.kpi .k-label { font-size:.76rem; color:var(--txt2); text-transform:uppercase; letter-spacing:1px; }
.kpi .k-val { font-size:1.85rem; font-weight:800; margin-top:6px; color:var(--strong); }
.pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:.72rem;
        border:1px solid var(--line); background:rgba(var(--hi-rgb),.12); color:var(--accent2);
        margin:2px 4px 2px 0; }
.pill.ok { border-color:rgba(var(--ok-rgb),.4); background:rgba(var(--ok-rgb),.12); color:var(--ok); }
.pill.warn { border-color:rgba(var(--warn-rgb),.4); background:rgba(var(--warn-rgb),.12); color:var(--warn); }
.price { font-size:1.5rem; font-weight:800; color:var(--strong); }
.save { color:var(--ok); font-weight:700; font-size:.85rem; }
.muted { color:var(--muted); font-size:.85rem; }

/* Tables and bars are rendered as HTML so they follow this theme instead of
   Streamlit's own one. */
.tbl-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:12px;
            background:var(--panel2); }
table.tbl { border-collapse:collapse; width:100%; font-size:.86rem; }
table.tbl th, table.tbl td { text-align:left; padding:9px 14px;
            border-bottom:1px solid rgba(var(--hi-rgb),.12); white-space:nowrap; }
table.tbl th { font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
            color:var(--txt2); background:rgba(var(--hi-rgb),.06); font-weight:600; }
table.tbl tbody tr:last-child td { border-bottom:0; }
table.tbl td.num { text-align:right; font-variant-numeric:tabular-nums; }
.hb { display:grid; grid-template-columns:130px 1fr 46px; gap:12px; align-items:center;
      margin-bottom:9px; }
.hb-l { font-size:.84rem; color:var(--txt2); overflow:hidden; text-overflow:ellipsis; }
.hb-t { background:rgba(var(--hi-rgb),.08); border-radius:999px; height:14px; overflow:hidden; }
.hb-f { background:var(--bar-bg); height:100%; border-radius:999px; }
.hb-v { font-size:.84rem; font-variant-numeric:tabular-nums; text-align:right; }
.split { font-size:.8rem; color:var(--txt2); border-top:1px dashed var(--line); margin-top:8px; padding-top:6px; }
#MainMenu, footer {visibility:hidden;}
"""

# The sidebar is its own small theme: inside it the shared variables take the sb_*
# values, so every rule above (inputs, radios, buttons) follows without duplication.
_SIDEBAR_SCOPE = """
section[data-testid="stSidebar"] {
    --txt:var(--sb-txt); --txt2:var(--sb-txt2); --muted:var(--sb-txt2); --strong:var(--sb-txt);
    --input:var(--sb-input); --input2:var(--sb-input2); --line:var(--sb-line);
    --accent:var(--sb-accent); --accent2:var(--sb-accent2); --brand-color:var(--sb-brand-color);
    --btn-bg:var(--sb-btn-bg); --btn-txt:var(--sb-btn-txt);
}
"""

# .streamlit/config.toml paints Streamlit's own widgets in the default theme's colours.
# Any other theme repaints the native parts the base stylesheet never needed to reach.
_NATIVE_CSS = """
.stMarkdown a, [data-testid="stMarkdownContainer"] a:not([data-testid]) { color:var(--accent2); }
hr { border-color:var(--line) !important; }
[data-testid="stForm"] { border-color:var(--line) !important; }
[data-testid="stTextAreaRootElement"] { background:transparent !important; }
::placeholder { color:var(--muted) !important; opacity:.75 !important; }
[data-testid="stCheckbox"] label div:has(> svg) { background:var(--input); border-color:var(--line); }
[data-testid="stFileUploaderDropzone"] {
    background:var(--input) !important; border:1px dashed var(--line) !important; border-radius:12px;
}
[data-testid="stFileUploaderDropzone"] svg { color:var(--txt2) !important; }
[data-testid="stExpander"] details { background:var(--panel); border-color:var(--line) !important; }
[data-testid="stExpander"] summary svg { color:var(--txt2) !important; }
[data-testid="stExpander"] summary:hover p { color:var(--accent2) !important; }
[data-testid="stTab"] p { color:var(--txt2); }
[data-testid="stTab"][aria-selected="true"] p { color:var(--accent2) !important; }
[data-testid="stTooltipContent"], [data-testid="stToast"], [data-testid="stPopoverBody"],
[data-testid="stDialog"] div[role="dialog"] {
    background:var(--popover) !important; color:var(--txt) !important; border:1px solid var(--line);
}
header[data-testid="stHeader"] button, header[data-testid="stHeader"] svg,
[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapseButton"] button {
    color:var(--txt) !important;
}
[data-testid="stCode"], .stMarkdown code { background:var(--input2) !important; color:var(--txt) !important; }
"""

# Sticker themes: a faint tile of the flower behind everything. The layer is a fixed
# pseudo-element at z-index -1 inside .stApp's own stacking context, so it paints above
# the ground and below every piece of content, and it never takes a click.
_STICKER_CSS = """
.stApp { isolation:isolate; }
.stApp::before {
    content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
    background-image:var(--sticker-tile); background-size:168px 168px; background-repeat:repeat;
}
.brand::after { content:var(--sticker-mark); margin-left:.35rem; font-size:.9em; pointer-events:none; }
"""

# Swatch cards for theme_picker(). Structure only; every colour is set per swatch.
_PICKER_CSS = """
.th-sw { display:flex; height:92px; border-radius:12px; overflow:hidden; border:1px solid var(--line);
         margin-bottom:8px; }
.th-sw-side { width:26%; display:flex; flex-direction:column; gap:5px; padding:9px 7px; }
.th-sw-side i { display:block; height:5px; border-radius:3px; opacity:.85; }
.th-sw-main { flex:1; padding:8px 10px; display:flex; flex-direction:column; justify-content:space-between;
              background-size:84px 84px; }
.th-sw-brand { font-weight:800; font-size:.95rem; line-height:1.1; }
.th-sw-row { display:flex; align-items:center; gap:5px; }
.th-sw-dot { width:12px; height:12px; border-radius:50%; display:inline-block; }
.th-sw-btn { font-size:.66rem; font-weight:700; padding:2px 8px; border-radius:6px; margin-left:auto; }
.th-name { font-weight:700; font-size:.92rem; }
"""

SWATCH_OPACITY = 0.13  # how strongly a sticker tile shows; kept faint so text never suffers


def sticker_tile(emoji, size=168, opacity=SWATCH_OPACITY):
    """An inline-SVG data URI tiling `emoji` twice per tile, ready for url(...)."""
    a, b = size * 0.12, size * 0.62
    fs = size * 0.17
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' "
           f"viewBox='0 0 {size} {size}'><g opacity='{opacity}' font-size='{fs:.0f}' "
           f"font-family='Apple Color Emoji,Segoe UI Emoji,Noto Color Emoji,sans-serif'>"
           f"<text x='{a:.0f}' y='{a + fs:.0f}'>{emoji}</text>"
           f"<text x='{b:.0f}' y='{b + fs:.0f}' transform='rotate(-14 {b + fs / 2:.0f} {b + fs / 2:.0f})'>"
           f"{emoji}</text></g></svg>")
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


def _root_vars(theme):
    t = theme["tokens"]
    names = {
        "bg": "bg", "bg2": "bg2", "glow": "glow", "sidebar": "sidebar", "sidebar2": "sidebar2",
        "sb_txt": "sb-txt", "sb_txt2": "sb-txt2", "sb_input": "sb-input", "sb_input2": "sb-input2",
        "sb_line": "sb-line", "sb_accent": "sb-accent", "sb_accent2": "sb-accent2",
        "sb_brand_color": "sb-brand-color", "sb_btn_bg": "sb-btn-bg", "sb_btn_txt": "sb-btn-txt",
        "panel": "panel", "panel2": "panel2", "line": "line",
        "accent": "accent", "accent_deep": "accent-deep", "accent2": "accent2",
        "accent_rgb": "accent-rgb", "hi_rgb": "hi-rgb",
        "txt": "txt", "txt2": "txt2", "muted": "muted", "strong": "strong",
        "input": "input", "input2": "input2", "popover": "popover",
        "btn_bg": "btn-bg", "btn_txt": "btn-txt", "bar_bg": "bar-bg",
        "brand_fill": "brand-fill", "brand_color": "brand-color",
        "ok": "ok", "ok_rgb": "ok-rgb", "warn": "warn", "warn_rgb": "warn-rgb",
        "shadow": "shadow", "shadow2": "shadow2",
    }
    decl = [f"--{css}:{t[k]};" for k, css in names.items()]
    # Names the original stylesheet used, kept so nothing written against them breaks.
    decl += ["--bg0:var(--bg);", "--amber:var(--accent);", "--amber2:var(--accent2);", "--mint:var(--ok);"]
    if theme.get("sticker"):
        decl.append(f"--sticker-tile:url(\"{sticker_tile(theme['sticker'])}\");")
        decl.append(f"--sticker-mark:\"{theme['sticker']}\";")
    return ":root {\n    " + "\n    ".join(decl) + "\n}\n"


def css_for(key):
    """The complete <style> block for theme `key`, for st.markdown(..., unsafe_allow_html=True)."""
    theme = get(key)
    parts = [_root_vars(theme), _BASE_CSS, _SIDEBAR_SCOPE]
    if theme["key"] != DEFAULT_THEME:
        parts.append(_NATIVE_CSS)
    if theme.get("sticker"):
        parts.append(_STICKER_CSS)
    parts.append(_PICKER_CSS)
    return "<style>\n" + "".join(parts) + "</style>"


# --------------------------------------------------------------------------- picker
def _swatch_html(theme, current):
    t = theme["tokens"]
    tile = ""
    if theme.get("sticker"):
        # Percent-encoded with no quotes or parentheses left, so it sits unquoted in url().
        tile = f"background-image:url({sticker_tile(theme['sticker'], size=84, opacity=0.22)});"
    brand_style = f"color:{t['brand_color']};"
    if t["brand_fill"] != "none":
        brand_style += (f"background:{t['brand_fill']};-webkit-background-clip:text;"
                        f"background-clip:text;")
    sticker = f" {theme['sticker']}" if theme.get("sticker") else ""
    dots = "".join(f"<span class='th-sw-dot' style='background:{c}'></span>"
                   for c in (t["accent"], t["accent2"], t["ok"], t["warn"]))
    side_bars = "".join(f"<i style='background:{c}'></i>"
                        for c in (t["sb_txt"], t["sb_txt2"], t["sb_txt2"], t["sb_accent"]))
    state = ("<span class='pill ok'>In use</span>" if current
             else f"<span class='pill'>{'Dark' if theme['dark'] else 'Light'}</span>")
    return (
        f"<div class='th-sw' style='border-color:{t['line']}'>"
        f"<div class='th-sw-side' style='background:linear-gradient(180deg,{t['sidebar']},{t['sidebar2']})'>"
        f"{side_bars}</div>"
        f"<div class='th-sw-main' style='background-color:{t['bg']};{tile}'>"
        f"<div class='th-sw-brand' style='color:{t['txt']}'>Ghar<span style='{brand_style}'>Se</span>{sticker}</div>"
        f"<div style='color:{t['txt2']};font-size:.68rem'>Fresh lunch, 2 streets away</div>"
        f"<div class='th-sw-row'>{dots}"
        f"<span class='th-sw-btn' style='background:{t['btn_bg']};color:{t['btn_txt']}'>Order</span></div>"
        f"</div></div>"
        f"<div class='th-name'>{theme['name']}</div>{state}"
    )


def theme_picker(current_key, key_prefix="theme"):
    """Show all 20 themes as swatch cards, grouped. Returns the key the person just
    chose on the run they click it, otherwise None. Saving is the caller's job."""
    current = get(current_key)["key"]
    chosen = None
    for group in GROUPS:
        members = [t for t in THEMES if t["group"] == group]
        st.markdown(f"**{group}** <span class='muted'>· {GROUP_BLURBS[group]}</span>",
                    unsafe_allow_html=True)
        cols = st.columns(5)
        for i, theme in enumerate(members):
            with cols[i % 5]:
                with st.container(border=True):
                    is_current = theme["key"] == current
                    st.markdown(_swatch_html(theme, is_current), unsafe_allow_html=True)
                    if st.button("In use" if is_current else "Use this",
                                 key=f"{key_prefix}_{theme['key']}",
                                 disabled=is_current, use_container_width=True):
                        chosen = theme["key"]
    return chosen
