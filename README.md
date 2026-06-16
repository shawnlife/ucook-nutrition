# UCook Nutrition Ranker

A tool that fetches this week's [UCook](https://www.ucook.co.za) meal kit menu, pulls the per-serving nutritional info for every dinner meal, ranks them, and opens a sortable table in your browser.

## What it does

- Fetches the current week's dinner meals (lunch meals excluded automatically)
- Pulls per-serving: protein, fibre, fat, saturated fat, carbs, sugars, sodium, and kcal
- Ranks each meal:
  - 🥇 **Gold** — Protein ≥50g, Fibre ≥10g, Sat Fat ≤10g, Sodium ≤1200mg (all 4)
  - 🥈 **Silver** — 3 of 4: protein ≥50g, fibre ≥8g, sat fat ≤13g, sodium ≤1500mg
  - 🥉 **Bronze** — 2 of 4: protein ≥40g, fibre ≥6g, sat fat ≤15g, sodium ≤1800mg
  - **Unranked** — fewer than 2 criteria met (or contains beetroot / primarily fried)
- Flags: 🍄 mushrooms, 🔥 over 1000 kcal, ⚑ red flag warnings (high sodium, high sat fat, high sugar, low protein, low fibre)
- Sortable by any column, with two-level sorting (primary + secondary tiebreaker)
- Includes spice level, cook time, eat-within days, and a direct link to each meal page
- Download CSV button for pasting into a nutrition chat

## Usage

Requires Python 3 (no third-party packages needed).

```bash
python3 ucook_nutrition.py
```

Runs in ~30 seconds, generates `ucook_nutrition.html`, and opens it in your browser automatically. Re-run each week when the new menu drops.
