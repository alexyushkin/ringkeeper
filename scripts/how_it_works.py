"""The "how it works" drawing, written once for the app and for the docs.

    uv run python scripts/how_it_works.py

docs/images/how-it-works.svg carries the app's colors, light and dark, and follows the
reader's system. web/components/how-it-works.ts is the same drawing on the app's own
color tokens, so it follows Settings > Appearance. tests/test_how_it_works.py checks
that both are current, and that the colors are still the app's.
"""

import json
import re
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parents[1]
DOCS_FILE = REPO / "docs" / "images" / "how-it-works.svg"
APP_FILE = REPO / "web" / "components" / "how-it-works.ts"
CSS_FILE = REPO / "web" / "app" / "globals.css"

WIDTH, HEIGHT = 1200, 650
SUMMARY = (
    "How a request flows. You ask in plain words. Ringkeeper finds the right client "
    "project. Claude does the work with only that project's connections. Reads and test "
    "data run straight through; a change to live data or a message to real people waits "
    "for your OK. Then it reports back, and most data changes can be undone for 7 days. "
    "Agents and routines are an optional way to start the same work without asking. "
    "Each client's connections are kept apart, and the AI never sees a key."
)
TOKENS = (
    "bg", "surface", "sunken", "line", "ink", "muted", "accent", "accent-ink", "good",
    "warn", "accent-soft", "good-soft", "warn-soft",
)  # fmt: skip

ICONS = {
    "chat": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
    "folder": "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "spark": "M12 3l1.9 5.6 5.6 1.9-5.6 1.9L12 18l-1.9-5.6-5.6-1.9 5.6-1.9z",
    "shield": "M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6z",
    "check": "M5 12.5l4.5 4.5L19 7.5",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3.5 2",
    "lock": "M6 11h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1zM8 11V8a4 4 0 0 1 8 0v3",
}  # fmt: skip


def color(name: str) -> str:
    return f"var(--color-{name})"


def text(x: float, y: float, words: str, size: float, fill: str = "ink", weight: int = 400,
         anchor: str = "start") -> str:  # fmt: skip
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color(fill)}" text-anchor="{anchor}">{escape(words)}</text>'
    )


def lines(x: float, y: float, words: str, width: int, size: float = 13, fill: str = "muted",
          gap: float = 18) -> str:  # fmt: skip
    return "".join(
        text(x, y + i * gap, line, size, fill) for i, line in enumerate(textwrap.wrap(words, width))
    )


def icon(x: float, y: float, name: str, tone: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="38" height="38" rx="11" fill="{color(tone + "-soft")}"/>'
        f'<path transform="translate({x + 7} {y + 7})" d="{ICONS[name]}" fill="none" '
        f'stroke="{color(tone)}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def card(x: float, y: float, w: float, h: float, border: str = "line", width: float = 1.5) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{color("surface")}" '
        f'stroke="{color(border)}" stroke-width="{width}" filter="url(#hiw-lift)"/>'
    )


def chip(x: float, y: float, words: str, tone: str = "accent") -> tuple[str, float]:
    width = 16 + len(words) * 6.7
    drawn = (
        f'<rect x="{x}" y="{y}" width="{width:.0f}" height="25" rx="12.5" '
        f'fill="{color(tone + "-soft")}"/>' + text(x + 9, y + 17, words, 12, tone, 600)
    )
    return drawn, x + width + 6


def arrow(path: str, tone: str = "accent", dashed: bool = False, marker: str = "hiw-head") -> str:
    dash = ' stroke-dasharray="6 6"' if dashed else ""
    return (
        f'<path d="{path}" fill="none" stroke="{color(tone)}" stroke-width="2.25" '
        f'stroke-linecap="round"{dash} marker-end="url(#{marker})"/>'
    )


CARD_W, CARD_H, CARD_Y, STEP = 196, 226, 150, 227
LEFT = 48


def step(index: int, name: str, title: str, tone: str = "accent", border: str = "line",
         stroke: float = 1.5) -> tuple[str, float]:  # fmt: skip
    x = LEFT + index * STEP
    head = (
        card(x, CARD_Y, CARD_W, CARD_H, border, stroke)
        + icon(x + 16, CARD_Y + 16, name, tone)
        + text(x + CARD_W - 16, CARD_Y + 41, str(index + 1), 13, "muted", 600, "end")
        + text(x + 16, CARD_Y + 79, title, 16, "ink", 700)
    )
    return head, x + 16


def you_ask() -> str:
    head, x = step(0, "chat", "You ask")
    bubble = (
        f'<rect x="{x}" y="{CARD_Y + 94}" width="164" height="62" rx="14" '
        f'fill="{color("accent")}"/>'
        + "".join(
            text(x + 12, CARD_Y + 115 + i * 17, line, 12.5, "accent-ink", 500)
            for i, line in enumerate(
                ("Add last week's", "leads to General", "Informatics' Airtable")
            )
        )
    )
    return head + bubble + lines(x, CARD_Y + 180, "Typed or spoken, in the app or on Telegram", 25)


def finds_project() -> str:
    head, x = step(1, "folder", "It finds the project")
    drawn, _ = chip(x, CARD_Y + 96, "geninfos.com website")
    return (
        head
        + drawn
        + lines(x, CARD_Y + 146, "From what you said. If it isn't sure, it asks you first.", 25)
    )


def does_the_work() -> str:
    head, x = step(2, "spark", "Claude does the work")
    first, nx = chip(x, CARD_Y + 96, "Airtable")
    second, _ = chip(nx, CARD_Y + 96, "n8n")
    return head + first + second + lines(
        x, CARD_Y + 146, "In that project's folder, with only that project's connections.", 25
    )  # fmt: skip


def waits_for_you() -> str:
    head, x = step(3, "shield", "Live changes wait", "warn", "warn", 2)
    approve = (
        f'<rect x="{x}" y="{CARD_Y + 96}" width="82" height="28" rx="9" fill="{color("good")}"/>'
        + text(x + 41, CARD_Y + 115, "Approve", 12.5, "surface", 700, "middle")
        + f'<rect x="{x + 90}" y="{CARD_Y + 96}" width="64" height="28" rx="9" fill="none" '
        f'stroke="{color("muted")}"/>'
        + text(x + 122, CARD_Y + 115, "Deny", 12.5, "ink", 600, "middle")
    )
    return head + approve + lines(
        x, CARD_Y + 146, "Production data and messages to real people need your OK.", 25
    )  # fmt: skip


def done() -> str:
    head, x = step(4, "check", "Done, and logged", "good", "good", 2)
    drawn, _ = chip(x, CARD_Y + 96, "Undo for 7 days", "good")
    return head + drawn + lines(
        x, CARD_Y + 146, "Reported in your chat. Undo covers databases, Airtable and n8n.", 25
    )  # fmt: skip


def flow_arrows() -> str:
    middle = CARD_Y + CARD_H / 2
    drawn = ""
    for index in range(4):
        start = LEFT + index * STEP + CARD_W + 4
        tone, head = ("warn", "hiw-head-warn") if index == 2 else ("accent", "hiw-head")
        tone, head = ("good", "hiw-head-good") if index == 3 else (tone, head)
        drawn += arrow(f"M{start} {middle} H{start + STEP - CARD_W - 11}", tone, marker=head)
    # Past the hold: what doesn't need you.
    from_x = LEFT + 2 * STEP + CARD_W / 2
    to_x = LEFT + 4 * STEP + CARD_W / 2
    top = CARD_Y - 6
    drawn += arrow(
        f"M{from_x} {top} V{top - 8} Q{from_x} {top - 22} {from_x + 14} {top - 22} "
        f"H{to_x - 14} Q{to_x} {top - 22} {to_x} {top - 8} V{top - 3}", "good",
        marker="hiw-head-good",
    )  # fmt: skip
    label = "Reading, and test data, go straight through"
    drawn += (
        f'<rect x="{(from_x + to_x) / 2 - 145}" y="{top - 33}" width="290" height="22" '
        f'fill="url(#hiw-page)"/>'
        + text((from_x + to_x) / 2, top - 17, label, 13, "good", 600, "middle")
    )
    return drawn


PANEL_Y, PANEL_H = 416, 190


def optional_panel() -> str:
    x, w = LEFT, 600
    y = PANEL_Y
    routine_x, agent_x = x + 24, x + 340
    box_y = y + 72
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{PANEL_H}" rx="18" fill="none" '
        f'stroke="{color("muted")}" stroke-width="1.5" stroke-dasharray="7 7"/>'
        + text(x + 24, y + 34, "Optional: work that starts itself", 16, "ink", 700)
        + text(x + 24, y + 56, "You don't need these. Asking is enough.", 13, "muted")
        + card(routine_x, box_y, 272, 94)
        + icon(routine_x + 14, box_y + 14, "clock", "accent")
        + text(routine_x + 62, box_y + 31, "Routine: when", 14, "ink", 700)
        + lines(routine_x + 62, box_y + 51, "On a schedule, on an event, or when you press Run", 30, 12.5, gap=16)
        + card(agent_x, box_y, 236, 94)
        + icon(agent_x + 14, box_y + 14, "spark", "accent")
        + text(agent_x + 62, box_y + 31, "Agent: who", 14, "ink", 700)
        + lines(agent_x + 62, box_y + 51, "A saved helper with its own instructions", 24, 12.5, gap=16)
        + arrow(f"M{routine_x + 276} {box_y + 47} H{agent_x - 6}")
        + arrow(f"M{LEFT + 2 * STEP + CARD_W / 2} {box_y - 4} V{CARD_Y + CARD_H + 7}", dashed=True)
    )  # fmt: skip


def apart_panel() -> str:
    x, w = LEFT + 624, 480
    y = PANEL_Y
    lane_y = y + 72
    return (
        card(x, y, w, PANEL_H)
        + icon(x + 20, y + 16, "lock", "good")
        + text(x + 70, y + 34, "Every client kept apart", 16, "ink", 700)
        + text(x + 70, y + 54, "One client's project can't reach another's connections.", 12.5, "muted")
        + f'<rect x="{x + 20}" y="{lane_y}" width="210" height="50" rx="12" fill="{color("sunken")}"/>'
        + text(x + 34, lane_y + 21, "General Informatics LLC", 13, "ink", 700)
        + text(x + 34, lane_y + 39, "Its projects and connections", 12, "muted")
        + f'<rect x="{x + 250}" y="{lane_y}" width="210" height="50" rx="12" fill="{color("sunken")}"/>'
        + text(x + 264, lane_y + 21, "Another client", 13, "ink", 700)
        + text(x + 264, lane_y + 39, "Its projects and connections", 12, "muted")
        + f'<path d="M{x + 240} {lane_y - 4} V{lane_y + 54}" stroke="{color("warn")}" '
        f'stroke-width="2.5" stroke-linecap="round"/>'
        + text(x + 20, y + 150, "Ringkeeper adds each key itself. The AI never sees one.", 13, "ink", 600)
        + text(x + 20, y + 170, "Your own tools, like a browser, can go to any project.", 12.5, "muted")
    )  # fmt: skip


def defs() -> str:
    def head(name: str, tone: str) -> str:
        return (
            f'<marker id="{name}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6.5" '
            f'markerHeight="6.5" orient="auto-start-reverse"><path d="M0 0.8L9 5L0 9.2z" '
            f'fill="{color(tone)}"/></marker>'
        )

    return (
        "<defs>"
        + head("hiw-head", "accent")
        + head("hiw-head-warn", "warn")
        + head("hiw-head-good", "good")
        + '<linearGradient id="hiw-page" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color("bg")}"/>'
        f'<stop offset="1" stop-color="{color("bg")}"/></linearGradient>'
        '<filter id="hiw-lift" x="-10%" y="-10%" width="120%" height="125%">'
        '<feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="#000" flood-opacity="0.07"/>'
        "</filter></defs>"
    )


def drawing() -> str:
    return (
        f'<title id="hiw-title">{escape(SUMMARY)}</title>'
        + defs()
        + f'<rect width="{WIDTH}" height="{HEIGHT}" rx="24" fill="{color("bg")}"/>'
        + text(LEFT, 62, "You ask. Ringkeeper does the work. You stay in charge.", 28, "ink", 700)
        + text(LEFT, 92, "Connect each client's tools once, then just say what you need.", 15, "muted")
        + flow_arrows()
        + you_ask() + finds_project() + does_the_work() + waits_for_you() + done()
        + optional_panel()
        + apart_panel()
    )  # fmt: skip


def opening(extra: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-labelledby="hiw-title" font-family="Segoe UI, -apple-system, Roboto, Helvetica, '
        f'Arial, sans-serif"{extra}>'
    )


def palettes(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """The app's colors, light and dark, from its own stylesheet."""

    def read(block: str) -> dict[str, str]:
        return dict(re.findall(r"--color-([a-z-]+): (#[0-9a-f]{6});", block))

    light = css[css.index("@theme") : css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index(':root[data-theme="dark"]') :]
    return read(light), read(dark[: dark.index("}")])


def variables(values: dict[str, str]) -> str:
    return "".join(f"--color-{name}:{values[name]};" for name in TOKENS)


def docs_svg(css: str) -> str:
    light, dark = palettes(css)
    style = (
        f"<style>svg{{{variables(light)}}}"
        f"@media (prefers-color-scheme: dark){{svg{{{variables(dark)}}}}}</style>"
    )
    return opening() + style + drawing() + "</svg>\n"


def app_module() -> str:
    body = json.dumps(opening(' class="h-auto w-full"') + drawing() + "</svg>", ensure_ascii=False)
    return (
        "// Written by scripts/how_it_works.py: change the drawing there.\n"
        f"export const HOW_IT_WORKS = {body};\n"
    )


def main() -> None:
    css = CSS_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    DOCS_FILE.write_text(docs_svg(css), encoding="utf-8", newline="\n")
    APP_FILE.write_text(app_module(), encoding="utf-8", newline="\n")
    print(f"wrote {DOCS_FILE.name} and {APP_FILE.name}")


if __name__ == "__main__":
    main()
