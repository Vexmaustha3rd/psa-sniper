# CLAUDE.md: PSA Sniper

Project context for Claude Code. Read `HANDOFF.md` for current status and next steps.

## What this is
A read-only deal scanner. It searches eBay for **PSA 10 and PSA 1** graded trading cards priced **≥30% below comps** and posts alerts to a **Discord webhook**. It runs on **GitHub Actions every 15 minutes** (the owner wants it working while their PC is off).

## Owner requirements (do not change without asking)
- **Grades:** PSA 10 and PSA 1 only. Exclude other graders (BGS, CGC, SGC, TAG), raw cards, lots, "PSA ready", and reprints.
- **Categories:** Pokémon, sports (all major sports), and other TCGs (MTG, Yu-Gi-Oh!, One Piece, Lorcana).
- **Deal threshold:** all-in price (price + shipping + any buyer's premium) ≤ 70% of comp.
- **Tiers:**
  - 💰 **STEAL**: all-in under $100, any card.
  - 🏆 **GRAIL**: $100–$500, only cards that meet the grail rules.
  - 🏆 **PG** ("potential grail"): a STEAL that also meets the grail rules. The owner chose this tag over lowering the grail minimum.
- **Auctions ending soon are top priority.** An auction ending even ~10 minutes after a check must still alert. The current design gives an initial alert inside a 6-hour window, then one ⏰ FINAL CALL re-alert in the last 30 minutes, with `@here`.
- **Every auction alert shows:** end time in Pacific, current bid + shipping = all-in, bid count, and a **deal ceiling** (max bid that stays 30% under comps).
- **Alerts go to Discord** (the owner's choice over ntfy, Telegram, and email).
- **Strictly read-only.** Never bid, buy, make offers, sign in, or message sellers.
- **Facts and comps only.** No "buy this" language and no value predictions. The owner is aiming at long-term "grail" collecting, so keep the risk caveats.

## Layout
```
sniper.py                     # everything: eBay client, filters, grail rules, comps, scan, Discord
config.json                   # all tunables, searches, grail rules, watchlist
test_sniper.py                # offline unit tests (fake eBay, no network)
.github/workflows/sniper.yml  # 15-min cron + workflow_dispatch; state persisted via actions/cache
sniper.yml                    # duplicate of the workflow at root, only so the owner can paste it in via the GitHub web UI
README.md                     # owner-facing setup guide (non-technical; keep it that way)
state/state.json              # runtime only, gitignored: alerted item keys + 12h comp cache
```

## Key code paths (sniper.py)
- `Ebay.search()`: Browse API `item_summary/search`. Uses `buyingOptions:{AUCTION}` + `sort=endingSoonest` + an `itemEndDate` cap for auctions, and `FIXED_PRICE|BEST_OFFER` + `newlyListed` for BIN. OAuth client-credentials grant, scope `https://api.ebay.com/oauth/api_scope`.
- `parse_grade()` / `is_excluded()`: title filters. A title must mention exactly one PSA grade, and it must be 10 or 1.
- `grail_check(title, rules, sports=)`: keyword/serial/year heuristics. On TCG cards, `x/102` is a set number, not a serial, hence the `sports` flag.
- `Comps.get()`: source priority is watchlist `comp_value` → PriceCharting (`manual-only-price` = PSA 10, in pennies, paid token, 1 req/sec) → eBay active-ask fallback (median of the lower half of matching BIN asks, needs ≥4 matches, matching card number). Results are cached 12h in state.
- `scan()`: collects candidates, orders them soonest-ending auction first so they get comp lookups before `max_comp_lookups_per_run` runs out, then applies tier/threshold/dedupe. Dedupe keys are `item|found` and `item|final`.
- `post_discord()`: embeds, 10 per message, retries on 429.

## Commands
```
pip install requests
python -m unittest test_sniper -v                         # must stay green
DRY_RUN=1 EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... python sniper.py   # live eBay, prints Discord payload instead of posting
```
Env vars: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `DISCORD_WEBHOOK_URL`, optional `PRICECHARTING_TOKEN`, `DRY_RUN=1`, `SNIPER_CONFIG`, `SNIPER_STATE`.

## Constraints to respect
- **eBay Browse API quota: 5,000 calls/day.** Each run costs about 2 calls per search + 2 per watchlist entry + 1 per comp lookup. At 96 runs/day, keep a run under about 50 calls. Do the math before adding searches.
- **eBay sold data is not available.** Marketplace Insights is a restricted API, and the Finding API's `findCompletedItems` was decommissioned. Don't build on either without confirming access.
- **GitHub Actions:** public repos get unlimited minutes. Private repos get 2,000 min/month free, so use a ≥30-min cron there. Cron can start late. Scheduled workflows on public repos are paused after 60 days with no repo activity.
- Python 3.12 in CI. `requests` is the only dependency, so keep it lean.
- Add a test with every behavior change to filters, grail rules, comps, or the alert format.
