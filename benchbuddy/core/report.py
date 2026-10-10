"""Power-budget report as a self-contained HTML page.

Two flavours from the same data:
  * screen - dark industrial page (inline CSS, embedded diagram PNG), switches to a
             light layout when printed from a browser
  * print  - plain tables and light colours, simple enough for Qt's rich-text engine,
             which the GUI uses to write PDFs

No GUI imports here: the diagram arrives as PNG bytes from whoever calls us.
"""

from __future__ import annotations

import base64
import datetime as _dt
from html import escape

from .power import ERROR, OK, WARN, PowerReport, Project

DEFAULT_PALETTE = dict(obsidian="#0b0f14", panel="#11161d", border="#1e2831", border_hi="#2c3a46",
                       teal="#2fd6c3", phosphor="#4be08a", amber="#ffb454", red="#ff6b6b",
                       text="#c8d3da", text_hi="#e8eef2", muted="#6f808c")
PRINT_STATUS = {OK: "#1b8a4a", WARN: "#b86e00", ERROR: "#c62828"}
ICON = {OK: "✔", WARN: "⚠", ERROR: "✖"}
VERDICT = {OK: "Power budget looks healthy", WARN: "Works, but check the warnings",
           ERROR: "Problems found: this will brown out or overheat"}
ASSUMPTIONS = [
    "Average current drives battery runtime and regulator heat; peak current drives brown-out checks.",
    "All load peaks are assumed to happen at the same time (deliberately conservative).",
    "Warning thresholds: 80 % of rating, 5 % / 10 % source sag, 50 / 90 °C LDO temperature rise.",
    "Preset currents are typical values. Confirm with a datasheet or a meter.",
]


def _ma(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f} A"
    if v >= 10:
        return f"{v:.0f} mA"
    if v >= 1:
        return f"{v:.1f} mA"
    return f"{v * 1000:.0f} µA"


def _extra(rr) -> str:
    r = rr.rail
    if rr.runtime_h is not None:
        return f"{rr.runtime_h:.1f} h" if rr.runtime_h < 48 else f"{rr.runtime_h / 24:.1f} days"
    if r.kind == "ldo" and rr.temp_rise_avg_c is not None:
        return f"{rr.dissipation_avg_w * 1000:.0f} mW (+{rr.temp_rise_avg_c:.0f} °C)"
    if r.kind in ("buck", "boost"):
        return f"{rr.dissipation_avg_w * 1000:.0f} mW loss"
    return "—"


def summary(project: Project, report: PowerReport) -> dict:
    """Headline numbers: per-supply peak/avg draw and the shortest battery runtime."""
    supplies = [rr for rr in report.rails if rr.rail.kind == "supply"]
    runtimes = [rr.runtime_h for rr in supplies if rr.runtime_h is not None]
    return dict(
        status=report.status,
        rails=len(report.rails),
        loads=sum(l.qty for l in project.loads),
        peak_ma=sum(rr.peak_ma for rr in supplies),
        avg_ma=sum(rr.avg_ma for rr in supplies),
        runtime_h=min(runtimes) if runtimes else None,
        errors=sum(1 for rr in report.rails for lv, _ in rr.messages if lv == ERROR),
        warnings=sum(1 for rr in report.rails for lv, _ in rr.messages if lv == WARN),
    )


def render_html(project: Project, report: PowerReport, *, title: str = "Power budget",
                diagram_png: bytes | None = None, palette: dict | None = None,
                print_mode: bool = False, generated: _dt.datetime | None = None,
                app_version: str = "", diagram_src: str | None = None, diagram_width: int | None = None,
                diagram_size: tuple[int, int] | None = None) -> str:
    """diagram_png embeds the image; diagram_src instead references one the caller registers
    (Qt's rich-text engine, used for PDFs, wants a named resource rather than a data URI)."""
    pal = {**DEFAULT_PALETTE, **(palette or {})}
    s = summary(project, report)
    when = (generated or _dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    status_col = (PRINT_STATUS if print_mode else
                  {OK: pal["phosphor"], WARN: pal["amber"], ERROR: pal["red"]})

    def badge(level: str, text: str) -> str:
        return f"<span class='st' style='color:{status_col[level]}'>{ICON[level]} {escape(text)}</span>"

    runtime = "—"
    if s["runtime_h"] is not None:
        runtime = f"{s['runtime_h']:.1f} h" if s["runtime_h"] < 48 else f"{s['runtime_h'] / 24:.1f} days"
    stats = [("Peak draw", _ma(s["peak_ma"])), ("Average draw", _ma(s["avg_ma"])),
             ("Battery runtime", runtime), ("Rails / loads", f"{s['rails']} / {s['loads']}"),
             ("Errors / warnings", f"{s['errors']} / {s['warnings']}")]

    rail_rows = []
    for rr in report.rails:
        r = rr.rail
        indent = "&nbsp;&nbsp;↳ " if r.parent else ""
        rail_rows.append(
            "<tr>"
            f"<td>{indent}<b>{escape(r.name)}</b></td><td>{escape(r.kind)}</td><td class='n'>{r.v_out:g} V</td>"
            f"<td>{badge(rr.status, rr.status.upper())}</td>"
            f"<td class='n'>{_ma(rr.avg_ma)}</td><td class='n'>{_ma(rr.peak_ma)}</td>"
            f"<td class='n'>{_ma(r.max_ma)}</td><td class='n'>{rr.util_peak_pct:.0f}%</td>"
            f"<td class='n'>{'—' if rr.v_peak_v is None else f'{rr.v_peak_v:.2f} V'}</td>"
            f"<td>{escape(_extra(rr))}</td></tr>")
    load_rows = "".join(
        "<tr>"
        f"<td>{escape(l.name)}</td><td>{escape(l.rail)}</td><td class='n'>{l.qty}</td>"
        f"<td class='n'>{l.i_active_ma:g}</td><td class='n'>{l.i_peak_ma:g}</td>"
        f"<td class='n'>{l.i_sleep_ma:g}</td><td class='n'>{l.duty * 100:g}%</td>"
        f"<td class='n'>{_ma(l.avg_ma)}</td><td class='n'>{_ma(l.peak_ma)}</td></tr>"
        for l in project.loads)
    findings = []
    for rr in report.rails:
        r = rr.rail
        items = "".join(f"<li>{badge(lv, '')}{escape(t)}</li>" for lv, t in rr.messages)
        findings.append(f"<h3>{escape(r.name)} <span class='muted'>({r.kind}, {r.v_out:g} V)</span></h3>"
                        f"<ul class='find'>{items}</ul>")
    diagram = ""
    src = None
    if diagram_png:
        src = "data:image/png;base64," + base64.b64encode(diagram_png).decode("ascii")
    elif diagram_src:
        src = diagram_src
    if src:
        if diagram_size:
            width = f" width='{diagram_size[0]}' height='{diagram_size[1]}'"
        else:
            width = f" width='{diagram_width}'" if diagram_width else ""
        diagram = (f"<h2>Power tree</h2><div class='diagram'>"
                   f"<img alt='Power tree diagram' src='{src}'{width}></div>")
    head_stats = "".join(f"<div class='stat'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
                         for k, v in stats)
    if print_mode:   # Qt rich text has no flexbox: stats as a one-row table
        head_stats = ("<table class='stats'><tr>" +
                      "".join(f"<td><span class='k'>{k}</span><br><span class='v'>{v}</span></td>"
                              for k, v in stats) + "</tr></table>")
    assumptions = "".join(f"<li>{escape(a)}</li>" for a in ASSUMPTIONS)
    css = _print_css() if print_mode else _screen_css(pal)
    version = f" {escape(app_version)}" if app_version else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · BenchBuddy report</title>
<style>{css}</style></head>
<body><main>
<header>
  <div class="brand">BENCHBUDDY{version} · POWER BUDGET REPORT</div>
  <h1>{escape(title)}</h1>
  <div class="meta">Generated {when}</div>
  <div class="verdict" style="color:{status_col[s['status']]};border-color:{status_col[s['status']]}">
    {ICON[s['status']]} {VERDICT[s['status']]}</div>
</header>
<section class="stats">{head_stats}</section>
{diagram}
<h2>Rails</h2>
<div class="scroll"><table class="grid" width="100%">
<tr><th>Rail</th><th>Type</th><th>Vout</th><th>Status</th><th>Avg</th><th>Peak</th><th>Limit</th>
<th>Peak %</th><th>V @ peak</th><th>Heat / runtime</th></tr>
{''.join(rail_rows)}
</table></div>
<h2>Loads</h2>
<div class="scroll"><table class="grid" width="100%">
<tr><th>Load</th><th>Rail</th><th>Qty</th><th>Active mA</th><th>Peak mA</th><th>Sleep mA</th>
<th>Duty</th><th>Avg (total)</th><th>Peak (total)</th></tr>
{load_rows or "<tr><td colspan='9' class='muted'>No loads.</td></tr>"}
</table></div>
<h2>Findings</h2>
{''.join(findings) or "<p class='muted'>No rails.</p>"}
<h2>Assumptions</h2>
<ul class="muted">{assumptions}</ul>
</main></body></html>"""


def _screen_css(p: dict) -> str:
    return f"""
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: {p['obsidian']}; color: {p['text']};
  font: 14px/1.5 "JetBrains Mono", "Cascadia Mono", Consolas, ui-monospace, monospace; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 28px 16px 48px; }}
header {{ border-bottom: 1px solid {p['border']}; padding-bottom: 18px; margin-bottom: 18px; }}
.brand {{ color: {p['teal']}; font-weight: 600; letter-spacing: 2px; font-size: 12px; }}
h1 {{ color: {p['text_hi']}; font-size: 26px; margin: 6px 0 2px; font-weight: 600; }}
h2 {{ color: {p['teal']}; font-size: 13px; letter-spacing: 2px; text-transform: uppercase;
  margin: 28px 0 10px; font-weight: 600; }}
h3 {{ color: {p['text_hi']}; font-size: 14px; margin: 16px 0 4px; }}
.meta, .muted {{ color: {p['muted']}; font-weight: normal; }}
.verdict {{ display: inline-block; margin-top: 12px; padding: 6px 14px; border: 1px solid;
  font-weight: 700; letter-spacing: 1px; }}
section.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1px;
  background: {p['border']}; border: 1px solid {p['border']}; }}
.stat {{ background: {p['panel']}; padding: 12px 14px; }}
.stat .k {{ color: {p['muted']}; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }}
.stat .v {{ color: {p['text_hi']}; font-size: 20px; font-weight: 600; }}
.diagram {{ background: {p['obsidian']}; border: 1px solid {p['border']}; padding: 8px; overflow-x: auto; }}
.diagram img {{ display: block; max-width: 100%; height: auto; }}
.scroll {{ overflow-x: auto; }}
table.grid {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.grid th {{ background: {p['panel']}; color: {p['muted']}; text-align: left; font-weight: 600;
  padding: 7px 8px; border-bottom: 1px solid {p['border_hi']}; white-space: nowrap; }}
.grid td {{ padding: 6px 8px; border-bottom: 1px solid {p['border']}; white-space: nowrap; }}
.grid td.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.grid b {{ color: {p['text_hi']}; }}
.st {{ font-weight: 700; }}
ul.find {{ list-style: none; padding: 0; margin: 0; }}
ul.find li {{ padding: 3px 0 3px 22px; text-indent: -22px; }}
ul.find .st {{ display: inline-block; width: 22px; text-indent: 0; }}
@media print {{
  :root {{ color-scheme: light; }}
  body {{ background: #fff; color: #222; }}
  h1, h3, .grid b, .stat .v {{ color: #000; }}
  h2, .brand {{ color: #0b7d72; }}
  section.stats {{ background: #ccc; border-color: #ccc; }}
  .stat, .grid th {{ background: #f3f5f6; }}
  .grid td {{ border-color: #ddd; }}
  .diagram {{ background: {p['obsidian']}; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
"""


def _print_css() -> str:
    return """
body { font-family: "JetBrains Mono NL", "JetBrains Mono", Consolas, monospace; font-size: 9pt; color: #222; }
.brand { color: #0b7d72; font-weight: 600; font-size: 8pt; }
h1 { font-size: 16pt; margin: 2px 0; color: #000; }
h2 { color: #0b7d72; font-size: 10pt; margin-top: 16px; }
h3 { font-size: 9.5pt; margin: 10px 0 2px; color: #000; }
.meta, .muted { color: #6f808c; font-weight: normal; }
.verdict { font-weight: 700; margin-top: 6px; }
table.stats { border-collapse: collapse; margin-top: 8px; }
table.stats td { border: 1px solid #ccc; padding: 4px 10px; }
.k { color: #6f808c; font-size: 7.5pt; }
.v { font-size: 11pt; font-weight: 700; }
table.grid { border-collapse: collapse; }
.grid th { background: #eef1f2; text-align: left; padding: 3px 6px; border-bottom: 1px solid #999; }
.grid td { padding: 3px 6px; border-bottom: 1px solid #ddd; }
.grid td.n { text-align: right; }
.st { font-weight: 700; }
"""
