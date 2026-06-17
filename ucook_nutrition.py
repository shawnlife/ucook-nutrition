#!/usr/bin/env python3
"""
UCook Nutrition Ranker
Fetches this week's UCook dinner meals, ranks them, and generates index.html.

Local use:   python3 ucook_nutrition.py
CI/headless: python3 ucook_nutrition.py --no-open
"""

import json
import urllib.request
import os
import sys
import webbrowser
from datetime import date
from collections import Counter

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
    name subTitle slug description
    cookWithin
    overallTime { min max }
    sentIngredients
    mealKitCategories { title }
    nutritionPerServing {
      protein fibre fat saturatedFat carbs sugars salt energyInKiloCalories
    }
  }
}
"""

RANK_ORDER = {"Gold": 0, "Silver": 1, "Bronze": 2, "Unranked": 3}

PROTEIN_SOURCES = [
    ("Ostrich",     ["ostrich"]),
    ("Venison",     ["venison", "springbok", "kudu", "impala", "warthog"]),
    ("Lamb",        ["lamb", "mutton"]),
    ("Wagyu Beef",  ["wagyu"]),
    ("Beef",        ["beef", "sirloin", "brisket", "ribeye", "rump", "oxtail"]),
    ("Beef Mince",  ["beef mince", "mince"]),
    ("Pork",        ["pork", "banger", "sausage", "chorizo", "bacon", "ham", "pancetta"]),
    ("Chicken",     ["chicken", "poultry"]),
    ("Duck",        ["duck"]),
    ("Salmon",      ["salmon"]),
    ("Trout",       ["trout"]),
    ("Hake",        ["hake"]),
    ("Swordfish",   ["swordfish"]),
    ("Fish",        ["fish fillet", "fish"]),
    ("Mussels",     ["mussel"]),
    ("Seafood",     ["prawn", "shrimp", "calamari", "squid", "crab", "lobster"]),
    ("Eggs",        ["egg"]),
    ("Tofu",        ["tofu", "tempeh"]),
]


# ── GraphQL ───────────────────────────────────────────────────────────────────

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


# ── Processing ────────────────────────────────────────────────────────────────

def detect_protein_source(ingredients, name):
    text = " ".join(list(ingredients) + [name]).lower()
    for label, keywords in PROTEIN_SOURCES:
        if any(k in text for k in keywords):
            return label
    return "Plant-based"


def has_beetroot(dish):
    text = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        dish.get("description", ""),
        *dish.get("sentIngredients", []),
    ]).lower()
    return "beetroot" in text


def is_primarily_fried(dish):
    text = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        dish.get("description", ""),
    ]).lower()
    if any(w in text for w in ["deep fry", "deep-fry", "deep fried", "deep-fried"]):
        return True
    name = dish.get("name", "").lower()
    return name.startswith("fried ") or " fried " in name


def has_mushrooms(dish):
    text = " ".join([
        dish.get("name", ""),
        dish.get("subTitle", ""),
        *dish.get("sentIngredients", []),
    ]).lower()
    return "mushroom" in text


def compute_rank(dish, n):
    if has_beetroot(dish) or is_primarily_fried(dish):
        return "Unranked"
    p  = n.get("protein", 0) or 0
    f  = n.get("fibre", 0) or 0
    s  = n.get("saturatedFat", 0) or 0
    na = n.get("salt", 0) or 0
    if p >= 50 and f >= 10 and s <= 10 and na <= 1200:
        return "Gold"
    if sum([p >= 50, f >= 8,  s <= 13, na <= 1500]) >= 3:
        return "Silver"
    if sum([p >= 40, f >= 6,  s <= 15, na <= 1800]) >= 2:
        return "Bronze"
    return "Unranked"


def compute_info_flags(dish):
    flags = []
    if has_beetroot(dish):       flags.append("Contains beetroot")
    if is_primarily_fried(dish): flags.append("Primarily fried")
    if has_mushrooms(dish):      flags.append("Contains mushrooms")
    return flags


def compute_red_flags(n, kcal):
    flags = []
    if (n.get("salt") or 0) > 1800:        flags.append("Sodium >1800mg")
    if (n.get("saturatedFat") or 0) > 20:  flags.append("Sat Fat >20g")
    if (n.get("sugars") or 0) > 25:        flags.append("Sugar >25g")
    if (n.get("protein") or 0) < 35:       flags.append("Protein <35g")
    if (n.get("fibre") or 0) < 5:          flags.append("Fibre <5g")
    if kcal > 1000:                         flags.append(">1000 kcal")
    return flags


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all_meals():
    print("Fetching this week's UCook menu...")
    slugs = get_current_slugs()
    meals = []
    skipped = 0

    for i, slug in enumerate(slugs, 1):
        print(f"  [{i}/{len(slugs)}] {slug}                    ", end="\r")
        try:
            data = graphql(DISH_QUERY, {"slug": slug})
            dish = data.get("data", {}).get("oneMealKitDish")
            if not dish:
                continue

            cats = [c.get("title", "") for c in (dish.get("mealKitCategories") or [])]
            if any("lunch" in c.lower() for c in cats):
                skipped += 1
                continue

            n    = dish.get("nutritionPerServing") or {}
            kcal = n.get("energyInKiloCalories") or 0
            t    = dish.get("overallTime") or {}
            rank = compute_rank(dish, n)

            meals.append({
                "name":          dish.get("name", ""),
                "subTitle":      dish.get("subTitle", ""),
                "slug":          slug,
                "url":           f"https://www.ucook.co.za/meal-kit/{slug}",
                "category":      cats[0] if cats else "",
                "proteinSource": detect_protein_source(
                                     dish.get("sentIngredients", []),
                                     dish.get("name", "")
                                 ),
                "rank":          rank,
                "rankOrder":     RANK_ORDER[rank],
                "cookTime":      f"{t['min']}–{t['max']} min" if t.get("min") else "",
                "cookWithin":    dish.get("cookWithin") or 0,
                "protein":       n.get("protein") or 0,
                "fibre":         n.get("fibre") or 0,
                "fat":           n.get("fat") or 0,
                "saturatedFat":  n.get("saturatedFat") or 0,
                "carbs":         n.get("carbs") or 0,
                "sugars":        n.get("sugars") or 0,
                "sodium":        n.get("salt") or 0,
                "kcal":          kcal,
                "infoFlags":     compute_info_flags(dish),
                "redFlags":      compute_red_flags(n, kcal),
            })
        except Exception as e:
            print(f"\n  Warning: could not fetch {slug}: {e}")

    print(f"\nLoaded {len(meals)} meals ({skipped} lunch meals skipped).\n")
    return meals


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UCook Nutrition Ranker</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f0;
    color: #222;
    display: flex;
    flex-direction: column;
    height: 100dvh;
    overflow: hidden;
  }

  /* ── Top bar ── */
  .top-bar { flex-shrink: 0; background: #f5f5f0; padding: 14px 24px 0; z-index: 10; }

  .top-row {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 8px; margin-bottom: 8px;
  }
  h1 { font-size: 1.15rem; font-weight: 700; }
  .meta { font-size: 0.75rem; color: #999; margin-top: 2px; }

  .controls { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
  .btn { padding: 8px 14px; border: none; border-radius: 8px; font-size: 0.84rem; font-weight: 600; cursor: pointer; white-space: nowrap; -webkit-tap-highlight-color: transparent; }
  .btn-dl { background: #1b5e20; color: white; }
  .btn-dl:active { background: #145a1c; }
  input[type=search] {
    padding: 8px 11px; border: 1px solid #ccc; border-radius: 8px;
    font-size: 0.84rem; width: 175px; background: white;
    -webkit-appearance: none;
  }

  /* ── Legend (collapsible on mobile) ── */
  .legend-wrap { border-bottom: 1px solid #e0e0da; margin-bottom: 0; }
  .legend-toggle {
    display: none;
    width: 100%; padding: 7px 0 8px; background: none; border: none;
    font-size: 0.78rem; color: #666; text-align: left; cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .legend-toggle::after { content: ' ▾'; font-size: 0.7em; }
  .legend-toggle.open::after { content: ' ▴'; }
  .legend {
    display: flex; flex-wrap: wrap; gap: 5px 14px;
    font-size: 0.76rem; color: #555; padding: 7px 0 9px;
  }
  .legend-item { display: flex; align-items: center; gap: 5px; }

  /* ── Badges & flags ── */
  .badge { display: inline-block; padding: 2px 7px; border-radius: 10px; font-size: 0.7rem; font-weight: 700; white-space: nowrap; }
  .badge-Gold     { background: #ffd700; color: #5a3e00; }
  .badge-Silver   { background: #c8c8c8; color: #333; }
  .badge-Bronze   { background: #cd7f32; color: white; }
  .badge-Unranked { background: #e0e0e0; color: #666; }
  .flag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.69rem; white-space: nowrap; }
  .flag-warn { background: #ffebee; border: 1px solid #ef9a9a; color: #c62828; }
  .flag-info { background: #fff3e0; border: 1px solid #ffcc80; color: #e65100; }

  /* ── Status bar ── */
  .status-bar { padding: 3px 24px 4px; font-size: 0.76rem; color: #999; flex-shrink: 0; min-height: 20px; }

  /* ── Table ── */
  .table-wrap { flex: 1; overflow: auto; -webkit-overflow-scrolling: touch; overflow-x: hidden; padding: 0 20px; }

  table { width: 100%; border-collapse: collapse; background: white; font-size: 0.82rem; }
  thead tr { background: #1a1a1a; color: white; }
  th {
    padding: 10px 16px; text-align: left; white-space: nowrap;
    user-select: none; cursor: pointer;
    position: sticky; top: 0; z-index: 3; background: #1a1a1a;
  }
  th.num { text-align: right; }
  th:active { background: #2e2e2e; }
  @media (hover: hover) { th:hover { background: #2e2e2e; } }
  th.sorted-asc::after  { content: " ▲"; font-size: 0.6em; opacity: 0.75; }
  th.sorted-desc::after { content: " ▼"; font-size: 0.6em; opacity: 0.75; }
  th.secondary-sort { opacity: 0.7; }
  th.secondary-sort::after { content: " ·"; font-size: 0.8em; opacity: 0.5; }

  /* Desktop: rank col has fixed width, no horizontal scroll needed */
  th.col-rank { min-width: 64px; }

  td { padding: 10px 16px; border-bottom: 1px solid #f0f0eb; vertical-align: top; }
  .val-green { color: #2e7d32; font-weight: 700; }
  .val-red   { color: #c62828; font-weight: 700; }
  tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: #fafaf7; }

  tr.row-Gold     td.col-rank { border-left: 3px solid #ffd700; }
  tr.row-Silver   td.col-rank { border-left: 3px solid #c8c8c8; }
  tr.row-Bronze   td.col-rank { border-left: 3px solid #cd7f32; }
  tr.row-Unranked td.col-rank { border-left: 3px solid #e0e0e0; }

  .meal-name { font-weight: 600; line-height: 1.3; }
  .meal-sub  { font-size: 0.74rem; color: #888; margin-top: 2px; }
  .flags     { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 3px; }
  .meal-link { font-size: 0.72rem; margin-top: 3px; }
  .meal-link a { color: #2e7d32; text-decoration: none; }
  .meal-link a:active { text-decoration: underline; }
  @media (hover: hover) { .meal-link a:hover { text-decoration: underline; } }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .legend-table { border-collapse: collapse; font-size: 0.75rem; margin-top: 6px; width: 100%; max-width: 420px; }
  .legend-table th, .legend-table td { padding: 3px 10px; border: 1px solid #e0e0da; text-align: center; }
  .legend-table th { background: #f0f0eb; font-weight: 600; }
  .legend-table td:first-child { text-align: left; font-weight: 600; }

  /* ── Mobile overrides ── */
  @media (max-width: 600px) {
    .top-bar { padding: 10px 14px 0; }
    h1 { font-size: 1rem; }
    input[type=search] { width: 140px; font-size: 0.82rem; }
    .btn { padding: 8px 11px; font-size: 0.8rem; }
    .legend-toggle { display: block; }
    .legend { display: none; padding: 0 0 8px; }
    .legend.open { display: flex; }
    .status-bar { padding: 2px 14px 4px; font-size: 0.72rem; }
    td { padding: 8px 12px; }
    th { padding: 9px 12px; font-size: 0.78rem; }
    table { font-size: 0.78rem; }
    /* Re-enable horizontal scroll on mobile with sticky left cols */
    .table-wrap { overflow-x: auto; padding: 0; }
    th.col-rank, td.col-rank { position: sticky; left: 0; z-index: 2; background: inherit; }
    th.col-rank { z-index: 4; min-width: 52px; }
    th.col-meal, td.col-meal { position: sticky; left: 60px; z-index: 2; background: inherit; min-width: 140px; max-width: 160px; }
    th.col-meal { z-index: 4; }
    .table-wrap.scrolled th.col-meal,
    .table-wrap.scrolled td.col-meal { box-shadow: 3px 0 6px -2px rgba(0,0,0,0.12); }
  }
</style>
</head>
<body>

<div class="top-bar">
  <div class="top-row">
    <div>
      <h1>UCook Nutrition Ranker</h1>
      <div class="meta">Per serving · dinner meals · Updated __DATE__</div>
    </div>
    <div class="controls">
      <input type="search" id="search" placeholder="Search…" oninput="renderTable()">
      <button class="btn btn-dl" onclick="downloadCSV()">⬇ CSV</button>
    </div>
  </div>
  <div class="legend-wrap">
    <button class="legend-toggle" id="legendToggle" onclick="toggleLegend()">Ranking guide</button>
    <div class="legend" id="legend">
      <div class="legend-item"><span class="badge badge-Gold">Gold</span> all four: Protein ≥50g, Fibre ≥10g, Sat Fat ≤10g, Sodium ≤1200mg</div>
      <div class="legend-item"><span class="badge badge-Silver">Silver</span> 3 of 4: Protein ≥50g, Fibre ≥8g, Sat Fat ≤13g, Sodium ≤1500mg</div>
      <div class="legend-item"><span class="badge badge-Bronze">Bronze</span> 2 of 4: Protein ≥40g, Fibre ≥6g, Sat Fat ≤15g, Sodium ≤1800mg</div>
      <div class="legend-item"><span class="flag flag-warn">⚑</span> red flag · <span class="flag flag-info">⚑</span> info flag (Beetroot · Fried · Mushrooms · causes Unranked)</div>
      <table class="legend-table">
        <thead><tr><th>Nutrient</th><th class="val-green">Green</th><th>Black</th><th class="val-red">Red</th></tr></thead>
        <tbody>
          <tr><td>Protein</td><td class="val-green">≥ 50g</td><td>35–49g</td><td class="val-red">&lt; 35g</td></tr>
          <tr><td>Fibre</td><td class="val-green">≥ 10g</td><td>5–9g</td><td class="val-red">&lt; 5g</td></tr>
          <tr><td>Sat Fat</td><td class="val-green">≤ 8g</td><td>9–15g</td><td class="val-red">&gt; 15g</td></tr>
          <tr><td>Sodium</td><td class="val-green">≤ 800mg</td><td>801–1500mg</td><td class="val-red">&gt; 1500mg</td></tr>
          <tr><td>Kcal</td><td class="val-green">500–800</td><td>801–1100</td><td class="val-red">&gt; 1100</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="status-bar" id="statusBar"></div>

<div class="table-wrap" id="tableWrap">
  <table>
    <thead>
      <tr>
        <th class="col-rank" onclick="sortBy('rankOrder')" data-col="rankOrder" class="sorted-asc">Rank</th>
        <th class="col-meal" onclick="sortBy('name')" data-col="name">Meal</th>
        <th onclick="sortBy('category')" data-col="category">Category</th>
        <th onclick="sortBy('proteinSource')" data-col="proteinSource">Protein source</th>
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
const STRING_COLS = new Set(['name','category','proteinSource']);
const DEFAULT_DIR = col => (STRING_COLS.has(col)||col==='rankOrder'||col==='cookWithin') ? 1 : -1;
let sortStack = [{ col: 'rankOrder', dir: 1 }];

function toggleLegend() {
  const el = document.getElementById('legend');
  const btn = document.getElementById('legendToggle');
  el.classList.toggle('open');
  btn.classList.toggle('open');
}

// Shadow on sticky columns when scrolled
document.getElementById('tableWrap').addEventListener('scroll', function() {
  this.classList.toggle('scrolled', this.scrollLeft > 4);
});

function sortBy(col) {
  if (sortStack[0].col===col) { sortStack[0].dir*=-1; }
  else { sortStack=[{col,dir:DEFAULT_DIR(col)},sortStack[0]].slice(0,2); }
  renderTable();
}

function cProtein(v)  { v=Number(v); return v>=50?' val-green':v<35?' val-red':''; }
function cFibre(v)    { v=Number(v); return v>=10?' val-green':v<5?' val-red':''; }
function cSatFat(v)   { v=Number(v); return v<=8?' val-green':v>15?' val-red':''; }
function cSodium(v)   { v=Number(v); return v<=800?' val-green':v>1500?' val-red':''; }
function cKcal(v)     { v=Number(v); return (v>=500&&v<=800)?' val-green':v>1100?' val-red':''; }

function renderTable() {
  const q=document.getElementById('search').value.toLowerCase();
  let rows=RAW.filter(m=>!q||m.name.toLowerCase().includes(q)||(m.subTitle||'').toLowerCase().includes(q)||(m.category||'').toLowerCase().includes(q)||(m.proteinSource||'').toLowerCase().includes(q));
  rows.sort((a,b)=>{
    for(const {col,dir} of sortStack){
      let av=a[col]??(typeof b[col]==='string'?'':0);
      let bv=b[col]??(typeof a[col]==='string'?'':0);
      const r=typeof av==='string'?av.localeCompare(bv):av-bv;
      if(r!==0) return dir*r;
    }
    // tiebreakers: fewer flags first, then more protein
    const fc=(a.redFlags.length+a.infoFlags.length)-(b.redFlags.length+b.infoFlags.length);
    if(fc!==0) return fc;
    return b.protein-a.protein;
  });
  document.querySelectorAll('th').forEach(th=>{
    th.classList.remove('sorted-asc','sorted-desc','secondary-sort');
    const idx=sortStack.findIndex(s=>s.col===th.dataset.col);
    if(idx===0) th.classList.add(sortStack[0].dir===1?'sorted-asc':'sorted-desc');
    else if(idx===1) th.classList.add('secondary-sort');
  });
  document.getElementById('tbody').innerHTML=rows.map(m=>`
    <tr class="row-${m.rank}">
      <td class="col-rank"><span class="badge badge-${m.rank}">${m.rank==='Unranked'?'NR':m.rank}</span></td>
      <td class="col-meal">
        <div class="meal-name">${esc(m.name)}</div>
        ${m.subTitle?`<div class="meal-sub">${esc(m.subTitle)}</div>`:''}
        ${(m.redFlags.length||m.infoFlags.length)?`<div class="flags">${m.redFlags.map(f=>`<span class="flag flag-warn">⚑ ${esc(f)}</span>`).join('')}${m.infoFlags.map(f=>`<span class="flag flag-info">⚑ ${esc(f)}</span>`).join('')}</div>`:''}
        <div class="meal-link"><a href="${m.url}" target="_blank">View on UCook ↗</a></div>
      </td>
      <td style="white-space:nowrap;font-size:0.77rem;color:#555">${esc(m.category)}</td>
      <td style="white-space:nowrap;font-size:0.77rem">${esc(m.proteinSource)}</td>
      <td class="num" style="font-size:0.78rem">${m.cookWithin?m.cookWithin+'d':'—'}</td>
      <td class="num${cProtein(m.protein)}">${fmt(m.protein)}</td>
      <td class="num${cFibre(m.fibre)}">${fmt(m.fibre)}</td>
      <td class="num${cSatFat(m.saturatedFat)}">${fmt(m.saturatedFat)}</td>
      <td class="num${cSodium(m.sodium)}">${Math.round(m.sodium)}</td>
      <td class="num${cKcal(m.kcal)}">${Math.round(m.kcal)}</td>
    </tr>`).join('');
  const counts={};
  rows.forEach(m=>counts[m.rank]=(counts[m.rank]||0)+1);
  const summary=['Gold','Silver','Bronze','Unranked'].filter(r=>counts[r]).map(r=>`${counts[r]} ${r}`).join(' · ');
  document.getElementById('statusBar').textContent=`${rows.length} meals${q?' matching "'+q+'"':''} — ${summary}`;
}

function downloadCSV() {
  const cols=['rank','category','proteinSource','name','subTitle','url','cookWithin','protein','fibre','saturatedFat','sodium','kcal'];
  const headers=['Rank','Category','Protein Source','Name','Sub-title','URL','Eat Within (days)','Protein (g)','Fibre (g)','Sat Fat (g)','Sodium (mg)','Kcal'];
  const lines=[headers.join(',')];
  [...RAW].sort((a,b)=>a.rankOrder-b.rankOrder||b.protein-a.protein).forEach(m=>{
    lines.push(cols.map(c=>{const v=String(m[c]??'');return(v.includes(',')||v.includes('"')||v.includes('\n'))?`"${v.replace(/"/g,'""')}"`:`${v}`;}).join(','));
  });
  const blob=new Blob([lines.join('\n')],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ucook_nutrition.csv';a.click();
}

function fmt(v){return(v!=null&&v!=='')?Number(v).toFixed(1):'—';}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
renderTable();
</script>
</body>
</html>"""


def build_html(meals):
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(meals, ensure_ascii=False))
    html = html.replace("__DATE__", date.today().strftime("%-d %B %Y"))
    return html


def main():
    no_open = "--no-open" in sys.argv

    meals = fetch_all_meals()

    tally = Counter(m["rank"] for m in meals)
    print("Summary:")
    for rank in ["Gold", "Silver", "Bronze", "Unranked"]:
        if tally[rank]:
            print(f"  {rank:10s}: {tally[rank]}")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_html(meals))
    print(f"\nSaved: {out_path}")

    if not no_open:
        webbrowser.open(f"file://{out_path}")


if __name__ == "__main__":
    main()
