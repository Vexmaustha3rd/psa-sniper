# PSA Sniper

Scans eBay every 15 minutes for **PSA 10 and PSA 1** cards (Pokémon, sports, Magic, Yu-Gi-Oh!, One Piece, Lorcana) priced **30%+ below comps**, and posts alerts to a Discord channel. It runs free on GitHub's servers, so your PC can be off.

It only looks. It never bids, buys, or messages sellers.

## What you get

| Alert | Meaning |
|---|---|
| ⏰ **FINAL CALL** | Auction ends within 30 min and is still a deal. Includes `@here` so your phone pings. |
| 🏆 **GRAIL** | $100–$500 card with long-term collector pull (1st Edition, WOTC-era, low serial, vintage, chase Pokémon alt art, etc.) |
| 🏆 **PG** | "Potential grail": under $100 *and* meets the grail rules |
| 💰 **STEAL** | Any PSA 10 / PSA 1 under $100, 30%+ below comps |

Each alert shows end time (Pacific), current bid + shipping = all-in price, bid count, **deal ceiling** (the most you could bid and still be 30% under comps), comps, seller feedback, red flags, and links.

Auctions are alerted when they're within 6 hours of ending, then **again** in their final 30 minutes if they're still a deal. You never get the same alert twice.

---

## Setup (about 20 minutes, one time)

### 1. Discord webhook (where alerts go)
1. In Discord, create a server (or use yours) and a channel like `#psa-sniper`.
2. Channel ⚙️ **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**.
3. On your phone's Discord app: long-press the channel → **Notification Settings → All Messages**.

### 2. eBay developer keys (free)
1. Go to **developer.ebay.com** and sign up (approval can take up to a business day).
2. Open **Application Keys** and create a **Production** keyset.
3. eBay will ask about *Marketplace Account Deletion* notifications. This app doesn't store eBay user data, so choose the exemption option.
4. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)**.

### 3. GitHub (free account)
1. Sign up at **github.com**, then **New repository** → name it `psa-sniper` → **Public** → Create.
   *Public is recommended: GitHub Actions minutes are unlimited for public repos. Your keys stay private in Secrets (step 4). If you prefer Private, change the schedule to every 30 minutes (see Tuning) to stay inside the free 2,000 minutes/month.*
2. Click **uploading an existing file** and drag in `sniper.py`, `config.json`, `requirements.txt`, `test_sniper.py`, `README.md`, `.gitignore`. Commit.
3. The workflow lives in a hidden folder, so create it by hand: **Add file → Create new file**, name it exactly
   `.github/workflows/sniper.yml`
   and paste in the contents of the `sniper.yml` file from this zip. Commit.

### 4. Add your keys as Secrets
Repo **Settings → Secrets and variables → Actions → New repository secret**, add:

| Name | Value |
|---|---|
| `EBAY_CLIENT_ID` | eBay App ID |
| `EBAY_CLIENT_SECRET` | eBay Cert ID |
| `DISCORD_WEBHOOK_URL` | Discord webhook URL |
| `PRICECHARTING_TOKEN` | *(optional, paid)* PriceCharting API token, see Comps below |

### 5. Turn it on
**Actions** tab → enable workflows if asked → **PSA Sniper → Run workflow**. Open the run; the last log line should say `scan done: N deal(s), ... eBay calls`. After that it runs on its own every 15 minutes.

---

## How comps work (read this)

eBay's API doesn't give regular developers **sold** prices. So the sniper uses the best source available, in this order:

1. **Your watchlist value.** In `config.json`, set `comp_value` for a card you're hunting. This is the most accurate, because you set it from real sold data.
2. **PriceCharting** *(optional, paid subscription)*. Real PSA 10 sold averages. It doesn't cover PSA 1.
3. **eBay active listings** *(default)*. The median of the cheaper half of matching active Buy It Now listings for the same card number and grade. It's conservative, but asking prices aren't sold prices. These alerts say *"comps are active asks, check sold"*, and each one includes a **Check sold comps** link.

Always check the sold link and the PSA cert number before you bid.

## Hunting specific grails: the watchlist
Edit `config.json` on GitHub (pencil icon) and replace the EXAMPLE entry:

```json
"watchlist": [
  {
    "name": "151 Charizard SIR",
    "q": "2023 Pokemon 151 Charizard ex 199 PSA 10",
    "grade": "10",
    "comp_value": 0,
    "max_all_in": 500,
    "grail": true
  }
]
```
`comp_value` is the sold median you've confirmed (0 means auto). `max_all_in` is your cap for that card. Add as many as you like; each one costs about 2 eBay API calls per run.

## Tuning (all in `config.json`)
- `discount_threshold`: 0.30 = 30% below comps
- `steal_max_all_in` / `grail_max_all_in`: price caps ($100 / $500)
- `auction_window_hours`: how far ahead to look at auctions (6)
- `final_call_minutes`: when the FINAL CALL re-alert fires (30)
- `min_seller_feedback_score` / `min_seller_feedback_pct`: sellers below these are skipped
- `searches`: the eBay searches it runs. Add your own.
- `grail_rules`: the keywords and rules that earn the GRAIL / PG tag
- Schedule: edit the `cron` line in `.github/workflows/sniper.yml` (`*/30 * * * *` = every 30 min)

## Limits to know
- **eBay only for now.** Goldin, Fanatics Collect and Heritage don't offer public APIs; adding them is a later phase.
- **eBay API limit** is 5,000 calls/day. The default setup uses about 30–40 per run (about 3,500/day at every 15 min). If you add lots of searches or watchlist cards, slow the schedule down.
- **GitHub schedules can start a few minutes late** at busy times. Cards ending within the next 6 hours are caught early, and the FINAL CALL covers the last 30 minutes.
- **GitHub pauses schedules on public repos after 60 days with no commits.** Edit any file (like `config.json`) at least every couple of months, or click "Enable workflow" if it pauses.
- Prices often jump in an auction's last minute. The current bid isn't the final price, so use the deal ceiling.
- This is a price-comparison tool, not investment advice. Collectible values can go down.

## Testing locally (optional)
```
pip install requests
python -m unittest test_sniper -v
```
