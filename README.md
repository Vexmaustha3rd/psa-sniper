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
   *Public is recommended: GitHub Actions minutes are unlimited for public repos. Your keys stay private in Secrets (step 4). If you prefer Private, set the timer in step 6 to every 30 minutes to stay inside the free 2,000 minutes/month.*
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
**Actions** tab → enable workflows if asked → **PSA Sniper → Run workflow**. Open the run; the last log line should say `scan done: N deal(s), ... eBay calls`. After that, step 6 keeps it running every 15 minutes.

### 6. Set the 15-minute timer (cron-job.org)
GitHub's built-in schedule can run hours late, so a free outside timer starts each scan instead. GitHub's schedule stays on as a backup.

**Make a GitHub token.** It only needs permission to start this one workflow.
1. GitHub → your picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Name it `psa-sniper-timer`. Set the longest expiration offered and put a reminder in your calendar.
3. **Repository access → Only select repositories →** `psa-sniper`.
4. **Permissions → Repository permissions → Actions → Read and write**. Leave everything else alone.
5. Generate it and copy the token. GitHub only shows it once.

**Make the timer.**
1. Sign up at **cron-job.org** and choose **Create cronjob**.
2. Title: `PSA Sniper`. URL:
   `https://api.github.com/repos/Vexmaustha3rd/psa-sniper/actions/workflows/sniper.yml/dispatches`
3. Schedule: every 15 minutes.
4. Under **Advanced**, set **Request method** to `POST`, and add these headers (replace `YOUR-TOKEN`, keep the word `Bearer`):

| Key | Value |
|---|---|
| `Authorization` | `Bearer YOUR-TOKEN` |
| `Accept` | `application/vnd.github+json` |
| `Content-Type` | `application/json` |

5. Set **Request body** to `{"ref":"main"}` and save.

**Check it.** On GitHub's **Actions** tab, a new run labelled `workflow_dispatch` should appear about every 15 minutes. Runs labelled `schedule` are the backup, and they stop early when the timer is working.

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
- Schedule: change the timer on cron-job.org. Don't go faster than every 15 minutes without checking the eBay limit below.

## Limits to know
- **eBay only for now.** Goldin, Fanatics Collect and Heritage don't offer public APIs; adding them is a later phase.
- **eBay API limit** is 5,000 calls/day. The default setup uses about 30–40 per run (about 3,500/day at every 15 min). If you add lots of searches or watchlist cards, slow the schedule down.
- **GitHub's own schedule can run hours late or skip runs**, which is why the timer in step 6 does the real work. Cards ending within the next 6 hours are caught early, and the FINAL CALL covers the last 30 minutes.
- **If the timer's token expires**, scans drop back to GitHub's slower backup schedule. Make a new token and paste it into cron-job.org.
- **GitHub pauses its backup schedule on public repos after 60 days with no commits.** The timer isn't affected. Click "Enable workflow" if you want the backup back.
- Prices often jump in an auction's last minute. The current bid isn't the final price, so use the deal ceiling.
- This is a price-comparison tool, not investment advice. Collectible values can go down.

## Testing locally (optional)
```
pip install requests
python -m unittest test_sniper -v
```
