# HANDOFF: PSA Sniper (2026-10-29)

## Status
| Item | State |
|---|---|
| Scanner (`sniper.py`) | ✅ Written; 13 offline tests pass |
| GitHub Actions workflow | ✅ Written, **not yet deployed** |
| Live eBay run | ❌ **Never run against the real API.** The build environment couldn't reach eBay. Treat first contact as untested. |
| Discord alerts | ✅ Payload format verified offline; never posted to a real webhook |
| Owner setup | ⏳ Owner has the zip + README; eBay developer approval may still be pending |
| Old Claude scheduled scan | ⏸️ Paused (it couldn't reach eBay from its cloud sandbox) |

## How we got here
1. We started with a Claude scheduled task that scanned eBay via web fetch every 4 hours. Its sandbox blocked eBay, Goldin, Fanatics Collect, Heritage, 130point and PriceCharting, so runs finished in about 2 minutes with no data. That task is paused.
2. The owner wants it to run while away from their PC, so the target is **GitHub Actions + eBay Browse API + Discord**.
3. Requirements evolved through these steps: auctions ending soon became the priority (still alert at ~10 min left) → multi-marketplace scope → a $100–$500 grail tier → a "PG" tag for grail-quality cards under $100.

## First thing to do: verify against live eBay
With the owner's keys in env:
```
DRY_RUN=1 python sniper.py
```
Check and fix:
- [ ] OAuth token succeeds (production keyset, not sandbox).
- [ ] `filter` syntax is accepted: `buyingOptions:{AUCTION}`, `price:[..500]`, `priceCurrency:USD`, `itemEndDate:[..<ISO>]`, `deliveryCountry:US`. A 400 response usually means filter syntax.
- [ ] Response field names match what `to_listing()` reads: `currentBidPrice`, `bidCount`, `itemEndDate`, `shippingOptions[].shippingCost`, `seller.feedbackScore`, `seller.feedbackPercentage`, `image.imageUrl`. Some may be missing from search summaries, and shipping can need the `X-EBAY-C-ENDUSERCTX` header (already sent).
- [ ] Category IDs in `config.json` (`183454` CCG singles, `261328` sports trading card singles) return results. If not, look them up through the Taxonomy API or drop `category_ids`.
- [ ] Count eBay calls per run (printed at the end) and keep them within the 5,000/day budget at 96 runs/day.
- [ ] Look at the false positive and false negative rate of `parse_grade` / `is_excluded` on real titles. Expect to tune `EXCLUDE_PATTERNS` (e.g. `\bpick\b` and `\bbreak\b` may drop legit titles).

Then do a real Discord post and a `workflow_dispatch` run on GitHub.

## Known weaknesses (ranked)
1. **Comps are the weak link.** Without PriceCharting, comps come from *active asks*, not sales. That's conservative in one way but noisy, and PSA 1 is never covered by PriceCharting. Candidate fixes:
   - PriceCharting paid token (already wired, PSA 10 only).
   - Owner-maintained `comp_value` on watchlist entries (already wired).
   - Investigate legitimate sold-data sources: eBay Marketplace Insights access application, Card Ladder, 130point. **Check each one's terms first. Don't scrape sites that disallow it.**
2. **Grail rules are keyword heuristics** (`grail_check`). Expect misses (e.g. rookies without "RC" in the title) and false hits (e.g. "holo" + "pikachu" on any modern card). There's no PSA population data yet.
3. **Title matching for comps** uses Jaccard similarity on tokens (≥0.45) plus a card-number match. It can mix parallels (e.g. base vs. silver Prizm). Consider requiring parallel keywords to match.
4. **Shipping unknown → assumed $5** and flagged.
5. **GitHub cron latency** can be several minutes. If the owner wants tighter sniping, move to an always-on host (small VPS / Fly.io / Railway) with a 2–5 min loop. Mind the API quota.

## Roadmap (owner asked for these, not built)
- **More marketplaces:** Fanatics Collect, Goldin, Heritage. None has a known public listings API, so research official feeds or partner programs first. Include buyer's premium in all-in. The earlier estimates were Goldin ~23%, Heritage ~25%, Fanatics ~15%. Verify current terms.
- **PSA population counts** in grail notes (lower pop = scarcer). PSA has a public API (cert lookup, token required). Check whether it exposes pop data and what its rate limits are.
- **Cert verification:** pull the cert # from the listing if exposed and validate it via the PSA API. Flag mismatches.
- **Owner-facing extras** (only if asked): daily digest, a simple web dashboard of past alerts, per-alert "mute this card".

## Don'ts
- No auto-bidding or sniping execution. Alerts only.
- Don't commit secrets. Keys live in GitHub Actions secrets.
- Don't loosen the 30% threshold or the PSA 10/1-only rule without the owner's say-so.
- Keep README.md non-technical. The owner is a collector, not a developer.
