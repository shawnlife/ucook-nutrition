#!/usr/bin/env python3
"""
UCook Nutrition Ranker
Fetches this week's UCook meals, applies ranking criteria, and opens a sortable browser table.
Run: python3 ucook_nutrition.py
"""

import json
import urllib.request
import os
import webbrowser
from datetime import date

GRAPHQL_URL = "https://graphql.ucook.co.za/graphql"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

MENU_QUERY = "{ activeMenu { menuDishes { list { id name slug } } } }"

DISH_QUERY = """
query GetDish($slug: String!) {
  oneMealKitDish(slug: $slug, status: [PUBLISHED]) {
    name
    subTitle
    slug
    description
    spiceLevel
    cookWithin
    overallTime { min max }
    sentIngredients
    mealKitCategories { title }
    nutritionPerServing {
      protein
      fibre
      fat
      saturatedFat
      carbs
      sugars
      salt
      energyInKiloCalories
    }
  }
}
"""


def graphql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_current_slugs():
    data = graphql(MENU_QUERY)
    dishes = (
        data.get("data", {})
        .get("activeMenu", {})
        .get("menuDishes", {})
        .get("list", [])
    )
    if not dishes:
        raise RuntimeError(f"Could not load active menu: {data}")
    seen = {}
    for dish in dishes:
        slug = dish.get("slug")
        if slug and slug not in seen:
            seen[slug] = dish.get("name", slug)
    return list(seen.keys())


def has_beetroot(dish):
    haystack = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        dish.get("description", ""),
        " ".join(dish.get("sentIngredients", [])),
    ]).lower()
    return "beetroot" in haystack


def is_primarily_fried(dish):
    haystack = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        dish.get("description", ""),
    ]).lower()
    # Primary frying indicators — not just "air fryer" which is different
    fry_words = ["deep fry", "deep-fry", "deep fried", "deep-fried"]
    if any(w in haystack for w in fry_words):
        return True
    # Name starts with or prominently features "fried"
    name = dish.get("name", "").lower()
    if name.startswith("fried ") or " fried " in name:
        return True
    return False


def has_mushrooms(dish):
    haystack = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        " ".join(dish.get("sentIngredients", [])),
    ]).lower()
    return "mushroom" in haystack


def rank_meal(n):
    """Return 'Gold', 'Silver', 'Bronze', or 'Unranked'.

    Green thresholds (one point each):
      Protein ≥50g | Fibre ≥10g | Sat Fat ≤8g | Sodium ≤800mg | Kcal 500–800

    Red thresholds (penalty):
      Protein <35g | Fibre <5g | Sat Fat >15g | Sodium >1500mg | Kcal >1100

    Ranking:
      Gold   = ≥3 greens + 0 reds + protein ≥40g
      Silver = (≥2 greens, 0 reds) or (≥3 greens, 1 red) + protein ≥40g
      Bronze = ≥1 green + ≤1 red  (or protein 30–39g with above)
      NR     = protein <30g, or 2+ reds, or 0 greens
    """
    p    = n.get("protein", 0) or 0
    f    = n.get("fibre", 0) or 0
    s    = n.get("saturatedFat", 0) or 0
    na   = n.get("salt", 0) or 0
    kcal = n.get("energyInKiloCalories", 0) or 0

    if p < 30:
        return "Unranked"

    greens = sum([p >= 50, f >= 10, s <= 8, na <= 800, 500 <= kcal <= 800])
    reds   = sum([p < 35,  f < 5,   s > 15, na > 1500, kcal > 1100])

    if greens >= 3 and reds == 0:
        rank = "Gold"
    elif (greens >= 2 and reds == 0) or (greens >= 3 and reds <= 1):
        rank = "Silver"
    elif greens >= 1 and reds <= 1:
        rank = "Bronze"
    else:
        rank = "Unranked"

    # Hard floor: under 30g protein = always Unranked
    if p < 30:
        return "Unranked"
    # Protein ≥40g required for Silver or Gold
    if rank in ("Gold", "Silver") and p < 40:
        rank = "Bronze"

    return rank


RANK_ORDER = {"Gold": 0, "Silver": 1, "Bronze": 2, "Unranked": 3}


def fetch_all_meals():
    print("Fetching this week's UCook menu...")
    slugs = get_current_slugs()
    meals = []
    skipped_lunch = 0
    for i, slug in enumerate(slugs, 1):
        print(f"  [{i}/{len(slugs)}] {slug}                    ", end="\r")
        try:
            data = graphql(DISH_QUERY, {"slug": slug})
            dish = data.get("data", {}).get("oneMealKitDish")
            if not dish:
                continue

            # Skip lunch meals
            categories = [c.get("title", "") for c in (dish.get("mealKitCategories") or [])]
            if any("lunch" in c.lower() for c in categories):
                skipped_lunch += 1
                continue

            n = dish.get("nutritionPerServing") or {}

            # Beetroot/fried meals are still shown but forced to Unranked
            flagged = has_beetroot(dish) or is_primarily_fried(dish)
            flag_reason = []
            if has_beetroot(dish):    flag_reason.append("Beetroot")
            if is_primarily_fried(dish): flag_reason.append("Fried")
            rank = "Unranked" if flagged else rank_meal(n)

            overall = dish.get("overallTime") or {}
            cook_min = overall.get("min", "")
            cook_max = overall.get("max", "")
            cook_time = f"{cook_min}–{cook_max} min" if cook_min else ""

            spice_map = {"HOT": "🌶🌶🌶", "MEDIUM": "🌶🌶", "MILD": "🌶", "NONE": "—", None: "—", "": "—"}
            spice = spice_map.get(dish.get("spiceLevel"), dish.get("spiceLevel") or "—")

            category = categories[0] if categories else ""

            meals.append({
                "name":          dish.get("name", ""),
                "subTitle":      dish.get("subTitle", ""),
                "slug":          slug,
                "url":           f"https://www.ucook.co.za/meal-kit/{slug}",
                "category":      category,
                "rank":          rank,
                "rankOrder":     RANK_ORDER[rank],
                "protein":       n.get("protein") or 0,
                "fibre":         n.get("fibre") or 0,
                "fat":           n.get("fat") or 0,
                "saturatedFat":  n.get("saturatedFat") or 0,
                "carbs":         n.get("carbs") or 0,
                "sugars":        n.get("sugars") or 0,
                "sodium":        n.get("salt") or 0,
                "kcal":          n.get("energyInKiloCalories") or 0,
                "spice":         spice,
                "cookTime":      cook_time,
                "cookWithin":    dish.get("cookWithin") or 0,
                "mushrooms":     has_mushrooms(dish),
                "flagged":       flagged,
                "flagReason":    ", ".join(flag_reason),
            })
        except Exception as e:
            print(f"\n  Warning: could not fetch {slug}: {e}")

    print(f"\nLoaded {len(meals)} meals ({skipped_lunch} lunch meals skipped).\n")
    return meals


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>UCook Nutrition — This Week</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f0;
    color: #222;
    padding: 28px 20px;
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 18px;
  }
  h1 { font-size: 1.4rem; font-weight: 700; }
  .meta { font-size: 0.82rem; color: #888; margin-top: 3px; }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input[type=search] {
    padding: 7px 12px;
    border: 1px solid #ccc;
    border-radius: 8px;
    font-size: 0.88rem;
    width: 200px;
    background: white;
  }
  .btn {
    padding: 7px 14px;
    border: none;
    border-radius: 8px;
    font-size: 0.88rem;
    cursor: pointer;
    font-weight: 600;
  }
  .btn-dl      { background: #1b5e20; color: white; }
  .btn-dl:hover { background: #145a1c; }
  .btn-refresh  { background: #1a1a1a; color: white; }
  .btn-refresh:hover { background: #333; }
  .legend {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 14px;
    font-size: 0.82rem;
  }
  .legend-item { display: flex; align-items: center; gap: 5px; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 700;
    white-space: nowrap;
  }
  .badge-Gold     { background: #ffd700; color: #6b4c00; }
  .badge-Silver   { background: #c0c0c0; color: #333; }
  .badge-Bronze   { background: #cd7f32; color: white; }
  .badge-Unranked { background: #e0e0e0; color: #666; }
  .badge-Excluded { background: #ffcdd2; color: #b71c1c; }
  .flag-reason { font-size: 0.73rem; color: #e65100; margin-top: 2px; }
  .red-flags { font-size: 0.73rem; color: #c62828; margin-top: 3px; line-height: 1.5; }
  .red-flag-pill {
    display: inline-block;
    background: #ffebee;
    border: 1px solid #ef9a9a;
    border-radius: 4px;
    padding: 1px 5px;
    margin: 1px 2px 1px 0;
    white-space: nowrap;
  }
  .mushroom { font-size: 1em; }
  .table-wrap {
    overflow-x: auto;
    border-radius: 10px;
    box-shadow: 0 2px 14px rgba(0,0,0,0.09);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    background: white;
    font-size: 0.84rem;
  }
  thead tr { background: #1a1a1a; color: white; }
  th {
    padding: 10px 11px;
    text-align: left;
    white-space: nowrap;
    user-select: none;
    cursor: pointer;
    position: sticky;
    top: 0;
    z-index: 1;
    background: #1a1a1a;
  }
  th.num { text-align: right; }
  th:hover { background: #2e2e2e; }
  th.sorted-asc::after  { content: " ▲"; font-size: 0.65em; opacity: 0.8; }
  th.sorted-desc::after { content: " ▼"; font-size: 0.65em; opacity: 0.8; }
  td { padding: 9px 11px; border-bottom: 1px solid #f0f0eb; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: #fafaf7; }
  tr.excluded { opacity: 0.45; }
  tr.row-Gold     td:first-child { border-left: 3px solid #ffd700; }
  tr.row-Silver   td:first-child { border-left: 3px solid #c0c0c0; }
  tr.row-Bronze   td:first-child { border-left: 3px solid #cd7f32; }
  tr.row-Unranked td:first-child { border-left: 3px solid #e0e0e0; }
  tr.row-Excluded td:first-child { border-left: 3px solid #ef9a9a; }
  .meal-name { font-weight: 600; line-height: 1.3; }
  .meal-sub  { font-size: 0.78rem; color: #888; margin-top: 2px; }
  .meal-link { font-size: 0.76rem; }
  .meal-link a { color: #2e7d32; text-decoration: none; }
  .meal-link a:hover { text-decoration: underline; }
  .excl-reason { font-size: 0.73rem; color: #c62828; margin-top: 2px; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .count { color: #999; font-size: 0.82rem; margin-bottom: 10px; }
  .show-excl-wrap { font-size: 0.82rem; color: #888; }
  label { cursor: pointer; }
</style>
</head>
<body>
<header>
  <div>
    <h1>UCook — This Week's Meals</h1>
    <div class="meta">Per serving &nbsp;·&nbsp; Generated __DATE__</div>
  </div>
  <div class="controls">
    <input type="search" id="search" placeholder="Search meals…" oninput="renderTable()">
    <button class="btn btn-dl" onclick="downloadCSV()">⬇ CSV</button>
    <button class="btn btn-refresh" id="refreshBtn" onclick="triggerRefresh()">↺ Refresh</button>
  </div>
</header>

<div class="legend">
  <div class="legend-item"><span class="badge badge-Gold">Gold</span> Protein ≥50g, Fibre ≥10g, Sat Fat ≤10g, Sodium ≤1200mg — all 4</div>
  <div class="legend-item"><span class="badge badge-Silver">Silver</span> 3 of 4 (≥50g protein, ≥8g fibre, ≤13g sat fat, ≤1500mg sodium)</div>
  <div class="legend-item"><span class="badge badge-Bronze">Bronze</span> 2 of 4 (≥40g protein, ≥6g fibre, ≤15g sat fat, ≤1800mg sodium)</div>
  <div class="legend-item">⚠️ Beetroot or primary frying (Unranked)</div>
  <div class="legend-item">🍄 Contains mushrooms &nbsp; 🔥 Over 1000 kcal</div>
  <div class="legend-item"><span class="red-flag-pill">⚑ flag</span> Sodium &gt;1800mg · Sat Fat &gt;20g · Sugar &gt;25g · Protein &lt;35g · Fibre &lt;5g</div>
</div>

<div class="count" id="count"></div>

<div class="table-wrap">
  <table id="tbl">
    <thead>
      <tr>
        <th onclick="sortBy('rankOrder')" data-col="rankOrder" class="sorted-asc">Rank</th>
        <th onclick="sortBy('category')" data-col="category">Category</th>
        <th onclick="sortBy('name')" data-col="name">Meal</th>
        <th onclick="sortBy('spice')" data-col="spice">Spice</th>
        <th onclick="sortBy('cookTime')" data-col="cookTime">Cook time</th>
        <th onclick="sortBy('cookWithin')" data-col="cookWithin" class="num">Eat within</th>
        <th onclick="sortBy('protein')" data-col="protein" class="num">Protein (g)</th>
        <th onclick="sortBy('fibre')" data-col="fibre" class="num">Fibre (g)</th>
        <th onclick="sortBy('fat')" data-col="fat" class="num">Fat (g)</th>
        <th onclick="sortBy('saturatedFat')" data-col="saturatedFat" class="num">Sat Fat (g)</th>
        <th onclick="sortBy('carbs')" data-col="carbs" class="num">Carbs (g)</th>
        <th onclick="sortBy('sugars')" data-col="sugars" class="num">Sugars (g)</th>
        <th onclick="sortBy('sodium')" data-col="sodium" class="num">Sodium (mg)</th>
        <th onclick="sortBy('kcal')" data-col="kcal" class="num">Kcal</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
const RAW = __DATA__;

// Multi-level sort: primary + secondary
// Each entry: { col, dir }  dir: 1=asc, -1=desc
let sortStack = [{ col: 'rankOrder', dir: 1 }];

const STRING_COLS = new Set(['name','spice','cookTime','category']);
const DEFAULT_DIR = col => (STRING_COLS.has(col) || col === 'rankOrder' || col === 'cookWithin') ? 1 : -1;

function sortBy(col) {
  if (sortStack[0].col === col) {
    sortStack[0].dir *= -1;
  } else {
    // New primary: push old primary to secondary (keep only 2 levels)
    sortStack = [{ col, dir: DEFAULT_DIR(col) }, sortStack[0]].slice(0, 2);
  }
  renderTable();
}

function cmpVal(a, b, col, dir) {
  let av = a[col], bv = b[col];
  if (av == null) av = typeof bv === 'string' ? '' : 0;
  if (bv == null) bv = typeof av === 'string' ? '' : 0;
  const r = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
  return dir * r;
}

function redFlags(m) {
  const flags = [];
  if (m.sodium > 1800)       flags.push('Sodium &gt;1800mg');
  if (m.saturatedFat > 20)   flags.push('Sat Fat &gt;20g');
  if (m.sugars > 25)         flags.push('Sugar &gt;25g');
  if (m.protein < 35)        flags.push('Protein &lt;35g');
  if (m.fibre < 5)           flags.push('Fibre &lt;5g');
  if (!flags.length) return '';
  return `<div class="red-flags">${flags.map(f => `<span class="red-flag-pill">⚑ ${f}</span>`).join('')}</div>`;
}

function renderTable() {
  const q = document.getElementById('search').value.toLowerCase();

  let rows = RAW.filter(m => {
    if (!q) return true;
    return m.name.toLowerCase().includes(q) || (m.subTitle||'').toLowerCase().includes(q) || (m.category||'').toLowerCase().includes(q);
  });

  rows.sort((a, b) => {
    for (const { col, dir } of sortStack) {
      const r = cmpVal(a, b, col, dir);
      if (r !== 0) return r;
    }
    return 0;
  });

  // Update header arrows — primary sort gets arrow, secondary gets dimmer indicator
  document.querySelectorAll('th').forEach(th => {
    th.classList.remove('sorted-asc','sorted-desc');
    const idx = sortStack.findIndex(s => s.col === th.dataset.col);
    if (idx === 0) th.classList.add(sortStack[0].dir === 1 ? 'sorted-asc' : 'sorted-desc');
  });

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = rows.map(m => `
    <tr class="row-${m.rank}${m.excluded ? ' excluded' : ''}">
      <td><span class="badge badge-${m.rank}">${m.rank}</span></td>
      <td style="white-space:nowrap;font-size:0.8rem;color:#555">${esc(m.category)}</td>
      <td>
        <div class="meal-name">
          ${esc(m.name)}
          ${m.mushrooms ? '<span title="Contains mushrooms">🍄</span>' : ''}
          ${m.kcal > 1000 ? '<span title="Over 1000 kcal">🔥</span>' : ''}
        </div>
        ${m.subTitle ? `<div class="meal-sub">${esc(m.subTitle)}</div>` : ''}
        ${m.flagged ? `<div class="flag-reason">⚠️ ${esc(m.flagReason)}</div>` : ''}
        ${redFlags(m)}
        <div class="meal-link"><a href="${m.url}" target="_blank">View on UCook ↗</a></div>
      </td>
      <td>${m.spice}</td>
      <td style="white-space:nowrap">${esc(m.cookTime)}</td>
      <td class="num" style="white-space:nowrap">${m.cookWithin ? m.cookWithin + ' days' : '—'}</td>
      <td class="num">${fmt(m.protein)}</td>
      <td class="num">${fmt(m.fibre)}</td>
      <td class="num">${fmt(m.fat)}</td>
      <td class="num">${fmt(m.saturatedFat)}</td>
      <td class="num">${fmt(m.carbs)}</td>
      <td class="num">${fmt(m.sugars)}</td>
      <td class="num">${Math.round(m.sodium)}</td>
      <td class="num">${Math.round(m.kcal)}</td>
    </tr>
  `).join('');

  document.getElementById('count').textContent =
    `${rows.length} meal${rows.length !== 1 ? 's' : ''}${q ? ` matching "${q}"` : ''}`;
}

function fmt(v) { return (v != null && v !== '') ? Number(v).toFixed(1) : '—'; }
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function downloadCSV() {
  const cols = ['rank','category','name','subTitle','url','spice','cookTime','protein','fibre','fat','saturatedFat','carbs','sugars','sodium','kcal','mushrooms'];
  const headers = ['Rank','Category','Name','Sub-title','URL','Spice','Cook Time','Protein (g)','Fibre (g)','Fat (g)','Sat Fat (g)','Carbs (g)','Sugars (g)','Sodium (mg)','Kcal','Mushrooms'];
  const lines = [headers.join(',')];
  [...RAW].sort((a,b) => a.rankOrder - b.rankOrder || b.protein - a.protein).forEach(m => {
    lines.push(cols.map(c => {
      const v = m[c] ?? '';
      const s = String(v);
      return (s.includes(',') || s.includes('"') || s.includes('\n')) ? `"${s.replace(/"/g,'""')}"` : s;
    }).join(','));
  });
  const blob = new Blob([lines.join('\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ucook_nutrition.csv';
  a.click();
}

async function triggerRefresh() {
  const btn = document.getElementById('refreshBtn');
  const STORAGE_KEY = 'ucook_gh_token';
  let token = localStorage.getItem(STORAGE_KEY);
  if (!token) {
    token = prompt('Enter your GitHub personal access token to enable one-click refresh:\n(Stored only in your browser — never sent anywhere except GitHub)');
    if (!token) return;
    localStorage.setItem(STORAGE_KEY, token.trim());
    token = token.trim();
  }
  btn.disabled = true;
  btn.textContent = 'Triggering…';
  try {
    const res = await fetch(
      'https://api.github.com/repos/shawnlife/ucook-nutrition/actions/workflows/update.yml/dispatches',
      {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref: 'main' })
      }
    );
    if (res.status === 204) {
      let secs = 90;
      const tick = setInterval(() => {
        secs--;
        btn.textContent = 'Updating… ' + secs + 's';
        if (secs <= 0) { clearInterval(tick); location.reload(); }
      }, 1000);
    } else if (res.status === 401) {
      localStorage.removeItem(STORAGE_KEY);
      btn.textContent = 'Token invalid — try again';
      btn.disabled = false;
    } else {
      btn.textContent = 'Failed — try again';
      btn.disabled = false;
    }
  } catch(e) {
    btn.textContent = 'Error — try again';
    btn.disabled = false;
  }
}

renderTable();
</script>
<footer style="text-align:center;padding:14px 0 18px;font-size:0.75rem;color:#aaa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  Tool made using AI vibe-coding by <a href="https://shawnlife.com" target="_blank" style="color:#aaa;text-decoration:underline;">ShawnLife</a>
</footer>
</body>
</html>"""


def build_html(meals):
    data_json = json.dumps(meals, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA__", data_json)
    html = html.replace("__DATE__", date.today().strftime("%-d %B %Y"))
    return html


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true", help="Skip opening browser (used in CI)")
    args = parser.parse_args()

    meals = fetch_all_meals()

    from collections import Counter
    tally = Counter(m["rank"] for m in meals)
    print("\nSummary:")
    for rank in ["Gold", "Silver", "Bronze", "Unranked"]:
        if tally[rank]:
            print(f"  {rank:10s}: {tally[rank]}")

    html = build_html(meals)
    base = os.path.dirname(os.path.abspath(__file__))

    # index.html → served by GitHub Pages
    index_path = os.path.join(base, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved: {index_path}")

    if not args.no_open:
        preview_path = os.path.join(base, "ucook_nutrition.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html)
        webbrowser.open(f"file://{preview_path}")


if __name__ == "__main__":
    main()
