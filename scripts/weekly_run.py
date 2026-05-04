#!/usr/bin/env python3
"""
RigBrain — Weekly run: pull permits + regenerate sample lead report.

Where this file goes in the GitHub repo:
    scripts/weekly_run.py

Called by .github/workflows/weekly-permit-pull.yml on a Monday cron.

Outputs (under ./out/):
    austin_permits_YYYYMMDD.csv
    austin_permits_YYYYMMDD.json
    sample_lead_report.html    (always overwritten with the latest)
    sample_lead_report.pdf     (always overwritten with the latest)

Standalone run for local testing:
    python scripts/weekly_run.py --days 60 --min-value 1000000
"""

import argparse
import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import urlopen

API = "https://data.austintexas.gov/resource/3syk-w9eu.json"

EQUIPMENT_LEXICON = {
    "Excavators": ["excavat", "earthwork", "site work", "foundation", "utility trench"],
    "Dozers": ["grad", "site prep", "earthwork", "demoli", "demolition"],
    "Loaders": ["site work", "load", "earthwork", "demoli"],
    "Boom Lifts": ["story", "stories", "high", "tall", "steel", "tilt"],
    "Tower Cranes": ["tower", "high-rise", "5 story", "6 story", "7 story", "8 story", "9 story", "10 story", "garage"],
    "Cranes": ["steel", "tilt wall", "warehouse", "shell", "pre-engineered"],
    "Scissor Lifts": ["interior", "finish", "ceiling", "drywall", "MEP"],
    "Concrete Equipment": ["concrete", "tilt wall", "foundation", "garage", "paving"],
    "Skid Steers": ["site work", "landscape", "drive", "parking", "small commercial"],
    "Material Handling": ["warehouse", "industrial", "logistics", "distribution"],
}

HIGH_INTENT_CLASSES = {"New", "Shell", "Addition and Remodel"}


# ---------- pull + score ----------

def build_query(days_back: int, min_value: int) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000")
    where = (
        f"issue_date > '{cutoff}' "
        f"AND permit_class_mapped = 'Commercial' "
        f"AND total_job_valuation > {min_value}"
    )
    return f"{API}?$where={quote(where)}&$order=total_job_valuation%20DESC&$limit=200"


def fetch_permits(url: str) -> list[dict]:
    with urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def tag_equipment(description: str, work_class: str) -> list[str]:
    desc = (description or "").lower()
    matches = []
    for fam, keywords in EQUIPMENT_LEXICON.items():
        if any(k in desc for k in keywords):
            matches.append(fam)
    if work_class == "New" and not matches:
        matches.extend(["Excavators", "Dozers", "Loaders"])
    return matches


def score(permit: dict) -> int:
    s = 50
    val = float(permit.get("total_job_valuation") or 0)
    sqft = float(permit.get("total_new_add_sqft") or 0)
    floors = int(float(permit.get("number_of_floors") or 1))
    work_class = permit.get("work_class", "")

    if val >= 20_000_000: s += 20
    elif val >= 10_000_000: s += 15
    elif val >= 5_000_000: s += 10
    elif val >= 1_000_000: s += 5

    if sqft >= 50_000: s += 10
    elif sqft >= 10_000: s += 5

    if floors >= 4: s += 10
    elif floors >= 2: s += 5

    if work_class in HIGH_INTENT_CLASSES: s += 5

    return min(100, max(0, s))


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw or ""


def transform(permits: list[dict]) -> list[dict]:
    rows = []
    for p in permits:
        equipment = tag_equipment(p.get("description", ""), p.get("work_class", ""))
        rows.append({
            "score": score(p),
            "permit_number": p.get("permit_number", ""),
            "address": p.get("permit_location", ""),
            "zip": p.get("original_zip", ""),
            "description": (p.get("description", "") or "").strip().replace("\n", " "),
            "valuation": float(p.get("total_job_valuation") or 0),
            "sqft": float(p.get("total_new_add_sqft") or 0),
            "floors": p.get("number_of_floors", ""),
            "work_class": p.get("work_class", ""),
            "permit_class": p.get("permit_class", ""),
            "issue_date": (p.get("issue_date") or "")[:10],
            "contractor_company": p.get("contractor_company_name", ""),
            "contractor_name": p.get("contractor_full_name", ""),
            "contractor_phone": normalize_phone(p.get("contractor_phone", "")),
            "contractor_city": p.get("contractor_city", ""),
            "equipment": ", ".join(equipment),
            "permit_link": (p.get("link") or {}).get("url", ""),
        })
    rows.sort(key=lambda r: (-r["score"], -r["valuation"]))
    return rows


# ---------- report rendering ----------

def fmt_money(v: float) -> str:
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def score_class(s: int) -> str:
    if s >= 80: return "score-high"
    if s >= 65: return "score-mid"
    return "score-low"


def dedupe_top_n(rows: list[dict], n: int = 15) -> list[dict]:
    seen = {}
    for r in rows:
        key = (r["contractor_company"], r["address"])
        if key not in seen or r["score"] > seen[key]["score"]:
            seen[key] = r
    return sorted(seen.values(), key=lambda x: (-x["score"], -x["valuation"]))[:n]


def render_html(leads: list[dict]) -> str:
    count = len(leads)
    total = sum(l["valuation"] for l in leads)
    avg = total / count if count else 0
    unique_contractors = len({l["contractor_company"] for l in leads if l["contractor_company"]})

    rows_html = []
    for i, lead in enumerate(leads, 1):
        eq = lead.get("equipment") or "—"
        desc = (lead.get("description") or "").strip()
        if len(desc) > 180: desc = desc[:177] + "..."
        sqft = float(lead.get("sqft") or 0)
        sqft_str = f"{int(sqft):,} sqft" if sqft else ""
        rows_html.append(f"""
        <div class="lead">
          <div class="lead-header">
            <span class="rank">#{i}</span>
            <span class="score {score_class(lead['score'])}">Score {lead['score']}</span>
            <span class="value">{fmt_money(float(lead.get('valuation') or 0))}</span>
            <span class="issue-date">Permit issued {lead.get('issue_date','')}</span>
          </div>
          <div class="contractor">
            <strong>{lead.get('contractor_company') or 'Unknown'}</strong>
            {' &middot; ' + lead.get('contractor_name') if lead.get('contractor_name') else ''}
            &middot; <span class="phone">{lead.get('contractor_phone') or 'See Austin Open Data'}</span>
          </div>
          <div class="project">
            <span class="addr">{lead.get('address','')}</span>
            {' &middot; ' + str(lead.get('floors') or '') + ' floor(s)' if lead.get('floors') else ''}
            {' &middot; ' + sqft_str if sqft_str else ''}
          </div>
          <div class="description">{desc}</div>
          <div class="equipment-row">
            <span class="eq-label">Likely equipment:</span>
            {''.join(f'<span class="eq-pill">{e.strip()}</span>' for e in eq.split(',') if e.strip())}
          </div>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>RigBrain Sample Lead Report — Austin {datetime.now().strftime('%B %Y')}</title>
<style>
  @page {{ size: letter; margin: 0.6in; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
    color: #1a1a2e;
    background: #f7f7fa;
    line-height: 1.45;
    padding: 24px;
  }}
  .container {{ max-width: 880px; margin: 0 auto; background: white; padding: 36px; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ border-bottom: 3px solid #F59E0B; padding-bottom: 18px; margin-bottom: 24px; }}
  .brand {{ font-size: 0.85rem; color: #6B7280; letter-spacing: 1.5px; text-transform: uppercase; font-weight: 600; }}
  h1 {{ font-size: 1.85rem; color: #1a1a2e; margin-top: 4px; font-weight: 700; }}
  .subtitle {{ color: #4B5563; margin-top: 6px; font-size: 0.95rem; }}
  .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 24px 0 32px; padding: 20px; background: #f7f7fa; border-radius: 6px; }}
  .summary-item .label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; color: #6B7280; font-weight: 600; }}
  .summary-item .value {{ font-size: 1.5rem; font-weight: 700; color: #F59E0B; margin-top: 4px; }}
  .lead {{ border: 1px solid #E5E7EB; border-radius: 6px; padding: 16px 18px; margin-bottom: 14px; page-break-inside: avoid; }}
  .lead-header {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }}
  .rank {{ font-weight: 700; color: #6B7280; font-size: 0.95rem; }}
  .score {{ display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.3px; }}
  .score-high {{ background: rgba(16,185,129,0.15); color: #047857; }}
  .score-mid {{ background: rgba(245,158,11,0.15); color: #B45309; }}
  .score-low {{ background: rgba(107,114,128,0.15); color: #4B5563; }}
  .value {{ font-weight: 700; color: #1a1a2e; font-size: 1rem; }}
  .issue-date {{ color: #6B7280; font-size: 0.8rem; margin-left: auto; }}
  .contractor {{ font-size: 0.95rem; color: #1a1a2e; margin-bottom: 4px; }}
  .phone {{ color: #047857; font-weight: 600; }}
  .project {{ font-size: 0.85rem; color: #4B5563; margin-bottom: 6px; }}
  .description {{ font-size: 0.85rem; color: #4B5563; font-style: italic; margin-bottom: 8px; }}
  .equipment-row {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
  .eq-label {{ font-size: 0.75rem; color: #6B7280; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; margin-right: 4px; }}
  .eq-pill {{ background: #1a1a2e; color: white; padding: 3px 9px; border-radius: 10px; font-size: 0.72rem; font-weight: 600; }}
  .footer {{ margin-top: 32px; padding-top: 20px; border-top: 1px solid #E5E7EB; font-size: 0.85rem; color: #4B5563; }}
  .footer .cta {{ background: #F59E0B; color: #1a1a2e; padding: 14px 20px; border-radius: 6px; margin-top: 14px; text-align: center; font-weight: 600; }}
  .footer .cta a {{ color: #1a1a2e; text-decoration: none; }}
  .source-note {{ font-size: 0.72rem; color: #9CA3AF; margin-top: 14px; font-style: italic; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="brand">RigBrain &middot; PermitMatch</div>
    <h1>Austin Equipment Lead Report</h1>
    <div class="subtitle">Top {count} commercial construction permits issued in Travis County over the last 60 days, scored for equipment-purchase intent.</div>
  </div>

  <div class="summary">
    <div class="summary-item"><div class="label">Projects</div><div class="value">{count}</div></div>
    <div class="summary-item"><div class="label">Total Value</div><div class="value">{fmt_money(total)}</div></div>
    <div class="summary-item"><div class="label">Avg Project</div><div class="value">{fmt_money(avg)}</div></div>
    <div class="summary-item"><div class="label">Unique Contractors</div><div class="value">{unique_contractors}</div></div>
  </div>

  {''.join(rows_html)}

  <div class="footer">
    <div><strong>How this list was built:</strong> Pulled from the City of Austin Open Data permit feed, filtered for commercial new-construction permits over $1M issued in the last 60 days, scored for equipment-purchase intent based on project type, size, and vertical scope. Equipment tags inferred from permit description and project class.</div>
    <div class="cta">
      Want this delivered every Monday at 7am?<br>
      Reply to this email or visit <a href="https://rigbrain.io">rigbrain.io</a> &middot; $299/mo
    </div>
    <div class="source-note">
      Source: City of Austin Open Data Portal, dataset 3syk-w9eu. Public records, free to access. RigBrain handles the pull, scoring, dedupe, equipment tagging, and weekly delivery so your sales team doesn't have to.
    </div>
  </div>
</div>
</body>
</html>"""


# ---------- I/O helpers ----------

def write_csv(rows: list[dict], path: str) -> None:
    if not rows: return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_json(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


# ---------- entry ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--min-value", type=int, default=1_000_000)
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    print(f"[+] Pulling Austin commercial permits, last {args.days} days, value > ${args.min_value:,}")
    raw = fetch_permits(build_query(args.days, args.min_value))
    print(f"[+] {len(raw)} permits returned")

    rows = transform(raw)
    csv_path  = os.path.join(args.out_dir, f"austin_permits_{stamp}.csv")
    json_path = os.path.join(args.out_dir, f"austin_permits_{stamp}.json")
    write_csv(rows, csv_path)
    write_json(rows, json_path)
    print(f"[+] Wrote {csv_path}, {json_path}")

    leads = dedupe_top_n(rows, n=15)
    html = render_html(leads)
    html_path = os.path.join(args.out_dir, "sample_lead_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] Wrote {html_path}")

    # PDF (best effort — skip cleanly if weasyprint unavailable for local runs)
    try:
        from weasyprint import HTML
        pdf_path = os.path.join(args.out_dir, "sample_lead_report.pdf")
        HTML(string=html).write_pdf(pdf_path)
        print(f"[+] Wrote {pdf_path}")
    except ImportError:
        print("[!] weasyprint not installed — skipping PDF (install: pip install weasyprint)")


if __name__ == "__main__":
    main()
