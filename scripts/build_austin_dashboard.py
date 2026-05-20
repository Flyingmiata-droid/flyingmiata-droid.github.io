#!/usr/bin/env python3
"""
RigBrain — Rebuild the live /austin/ dashboard from real Socrata data.

Called by .github/workflows/refresh-dashboard.yml on a Monday cron.
Standalone run for local testing:
    python scripts/build_austin_dashboard.py

What it does:
- Pulls last 60 days of commercial BP permits from Austin Socrata (3syk-w9eu).
- Filters out master-permit-bleed records and bogus values.
- Dedupes by masterpermitnum.
- Scores per the rubric documented in the dashboard footer.
- Picks the top 5 by score+value.
- Rewrites the <!-- BEGIN PERMITS --> ... <!-- END PERMITS --> block in
  austin/index.html plus the four #stat-* cells and #last-updated stamp.

Phones are intentionally REDACTED in the public dashboard (shown as
"direct line in paid report"). The phones ARE in the public Socrata feed
— this is a positioning choice to preserve paid-product value.
"""

import datetime as dt
import html
import json
import pathlib
import re
import sys
from urllib.parse import quote
from urllib.request import urlopen

API = "https://data.austintexas.gov/resource/3syk-w9eu.json"
DAYS_BACK = 60
TOP_N = 5
SITE_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD_PATH = SITE_REPO_ROOT / "austin" / "index.html"


def fetch_permits() -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT00:00:00.000")
    where = f"issue_date > '{cutoff}'"
    url = (
        f"{API}?$where={quote(where)}"
        f"&permit_class_mapped=Commercial&permittype=BP"
        f"&$order=issue_date%20DESC&$limit=500"
    )
    with urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def best_val(r: dict) -> float:
    for c in (
        "total_job_valuation",
        "total_valuation_remodel",
        "building_valuation",
        "building_valuation_remodel",
    ):
        try:
            v = float(r.get(c, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            return v
    return 0


def best_sqft(r: dict) -> float:
    try:
        s = float(r.get("total_new_add_sqft", 0) or 0)
    except (TypeError, ValueError):
        s = 0
    try:
        r2 = float(r.get("remodel_repair_sqft", 0) or 0)
    except (TypeError, ValueError):
        r2 = 0
    return max(s, r2)


def floors(r: dict) -> int:
    try:
        return int(float(r.get("number_of_floors", 0) or 0))
    except (TypeError, ValueError):
        return 0


def score(r: dict) -> int:
    v, s, f = best_val(r), best_sqft(r), floors(r)
    pts = 50
    if v >= 20_000_000:
        pts += 20
    elif v >= 10_000_000:
        pts += 15
    elif v >= 5_000_000:
        pts += 10
    elif v >= 1_000_000:
        pts += 5
    if s >= 50_000:
        pts += 10
    elif s >= 10_000:
        pts += 5
    if f >= 4:
        pts += 10
    elif f >= 2:
        pts += 5
    if (r.get("work_class", "") or "").lower() in ("new", "shell", "addition", "addition and remodel"):
        pts += 5
    return max(0, min(100, pts))


def signals(r: dict) -> list[str]:
    v, s, f, wc = best_val(r), best_sqft(r), floors(r), r.get("work_class", "")
    out = []
    if v >= 20_000_000:
        out.append("$20M+ value")
    elif v >= 10_000_000:
        out.append("$10M+ value")
    elif v >= 5_000_000:
        out.append("$5M+ value")
    elif v >= 1_000_000:
        out.append("$1M+ value")
    if s >= 50_000:
        out.append("50K+ sqft")
    elif s >= 10_000:
        out.append("10K+ sqft")
    if f >= 4:
        out.append(f"{f} floors")
    elif f >= 2:
        out.append(f"{f} floors")
    if wc:
        out.append(wc)
    return out


def equipment(r: dict) -> list[str]:
    desc = ((r.get("description", "") or "") + " " + (r.get("work_class", "") or "")).lower()
    f, s = floors(r), best_sqft(r)
    if f >= 6 or (f >= 4 and s >= 50_000):
        return ["Tower Cranes", "Cranes", "Boom Lifts", "Concrete Equipment"]
    if "multifamily" in desc or "multi-family" in desc:
        return ["Excavators", "Skid Steers", "Boom Lifts", "Concrete Equipment", "Aerial Lifts"]
    if "shell" in desc:
        return ["Tower Cranes", "Cranes", "Boom Lifts", "Concrete Equipment"]
    if any(k in desc for k in ("warehouse", "industrial")):
        return ["Skid Steers", "Boom Lifts", "Material Handling", "Excavators"]
    if "new" in desc and s >= 20_000:
        return ["Excavators", "Skid Steers", "Loaders", "Boom Lifts", "Concrete Equipment"]
    if any(k in desc for k in ("earthwork", "site work", "grading", "pad", "foundation")):
        return ["Excavators", "Skid Steers", "Dozers", "Loaders"]
    if "hotel" in desc:
        return ["Tower Cranes", "Cranes", "Boom Lifts", "Concrete Equipment"]
    if any(k in desc for k in ("interior", "remodel", "finish-out", "tenant")):
        return ["Boom Lifts", "Scissor Lifts", "Material Handling"]
    return ["Boom Lifts", "Skid Steers"]


def filter_and_pick(records: list[dict]) -> list[dict]:
    cutoff = dt.date.today() - dt.timedelta(days=DAYS_BACK)
    candidates = []
    for r in records:
        try:
            idate = dt.datetime.strptime((r.get("issue_date", "") or "")[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if idate < cutoff:
            continue
        v = best_val(r)
        s = best_sqft(r)
        if v > 500_000_000:
            continue  # bogus
        if not r.get("contractor_company_name"):
            continue
        # Master-permit value bleed: $10M+ value on sub-5K-sqft permit = data error
        if v >= 10_000_000 and s < 5_000:
            continue
        if v < 1_000_000 and s < 10_000:
            continue
        candidates.append(r)
    # Dedupe by master permit
    seen: dict[str, dict] = {}
    for r in candidates:
        key = r.get("masterpermitnum") or r.get("permit_number")
        if key not in seen or score(r) > score(seen[key]):
            seen[key] = r
    return sorted(seen.values(), key=lambda r: (score(r), best_val(r)), reverse=True)[:TOP_N]


def fmt_val(v: float) -> str:
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return "(undisclosed)" if v == 0 else f"${v:,.0f}"


def render_row(i: int, r: dict) -> str:
    v = best_val(r)
    s = best_sqft(r)
    f = floors(r)
    sc = score(r)
    score_cls = "score-high" if sc >= 75 else ("score-mid" if sc >= 60 else "score-low")
    company = html.escape(re.sub(r"\*+\s*main\s*\**", "", r.get("contractor_company_name", ""), flags=re.IGNORECASE).strip(" *"))
    contact = html.escape(r.get("contractor_full_name", "") or "")
    addr = html.escape((r.get("original_address1", "") or "") + ", " + (r.get("original_zip", "") or "")[:5])
    desc_short = html.escape(re.sub(r"\s+", " ", (r.get("description", "") or "").strip())[:180])
    val_str = fmt_val(v)
    meta = [p for p in [addr, (f"{f} floor(s)" if f else ""), (f"{int(s):,} sqft" if s else "")] if p]
    project_html = " &middot; ".join(meta)
    sig_pills = "".join(f'<span class="sig-pill">{html.escape(x)}</span>' for x in signals(r))
    eq_pills = "".join(f'<span class="eq-pill">{html.escape(x)}</span>' for x in equipment(r))
    link = r.get("link", {}).get("url", "") if isinstance(r.get("link"), dict) else ""
    issue = (r.get("issue_date", "") or "")[:10]
    contact_line = (f" &middot; {contact}" if contact else "") + ' &middot; <span class="phone">(direct line in paid report)</span>'
    permit_link_html = (
        f'\n      <div class="permit-link"><a href="{html.escape(link)}" target="_blank" rel="noopener">View permit on AustinBuild</a></div>'
        if link else ""
    )
    return f"""    <div class="lead">
      <div class="lead-header">
        <span class="rank">#{i}</span>
        <span class="score {score_cls}">Score {sc}</span>
        <span class="value">{val_str}</span>
        <span class="issue-date">Issued {issue}</span>
      </div>
      <div class="contractor">
        <strong>{company}</strong>{contact_line}
      </div>
      <div class="project">
        <span class="addr">{project_html}</span>
      </div>
      <div class="description">{desc_short}</div>
      <div class="signals-row">
        <span class="sig-label">Why this scored:</span>
        {sig_pills}
      </div>
      <div class="equipment-row">
        <span class="eq-label">Likely equipment:</span>
        {eq_pills}
      </div>{permit_link_html}
    </div>"""


def main() -> int:
    print(f"Fetching last {DAYS_BACK} days of Austin commercial BP permits...")
    raw = fetch_permits()
    print(f"  pulled: {len(raw)} permits")
    picked = filter_and_pick(raw)
    print(f"  kept after filter+dedupe: {len(picked)}")
    if not picked:
        print("ERROR: zero permits passed filter; refusing to write empty dashboard.")
        return 1
    rows_html = "\n".join(render_row(i, r) for i, r in enumerate(picked, 1))
    total = sum(best_val(r) for r in picked if best_val(r) > 0)
    known = sum(1 for r in picked if best_val(r) > 0)
    avg = total / known if known else 0
    gcs = len({r.get("contractor_company_name") for r in picked})
    today = dt.date.today().isoformat()

    src = DASHBOARD_PATH.read_text(encoding="utf-8")
    new_block = "<!-- BEGIN PERMITS -->\n" + rows_html + "\n    <!-- END PERMITS -->"
    src, n1 = re.subn(
        r"<!-- BEGIN PERMITS -->.*?<!-- END PERMITS -->",
        lambda _m: new_block,
        src,
        count=1,
        flags=re.DOTALL,
    )
    src, n2 = re.subn(
        r'<strong id="last-updated">[^<]+</strong>',
        f'<strong id="last-updated">{today}</strong>',
        src,
        count=1,
    )
    def upd(text: str, sid: str, value) -> tuple[str, int]:
        return re.subn(
            rf'(<div class="value" id="{sid}">)[^<]+(</div>)',
            rf"\g<1>{value}\g<2>",
            text,
            count=1,
        )
    src, n3 = upd(src, "stat-count", len(picked))
    src, n4 = upd(src, "stat-total", f"${total/1e6:.1f}M")
    src, n5 = upd(src, "stat-avg", f"${avg/1e6:.1f}M")
    src, n6 = upd(src, "stat-gcs", gcs)
    if not all([n1, n2, n3, n4, n5, n6]):
        print(f"ERROR: substitution failure (n1={n1} n2={n2} n3={n3} n4={n4} n5={n5} n6={n6})")
        return 2
    DASHBOARD_PATH.write_text(src, encoding="utf-8")
    print(f"Wrote {DASHBOARD_PATH} — stats: count={len(picked)} total=${total/1e6:.1f}M avg=${avg/1e6:.1f}M gcs={gcs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
