# UCook Nutrition Ranker

A shareable browser tool that fetches this week's [UCook](https://www.ucook.co.za) meal kit menu and ranks every dinner meal by per-serving nutrition — protein, fibre, sat fat, sodium, and more.

**Live site:** https://shawnlife.github.io/ucook-nutrition

---

## How it works

- Fetches the current week's dinner meals (lunch excluded automatically)
- Ranks each meal:
  - 🥇 **Gold** — all four: Protein ≥50g, Fibre ≥10g, Sat Fat ≤10g, Sodium ≤1200mg
  - 🥈 **Silver** — 3 of 4: Protein ≥50g, Fibre ≥8g, Sat Fat ≤13g, Sodium ≤1500mg
  - 🥉 **Bronze** — 2 of 4: Protein ≥40g, Fibre ≥6g, Sat Fat ≤15g, Sodium ≤1800mg
  - **Unranked** — fewer than 2 criteria met, or contains beetroot / primarily fried
- Red flag warnings: Sodium >1800mg, Sat Fat >20g, Sugar >25g, Protein <35g, Fibre <5g, >1000 kcal
- Info flags: beetroot, fried, mushrooms
- Sortable columns with two-level sort (primary + secondary tiebreaker)
- Protein source detection, cook time, eat-within days
- Download CSV for pasting into a nutrition chat

---

## Setup (one time)

The frontend is a static GitHub Pages site. Because UCook's API blocks direct browser requests, a tiny Cloudflare Worker acts as a proxy.

### 1. Deploy the Cloudflare Worker

1. Go to [workers.cloudflare.com](https://workers.cloudflare.com) and create a free account
2. Click **"Create application"** → **"Create Worker"**
3. Replace all the default code with the contents of [`worker.js`](worker.js)
4. Click **"Deploy"**
5. Copy your Worker URL — it looks like `https://ucook-proxy.yourname.workers.dev`

### 2. Enable GitHub Pages

1. Go to your repo on GitHub → **Settings** → **Pages**
2. Under "Branch", select `main` and folder `/` (root)
3. Click **Save** — your site will be live at `https://shawnlife.github.io/ucook-nutrition` within a minute

### 3. Use the tool

1. Visit your GitHub Pages URL
2. Paste your Cloudflare Worker URL when prompted (saved to your browser — only needed once)
3. Click **"Load this week's meals"**

---

## Local fallback

`ucook_nutrition.py` still works if you want to run it from Terminal — no Cloudflare needed.

```bash
python3 ucook_nutrition.py
```
