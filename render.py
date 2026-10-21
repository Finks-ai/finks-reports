"""Render the public Finks reports site (Finks-ai/finks-reports) from its model.

    python render.py --model model.json --out _site

The model is built and allowlist-checked in finks-lakehouse, exported to the `data` branch here as
`data/model.json`, and is the only input: model JSON in, static HTML out. Nothing else is read. Model strings come
from finks-lakehouse's closed allowlists and are only ever placed in text nodes, never in attribute values (every
dynamic attribute is a number or a constant); scan.py is the tag and attribute gate. `site` returns the six HTML
files; `write` renders, then runs scan.py over what was written (a finding deletes the output).
"""

import argparse
import datetime as dt
import json
import math
import shutil
import sys
from collections import Counter
from html import escape
from pathlib import Path

from scan import FILES, Leak, scan

STATUSES = ("pass", "warn", "fail", "error", "skip")
STATUS = ("done", "in progress", "planned")

CSS = """
:root{--pass:#007a5a;--warn:#b25e00;--fail:#c0392b;--error:#7b3fa0;--skip:#6b7280;--line:#e5e7eb;--muted:#6b7280}
*{box-sizing:border-box}
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#111827;
background:#f9fafb;margin:0}
main{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
a{color:#1d4ed8}
nav{margin-bottom:16px}
.muted{color:var(--muted)}
.small{font-size:12px}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.pill{display:inline-block;border-radius:999px;padding:2px 10px;font-weight:600;border:1px solid currentColor}
.st-pass{color:var(--pass)}
.st-warn{color:var(--warn)}
.st-fail{color:var(--fail)}
.st-error{color:var(--error)}
.st-skip{color:var(--skip)}
.f-pass{fill:var(--pass)}
.f-warn{fill:var(--warn)}
.f-fail{fill:var(--fail)}
.f-error{fill:var(--error)}
.f-skip{fill:#d1d5db}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card h3{margin:0 0 6px;font-size:15px;display:flex;justify-content:space-between}
.bar{display:block;margin:6px 0;border-radius:3px}
.about{font-size:12px;color:#374151;margin:8px 0 0;border-top:1px solid var(--line);padding-top:6px}
.chg{font-size:28px;font-weight:700}
.up{color:var(--fail)}
.down{color:var(--pass)}
.chart{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:12px 0}
.chart h3{margin:0 0 4px;font-size:15px}
.ax{font-size:10px;fill:#6b7280}
.ev{font-size:10px;fill:#b25e00;font-weight:700}
.done{color:var(--pass)}
.prog{color:var(--warn)}
.plan{color:var(--skip)}
.f-done{fill:var(--pass)}
.f-prog{fill:var(--warn)}
.f-plan{fill:#d1d5db}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
th{background:#f3f4f6}
ul{margin:6px 0 0;padding-left:18px}
.feature{border:2px solid #1d4ed8;margin-bottom:12px}
"""


def page(title: str, body: str, home: str) -> str:
    nav = f'<nav class="small"><a href="{home}">Finks reports</a></nav>' if home else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>{nav}{body}</main></body></html>\n"
    )


def evals_page(m: dict) -> str:
    total = sum(s["checks"] for s in m["suites"])
    st = {k: sum(s["status"][k] for s in m["suites"]) for k in STATUSES}
    h = [
        "<header><h1>Finks data quality checks</h1>",
        f'<div class="muted">as of {m["as_of"]} · {len(m["suites"])} suites · {total:,} checks</div>',
        '<div class="counts">',
        *(f'<span class="pill st-{k}">{st[k]:,} {k}</span>' for k in STATUSES if st[k]),
        "</div></header>",
        "<p>Automated checks run against the data Finks serves. Each suite groups checks of one kind. A check passes, "
        "warns, fails, errors (could not run) or is skipped. Gate checks block a release when they fail. The pass "
        "rate excludes skipped checks; the line shows it across saved runs.</p>",
        "<h2>Suites</h2><div class=cards>",
    ]
    for s in m["suites"]:
        c, rates = s["status"], [x["rate"] for x in s["rates"]]
        r = rates[-1]
        h.append(
            f'<div class=card><h3><span>{s["suite"]}</span><span class="muted small">{s["day"]}</span></h3>'
            f"{bar(Counter(c))}<div class=small>"
            + " · ".join(
                f'<span class="st-{k}">{c[k]} {k}</span>' for k in STATUSES if c[k]
            )
            + f"</div><div>{s['checks']} checks · pass rate <b>{'n/a' if r is None else f'{r:.1f} %'}</b></div>"
            f"<div>{spark(rates)}</div>"
            f"<div class=small>gate checks: {s['gates']['pass']} pass · {s['gates']['fail']} fail</div>"
            f'<p class="about">{escape(s["about"])}</p></div>'
        )
    h.append("</div>")
    return page(f"Finks data quality checks {m['as_of']}", "\n".join(h), "../")


def chart(
    days: list[str],
    ys: list[float],
    events: list[tuple[int, str]],
    guide: float = 100,
    fmt=lambda v: f"{v:.0f}",
    label: str = "daily index, baseline 100",
    w: int = 720,
    h: int = 170,
) -> str:
    """Line over `days` with a dashed `guide` (the index's 100, or a series' first value) and numbered event markers."""
    left, right, top, bottom = 72, 10, 14, 22
    hi = max(guide * 1.5, -(-max(ys) * 1.1 // 50) * 50)

    def x(i: int) -> float:
        return left + i * (w - left - right) / max(len(days) - 1, 1)

    def y(v: float) -> float:
        return top + (h - top - bottom) * (1 - v / hi)

    g = []
    for v in (0, guide, hi):
        dash = ' stroke-dasharray="4 3"' if v == guide else ""
        g.append(
            f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#9ca3af"{dash}/>'
        )
        if v != 0 or y(0) - y(guide) > 12:  # a tall axis squeezes 0 into the guide
            g.append(
                f'<text class="ax" x="{left - 4}" y="{y(v) + 3:.1f}" text-anchor="end">{fmt(v)}</text>'
            )
    for n, (i, _) in enumerate(events, 1):
        g.append(
            f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{top}" y2="{y(0):.1f}" stroke="#b25e00" stroke-dasharray="3 3"/>'
        )
        g.append(
            f'<text class="ev" x="{x(i) + 2:.1f}" y="{top - 3 + 10 * (n % 2)}">{n}</text>'
        )
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(ys))
    g.append(
        f'<polyline points="{pts}" fill="none" stroke="#1d4ed8" stroke-width="1.5"/>'
    )
    for i in (0, len(days) - 1):
        anchor = "start" if i == 0 else "end"
        g.append(
            f'<text class="ax" x="{x(i):.1f}" y="{h - 6}" text-anchor="{anchor}">{days[i]}</text>'
        )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{label}">{"".join(g)}</svg>'


def costs_page(m: dict) -> str:
    days = m["days"]
    ev = [(days.index(e["day"]), e["label"]) for e in m["events"] if e["day"] in days]
    b0, b1 = m["baseline"]
    h = [
        "<header><h1>Finks infrastructure cost trends</h1>",
        f'<div class="muted">daily, {days[0]} to {days[-1]} · indexed: each category\'s own average over '
        f"{b0} to {b1} = 100 · no amounts are published</div></header>",
        f'<h2>Change: {m["now"][0]} to {m["now"][1]} vs the baseline</h2><div class="grid">',
    ]
    for k in m["categories"]:
        c = m["change"][k]
        chg = (
            '<div class="chg">n/a</div><div class="muted small">not in the baseline period</div>'
            if c is None
            else f'<div class="chg {"up" if c > 0 else "down"}">{c:+d} %</div>'
        )
        h.append(f'<div class="card"><div class="muted">{k}</div>{chg}</div>')
    h.append("</div><h2>Daily index by category</h2>")
    h.append(
        '<p class="small">Events: '
        + " · ".join(
            f"<b>{n}</b> {days[i]} {label}" for n, (i, label) in enumerate(ev, 1)
        )
        + "</p>"
    )
    for k in m["categories"]:
        note = (
            f' <span class="muted small">indexed to {m["now"][0]} to {m["now"][1]} = 100</span>'
            if k in m["new"]
            else ""
        )
        h.append(
            f'<div class="chart"><h3>{k}{note}</h3>{chart(days, m["index"][k], ev)}</div>'
        )
    h.append(
        '<p class="small muted">Categories: ClickHouse (analytics database), orchestration (pipeline scheduler usage), '
        "compute (containers and functions), storage (object storage), audit logging (cloud audit trail). "
        f"The newest {m['settle_days']} days are left out until the providers settle them.</p>"
    )
    return page(f"Finks infrastructure cost trends {days[-1]}", "\n".join(h), "../")


CLS = {"done": "done", "in progress": "prog", "planned": "plan"}


def month(v: str) -> str:
    return dt.date.fromisoformat(f"{v}-01").strftime("%B %Y")


def status_bar(c: Counter, w: int = 260, h: int = 10) -> str:
    n, x, rects = c.total() or 1, 0.0, []
    for s in STATUS:
        dx = w * c[s] / n
        rects.append(
            f'<rect x="{x:.2f}" width="{dx:.2f}" height="{h}" class="f-{CLS[s]}"/>'
        )
        x += dx
    return (
        f'<svg class="bar" viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" role="img" '
        f'aria-label="{", ".join(f"{c[s]} {s}" for s in STATUS)}">{"".join(rects)}</svg>'
    )


def roadmap_page(m: dict) -> str:
    ds = m["datasets"]
    tot = Counter(d["status"] for d in ds)
    chip = lambda s: f'<span class="{CLS[s]}">{s}</span>'  # noqa: E731
    h = [
        "<header><h1>Finks data platform roadmap</h1>",
        f'<div class="muted">as of {month(m["as_of"])} · updated after every release</div>',
        '<div class="counts">',
        *(
            f'<span class="pill {CLS[s]}">{round(100 * tot[s] / len(ds))} % {s}</span>'
            for s in STATUS
            if tot[s]
        ),
        "</div></header>",
        f"<p>Finks is moving its data pipelines to a new platform and widening the datasets it serves. "
        f"{len(ds)} datasets are in the plan; the percentages above are shares of them.</p>",
        "<h2>Workstreams</h2><table><tr><th>Workstream</th><th>Status</th><th>Finished</th></tr>",
        *(
            f"<tr><td>{p['name']}</td><td>{chip(p['status'])}</td>"
            f"<td>{month(p['month']) if p['month'] else '-'}</td></tr>"
            for p in m["phases"]
        ),
        "</table>",
        f"<h2>{m['universe_name']}</h2><table><tr><th>Phase</th><th>Status</th><th>Finished</th></tr>",
        *(
            f"<tr><td>{p['name']}</td><td>{chip(p['status'])}</td>"
            f"<td>{month(p['month']) if p['month'] else '-'}</td></tr>"
            for p in m["universe"]
        ),
        "</table><h2>Dataset coverage</h2><div class=cards>",
    ]
    for cat in m["categories"]:
        items = [d for d in ds if d["category"] == cat]
        if items:
            c = Counter(d["status"] for d in items)
            h.append(
                f'<div class=card><h3><span>{cat}</span><span class="muted small">'
                f"{round(100 * c['done'] / len(items))} % done</span></h3>{status_bar(c)}<ul>"
                + "".join(f"<li>{chip(d['status'])} {d['name']}</li>" for d in items)
                + "</ul></div>"
            )
    h.append("</div>")
    return page(f"Finks data platform roadmap {m['as_of']}", "\n".join(h), "../")


BYTES = (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3))
ROWS = (("billion", 1e9), ("million", 1e6), ("thousand", 1e3))


def human(v: float, units: tuple) -> str:
    """2 significant figures in the largest unit that fits: 1.8 TB, 180 million."""
    name, size = next(((n, s) for n, s in units if v >= s), ("", 1))
    q = v / size
    if not q:
        return "0"
    d = 1 - math.floor(math.log10(q))
    return f"{round(q, d):.{max(d, 0)}f} {name}".strip()


def lake_page(m: dict) -> str:
    size, rows = (lambda v: human(v, BYTES)), (lambda v: human(v, ROWS))
    lake, nights, served = m["lake_series"], m["rows_series"], m["served_series"]
    h = [
        "<header><h1>Finks data lake</h1>",
        f'<div class="muted">as of {m["as_of"]} · sizes and row counts rounded to 2 significant figures</div>',
        '<div class="counts">',
        f'<span class="pill">{m["datasets"]} datasets served</span>',
        f'<span class="pill">{rows(m["rows"])} rows served</span>',
        f'<span class="pill">{size(m["bytes"])} served</span>',
        f'<span class="pill">{size(m["lake_bytes"])} in the lake</span>',
        "</div></header>",
        "<p>Served: the datasets products read, as stored by the serving database. In the lake: every stored file, "
        "earlier versions included, which are kept for audit.</p>",
        f'<h2>Lake size</h2><p class="small muted">daily since {lake[0]["day"]}; the dashed line is the first day</p>',
        f'<div class="chart">{chart([x["day"] for x in lake], [x["bytes"] for x in lake], [], lake[0]["bytes"], size, "lake size by day")}</div>',
        f'<h2>Rows by night</h2><p class="small muted">from the nightly exports since {nights[0]["day"]}; the dashed '
        "line is the first night</p>",
        f'<div class="chart">{chart([x["day"] for x in nights], [x["rows"] for x in nights], [], nights[0]["rows"], rows, "rows served by night")}</div>',
        "<h2>By category</h2><table><tr><th>Category</th><th>Datasets</th><th>Rows</th><th>Served size</th>"
        f"<th>Data since</th><th>Rows vs {m['rows_base']}</th></tr>",
    ]
    for c in m["categories"]:
        since = month(c["earliest"]) if c["earliest"] else "-"
        chg = "new" if c["rows_change"] is None else f"{int(c['rows_change']):+,d} %"
        h.append(
            f"<tr><td>{c['category']}</td><td>{c['datasets']}</td><td>{rows(c['rows'])}</td>"
            f"<td>{size(c['bytes'])}</td><td>{since}</td><td>{chg}</td></tr>"
        )
    h.append(
        '</table><p class="small muted">Data since: the earliest date in the category\'s date-partitioned datasets '
        "('-' when none is). New: under 1 % of today's rows a week earlier.</p>"
    )
    if len(served) > 1:
        h.append(
            f'<h2>Served size</h2><div class="chart">{chart([x["day"] for x in served], [x["bytes"] for x in served], [], served[0]["bytes"], size, "served size by day")}</div>'
        )
    else:
        h.append(
            f"<h2>Served size</h2><p>A daily series starts {served[0]['day']}, with one snapshot a day.</p>"
        )
    h.append("<h2>New datasets by month</h2><ul>")
    h += [
        f"<li>{month(a['month'])}: {', '.join(a['categories'])}</li>"
        for a in m["added"]
    ]
    h.append(
        '</ul><p class="small muted">By category, from the first nightly export on; datasets served before it count '
        "in its month.</p>"
    )
    h.append(ai_section(m["ai"]))
    return page(f"Finks data lake {m['as_of']}", "\n".join(h), "../")


def sparkline(vals: list[int], w: int = 140, h: int = 24) -> str:
    hi = max(vals) or 1
    step = (w - 4) / max(len(vals) - 1, 1)
    pts = " ".join(
        f"{2 + i * step:.1f},{2 + (h - 4) * (1 - v / hi):.1f}"
        for i, v in enumerate(vals)
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="daily count">'
        f'<polyline points="{pts}" fill="none" stroke="#1d4ed8" stroke-width="1.5"/></svg>'
    )


def ai_section(m: dict) -> str:
    days = m["days"]
    pct = lambda v: "-" if v is None else f"{int(v):+,d} %"  # noqa: E731
    h = [
        "<h2>AI use</h2>",
        f"<p>Calls to an LLM and vectors computed by an embedding model for the lake's own pipelines, per UTC day "
        f"from {days[0]} to {days[-1]}. Counts only. Daily caps are the limits the pipelines enforce on themselves.</p>",
        "<table><tr><th>Purpose</th><th>Model</th><th>Last 7 days</th><th>vs the 7 before</th>"
        "<th>Tokens in / out, 7 days</th><th>Daily cap</th><th>Cap used, 7-day average / peak</th>"
        "<th>Daily, last 28 days</th></tr>",
    ]
    for p in m["purposes"]:
        tok = (
            "not metered"
            if p["tokens_in"] is None
            else f"{human(p['tokens_in'], ROWS)} / {human(p['tokens_out'], ROWS)}"
        )
        cap = "none" if p["cap"] is None else f"{p['cap']:,}"
        used = "-" if p["cap"] is None else f"{p['cap_pct']} % / {p['peak_pct']} %"
        unit = "calls" if p["kind"] == "LLM" else "vectors"
        h.append(
            f"<tr><td>{p['purpose']}</td><td>{p['kind']}</td><td>{p['week']:,} {unit}</td><td>{pct(p['trend'])}</td>"
            f"<td>{tok}</td><td>{cap}</td><td>{used}</td><td>{sparkline(p['series'])}</td></tr>"
        )
    h.append(
        '</table><p class="small muted">Daily cap: the regular limit the pipeline sets itself today. Cap use above '
        "100 % comes from an approved one-off backfill, which ran under its own, higher cap for those days.</p>"
        "<p>Not yet metered: " + "; ".join(m["not_metered"]) + ".</p>"
    )
    return "\n".join(h)


def private_page(m: dict) -> str:
    n = lambda v: f"{int(v):,}"  # noqa: E731  (already 2 significant figures)
    chip = lambda s: f'<span class="{CLS[s]}">{s}</span>'  # noqa: E731
    h = [
        "<header><h1>Private companies</h1>",
        f'<div class="muted">as of {m["as_of"]} · counts rounded to 2 significant figures</div></header>',
        "<p>Finks assembles private-company data from public filings, its own news briefs and companies' own "
        "announcements. Each fact carries its source; tracked companies are resolved to one company id.</p>",
        "<h2>Live now</h2><div class=grid>",
        f"<div class=card><div class=muted>SEC Form D offerings</div><div class=chg>{n(m['offerings'])}</div>"
        f'<div class="small muted">from {n(m["issuers"])} issuers, since 2008</div></div>',
        "<div class=card><div class=muted>Private holdings in registered fund portfolios</div>"
        f'<div class=chg>{n(m["companies"])}</div><div class="small muted">distinct holding names: {n(m["positions"])} '
        f"positions in {n(m['funds'])} funds, from SEC N-PORT reports filed since {month(m['since'])}</div></div>",
        "</div><h2>Coming</h2><table><tr><th>Data</th><th>Status</th><th>Measured in testing</th></tr>",
        *(
            f"<tr><td>{c['item']}</td><td>{chip(c['status'])}</td><td>{c['tested'] or '-'}</td></tr>"
            for c in m["coming"]
        ),
        "</table><h2>Examples</h2><table><tr><th>Company</th><th>Registered funds with a position</th>"
        "<th>Round reports from news and newsrooms, in testing</th></tr>",
        *(
            f"<tr><td>{e['name']}</td><td>{e['funds']:,}</td><td>{e['rounds'] or '-'}</td></tr>"
            for e in m["examples"]
        ),
        f'</table><p class="small muted">Counts only. Funds counted from SEC N-PORT reports filed since '
        f"{month(m['since'])}.</p>",
        "<h2>Provenance</h2><p>Each fact carries a link to its source document and the date it was retrieved; "
        "tracked companies are resolved to one company id, and facts drawn from news carry a confidence score.</p>",
    ]
    return page(f"Finks private companies {m['as_of']}", "\n".join(h), "../")


def index_page(m: dict) -> str:
    body = (
        "<header><h1>Finks reports</h1></header>"
        "<p>Public summaries generated from internal reports. Only allowlisted summaries are published.</p>"
        '<div class="card feature"><h3><a href="private/">Private companies</a><span class="muted small">'
        f"{m['private']['as_of']}</span></h3><p>Private-company data from public filings, news and companies' own "
        "announcements, resolved to one company, with a source on every fact.</p></div>"
        '<div class="cards">'
        f'<div class="card"><h3><a href="roadmap/">Data platform roadmap</a><span class="muted small">'
        f"{month(m['roadmap']['as_of'])}</span></h3><p>Workstreams and dataset coverage, with their status.</p></div>"
        f'<div class="card"><h3><a href="lake/">Data lake</a><span class="muted small">'
        f"{m['lake']['as_of']}</span></h3><p>Datasets served, rows and storage by category, and how they grow.</p></div>"
        f'<div class="card"><h3><a href="evals/">Data quality checks</a><span class="muted small">'
        f"{m['eval']['as_of']}</span></h3><p>Suites of automated checks on the data Finks serves: counts, pass rates "
        "and release gates.</p></div>"
        f'<div class="card"><h3><a href="costs/">Infrastructure cost trends</a><span class="muted small">'
        f"{m['cost']['days'][-1]}</span></h3><p>Daily cost by category, indexed to a late-September baseline of "
        "100, with migration events marked.</p></div></div>"
    )
    return page("Finks reports", body, "")


# `bar` and `spark` are a hand-kept copy of the same functions in finks-lakehouse's
# evals/report/render.py (the eval scorecard uses them there too). Keep the two in sync by hand.
def bar(c: Counter, w: int = 260, h: int = 12) -> str:
    n = c.total() or 1
    x, rects = 0.0, []
    for s in STATUSES:
        if c[s]:
            dx = w * c[s] / n
            rects.append(
                f'<rect x="{x:.2f}" width="{dx:.2f}" height="{h}" class="f-{s}"><title>{c[s]} {s}</title></rect>'
            )
            x += dx
    label = ", ".join(f"{c[s]} {s}" for s in STATUSES if c[s])
    return (
        f'<svg class="bar" viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" role="img" '
        f'aria-label="{label}">{"".join(rects)}</svg>'
    )


def spark(rates: list[float | None], w: int = 180, h: int = 32) -> str:
    """Pass-rate sparkline; the y axis spans at least 5 pp so noise stays flat."""
    pts = [(i, r) for i, r in enumerate(rates) if r is not None]
    if not pts:
        return ""
    lo, hi = min(r for _, r in pts), max(r for _, r in pts)
    pad = max(0.0, 5 - (hi - lo)) / 2
    lo, hi = lo - pad, hi + pad
    step = (w - 8) / max(len(rates) - 1, 1)
    xy = [
        (4 + i * step if len(rates) > 1 else w / 2, 4 + (h - 8) * (hi - r) / (hi - lo))
        for i, r in pts
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    x, y = xy[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="pass rate '
        f'{pts[0][1]:.1f}% to {pts[-1][1]:.1f}% over {len(rates)} snapshots">'
        f'<polyline points="{line}" fill="none" stroke="#1d4ed8" stroke-width="1.5"/>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#1d4ed8"/></svg>'
    )


def site(m: dict) -> dict[str, str]:
    return {
        "index.html": index_page(m),
        "private/index.html": private_page(m["private"]),
        "roadmap/index.html": roadmap_page(m["roadmap"]),
        "lake/index.html": lake_page(m["lake"]),
        "evals/index.html": evals_page(m["eval"]),
        "costs/index.html": costs_page(m["cost"]),
    }


def write(files: dict[str, str], out: Path) -> None:
    if out.exists():
        if next(
            (
                p
                for p in out.rglob("*")
                if not p.is_dir() and str(p.relative_to(out)) not in FILES
            ),
            None,
        ):
            sys.exit(f"{out} holds files other than the site: refusing to replace it")
        shutil.rmtree(out)
    for rel, text in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text)
    try:
        scan(out)
    except Leak:
        shutil.rmtree(out)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render")
    ap.add_argument("--model", type=Path, required=True, help="the model JSON to render")
    ap.add_argument("--out", type=Path, required=True, help="directory to write the site into")
    a = ap.parse_args(argv)
    m = json.loads(a.model.read_text())
    if m.get("version") != 1:
        sys.exit("refused: model version must be 1")
    try:
        write(site(m), a.out)
    except Leak as e:
        sys.exit(f"refused: {e}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
