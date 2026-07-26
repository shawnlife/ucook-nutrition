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
    # Protein ≥50g required for Gold; ≥40g required for Silver
    if rank == "Gold" and p < 50:
        rank = "Silver" if p >= 40 else "Bronze"
    elif rank == "Silver" and p < 40:
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

            spice_map = {"HOT": "🌶🌶🌶", "MEDIUM": "🌶🌶", "MILD": "🌶", "NONE": "", None: "", "": ""}
            spice = spice_map.get(dish.get("spiceLevel"), "")

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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UCook Nutrition — This Week</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F468;&#x200D;&#x1F373;</text></svg>">
<style>
  :root {
    --bg: #f5f5f0;
    --surface: #ffffff;
    --text: #222222;
    --text-muted: #888888;
    --text-dim: #555555;
    --border: #f0f0eb;
    --header-bg: #1a1a1a;
    --header-hover: #2e2e2e;
    --btn-green: #1b5e20;
    --btn-green-hover: #145a1c;
    --link-color: #2e7d32;
    --gold: #ffd700;
    --gold-text: #6b4c00;
    --silver: #c0c0c0;
    --silver-text: #333333;
    --bronze: #cd7f32;
    --row-hover: #fafaf7;
    --val-green: #1b5e20;
    --val-red: #c62828;
    --flag-bg: #ffebee;
    --flag-border: #ef9a9a;
    --flag-orange: #e65100;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px 20px;
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 16px;
  }
  h1 { font-size: 1.4rem; font-weight: 700; }
  .meta { font-size: 0.82rem; color: var(--text-muted); margin-top: 3px; }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input[type=search] {
    padding: 7px 12px;
    border: 1px solid #ccc;
    border-radius: 8px;
    font-size: 0.88rem;
    width: 200px;
    background: var(--surface);
  }
  input[type=search]:focus { outline: 2px solid var(--btn-green); outline-offset: 1px; }
  .btn {
    padding: 7px 14px;
    border: none;
    border-radius: 8px;
    font-size: 0.88rem;
    cursor: pointer;
    font-weight: 600;
  }
  .btn-dl       { background: var(--btn-green); color: white; }
  .btn-dl:hover { background: var(--btn-green-hover); }
  .btn-refresh       { background: var(--header-bg); color: white; }
  .btn-refresh:hover { background: var(--header-hover); }

  /* Collapsible legend */
  .legend-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 0.84rem;
    font-weight: 600;
    color: var(--text-muted);
    user-select: none;
    margin-bottom: 10px;
    background: none;
    border: none;
    padding: 0;
  }
  .legend-toggle:hover { color: var(--text); }
  .legend-arrow { font-size: 0.7rem; transition: transform 0.2s; display: inline-block; }
  .legend-arrow.open { transform: rotate(90deg); }
  .legend-body { display: none; margin-bottom: 14px; }
  .legend-body.open { display: block; }
  .legend-row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.82rem;
    margin-bottom: 5px;
    flex-wrap: wrap;
  }
  .rank-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .rank-dot-Gold     { background: var(--gold); }
  .rank-dot-Silver   { background: var(--silver); }
  .rank-dot-Bronze   { background: var(--bronze); }
  .rank-dot-Unranked { background: #e0e0e0; }
  .rank-dot-lg {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .rank-dot-lg.gold   { background: var(--gold); }
  .rank-dot-lg.silver { background: var(--silver); }
  .rank-dot-lg.bronze { background: var(--bronze); }

  .threshold-table { border-collapse: collapse; font-size: 0.8rem; margin-top: 8px; }
  .threshold-table th, .threshold-table td { padding: 4px 10px; border: 1px solid #ddd; text-align: center; }
  .threshold-table th { background: #f0f0eb; font-weight: 600; }
  .threshold-table .tg { color: var(--val-green); font-weight: 600; }
  .threshold-table .tr { color: var(--val-red); font-weight: 600; }

  .flag-reason { font-size: 0.73rem; color: var(--flag-orange); margin-top: 2px; }
  .red-flags { font-size: 0.73rem; margin-top: 3px; line-height: 1.8; }
  .red-flag-pill {
    display: inline-block;
    background: var(--flag-bg);
    border: 1px solid var(--flag-border);
    border-radius: 4px;
    padding: 1px 5px;
    margin: 1px 2px 1px 0;
    white-space: nowrap;
    color: var(--val-red);
  }

  .table-wrap {
    overflow-x: auto;
    border-radius: 10px;
    box-shadow: 0 2px 14px rgba(0,0,0,0.09);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    font-size: 0.84rem;
  }
  thead tr { background: var(--header-bg); color: white; }
  th {
    padding: 10px 11px;
    text-align: left;
    white-space: nowrap;
    user-select: none;
    cursor: pointer;
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--header-bg);
  }
  th.num { text-align: right; }
  th:hover { background: var(--header-hover); }
  th.sorted-asc::after  { content: " ▲"; font-size: 0.65em; opacity: 0.8; }
  th.sorted-desc::after { content: " ▼"; font-size: 0.65em; opacity: 0.8; }
  td { padding: 9px 11px; border-bottom: 1px solid var(--border); vertical-align: middle; background: var(--surface); }
  tr:last-child td { border-bottom: none; }
  tbody tr:hover td { background: var(--row-hover); }
  tr.excluded { opacity: 0.45; }

  .meal-cell { position: sticky; left: 0; z-index: 1; min-width: 180px; }
  th.meal-cell { z-index: 3; }
  tbody tr:hover .meal-cell { background: var(--row-hover); }

  .meal-name {
    font-weight: 600;
    line-height: 1.3;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .meal-sub  { font-size: 0.78rem; color: var(--text-muted); margin-top: 2px; }
  .meal-link { font-size: 0.76rem; }
  .meal-link a { color: var(--link-color); text-decoration: none; }
  .meal-link a:hover { text-decoration: underline; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .count { color: #999; font-size: 0.82rem; margin-bottom: 10px; }

  .val-green { color: var(--val-green); font-weight: 700; }
  .val-red   { color: var(--val-red);   font-weight: 700; }

  .swipe-hint {
    display: none;
    font-size: 0.75rem;
    color: var(--text-muted);
    text-align: center;
    margin-bottom: 6px;
  }

  @media (max-width: 700px) {
    body { padding: 16px 0; }
    header { padding: 0 16px; }
    .legend-toggle { padding: 0 16px; }
    .legend-body { padding: 0 16px; }
    .count { padding: 0 16px; }
    .swipe-hint { display: block; }
    .table-wrap { border-radius: 0; }
    input[type=search] { width: 150px; }
  }
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

<button class="legend-toggle" onclick="toggleLegend()" id="legendToggle">
  <span class="legend-arrow" id="legendArrow">▶</span> Ranking Guide
</button>
<div class="legend-body" id="legendBody">
  <div class="legend-row">
    <span class="rank-dot-lg gold"></span>
    <strong>Gold</strong> — 3+ greens, 0 reds, protein ≥50g
  </div>
  <div class="legend-row">
    <span class="rank-dot-lg silver"></span>
    <strong>Silver</strong> — (2+ greens, 0 reds) or (3+ greens, 1 red), protein ≥40g
  </div>
  <div class="legend-row">
    <span class="rank-dot-lg bronze"></span>
    <strong>Bronze</strong> — 1+ green, ≤1 red, protein ≥30g
  </div>
  <div class="legend-row">
    <strong>NR</strong> — 2+ reds, or protein &lt;30g, or beetroot/deep-fried
  </div>
  <table class="threshold-table">
    <thead>
      <tr>
        <th>Nutrient</th>
        <th class="tg">Green (good)</th>
        <th>Black (ok)</th>
        <th class="tr">Red (penalty)</th>
      </tr>
    </thead>
    <tbody>
      <tr><td>Protein</td><td class="tg">≥50g</td><td>35–49g</td><td class="tr">&lt;35g</td></tr>
      <tr><td>Fibre</td><td class="tg">≥10g</td><td>5–9g</td><td class="tr">&lt;5g</td></tr>
      <tr><td>Sat Fat</td><td class="tg">≤8g</td><td>9–15g</td><td class="tr">&gt;15g</td></tr>
      <tr><td>Sodium</td><td class="tg">≤800mg</td><td>801–1500mg</td><td class="tr">&gt;1500mg</td></tr>
      <tr><td>Kcal</td><td class="tg">500–800</td><td>801–1100</td><td class="tr">&gt;1100</td></tr>
    </tbody>
  </table>
</div>

<div class="count" id="count"></div>
<div class="swipe-hint" id="swipeHint">← Swipe to see all columns →</div>

<div class="table-wrap">
  <table id="tbl">
    <thead>
      <tr>
        <th class="meal-cell" onclick="sortBy('name')" data-col="name">Meal</th>
        <th onclick="sortBy('category')" data-col="category">Category</th>
        <th onclick="sortBy('cookWithin')" data-col="cookWithin" class="num">Eat within</th>
        <th onclick="sortBy('protein')" data-col="protein" class="num">Protein (g)</th>
        <th onclick="sortBy('fibre')" data-col="fibre" class="num">Fibre (g)</th>
        <th onclick="sortBy('saturatedFat')" data-col="saturatedFat" class="num">Sat Fat (g)</th>
        <th onclick="sortBy('sodium')" data-col="sodium" class="num">Sodium (mg)</th>
        <th onclick="sortBy('kcal')" data-col="kcal" class="num">Kcal</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
const RAW = __DATA__;

let sortStack = [{ col: 'rankOrder', dir: 1 }];

const STRING_COLS = new Set(['name','category']);
const DEFAULT_DIR = col => (STRING_COLS.has(col) || col === 'rankOrder' || col === 'cookWithin') ? 1 : -1;

function sortBy(col) {
  if (sortStack[0].col === col) {
    sortStack[0].dir *= -1;
  } else {
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

// Color functions — thresholds match Python rank_meal exactly
function cProtein(v)  { v=Number(v); return v>=50?' val-green':v<35?' val-red':''; }
function cFibre(v)    { v=Number(v); return v>=10?' val-green':v<5?' val-red':''; }
function cSatFat(v)   { v=Number(v); return v<=8?' val-green':v>15?' val-red':''; }
function cSodium(v)   { v=Number(v); return v<=800?' val-green':v>1500?' val-red':''; }
function cKcal(v)     { v=Number(v); return (v>=500&&v<=800)?' val-green':v>1100?' val-red':''; }

function redFlags(m) {
  const flags = [];
  if (m.protein < 35)        flags.push('Protein &lt;35g');
  if (m.fibre < 5)           flags.push('Fibre &lt;5g');
  if (m.saturatedFat > 15)   flags.push('Sat Fat &gt;15g');
  if (m.sodium > 1500)       flags.push('Sodium &gt;1500mg');
  if (m.kcal > 1100)         flags.push('Kcal &gt;1100');
  if (m.carbs > 60)          flags.push('Carbs &gt;60g');
  if (m.sugars > 20)         flags.push('Sugar &gt;20g');
  if (!flags.length) return '';
  return `<div class="red-flags">${flags.map(f => `<span class="red-flag-pill">⚑ ${f}</span>`).join('')}</div>`;
}

function dotHtml(rank) {
  if (rank === 'Unranked') return '';
  return `<span class="rank-dot rank-dot-${rank}" title="${rank}"></span>`;
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

  document.querySelectorAll('th').forEach(th => {
    th.classList.remove('sorted-asc','sorted-desc');
    const idx = sortStack.findIndex(s => s.col === th.dataset.col);
    if (idx === 0) th.classList.add(sortStack[0].dir === 1 ? 'sorted-asc' : 'sorted-desc');
  });

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = rows.map(m => `
    <tr class="row-${m.rank}${m.excluded ? ' excluded' : ''}">
      <td class="meal-cell">
        <div class="meal-name">
          ${dotHtml(m.rank)}${esc(m.name)}
          ${m.spice ? `<span title="${m.spice === '🌶🌶🌶' ? 'Hot' : m.spice === '🌶🌶' ? 'Medium' : 'Mild'}">${m.spice}</span>` : ''}
          ${m.mushrooms ? '<span title="Contains mushrooms">🍄</span>' : ''}
        </div>
        ${m.subTitle ? `<div class="meal-sub">${esc(m.subTitle)}</div>` : ''}
        ${m.flagged ? `<div class="flag-reason">⚠️ ${esc(m.flagReason)}</div>` : ''}
        ${redFlags(m)}
        <div class="meal-link"><a href="${m.url}" target="_blank">View on UCook ↗</a></div>
      </td>
      <td style="white-space:nowrap;font-size:0.8rem;color:var(--text-dim)">${esc(m.category)}</td>
      <td class="num" style="white-space:nowrap">${m.cookWithin ? m.cookWithin + ' days' : '—'}</td>
      <td class="num${cProtein(m.protein)}">${fmt(m.protein)}</td>
      <td class="num${cFibre(m.fibre)}">${fmt(m.fibre)}</td>
      <td class="num${cSatFat(m.saturatedFat)}">${fmt(m.saturatedFat)}</td>
      <td class="num${cSodium(m.sodium)}">${Math.round(m.sodium)}</td>
      <td class="num${cKcal(m.kcal)}">${Math.round(m.kcal)}</td>
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

function toggleLegend() {
  const body = document.getElementById('legendBody');
  const arrow = document.getElementById('legendArrow');
  body.classList.toggle('open');
  arrow.classList.toggle('open');
}

// Hide swipe hint after first swipe
document.querySelector('.table-wrap').addEventListener('touchstart', () => {
  document.getElementById('swipeHint').style.display = 'none';
}, { once: true });

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
