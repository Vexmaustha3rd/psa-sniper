#!/usr/bin/env python3
"""
PSA Sniper - scans eBay for PSA 10 / PSA 1 cards priced well below comps
and posts alerts to Discord. Built to run on GitHub Actions every ~15 min.

Read-only: it never bids, buys, or contacts sellers.

Environment variables (set as GitHub Actions secrets):
  EBAY_CLIENT_ID        eBay developer App ID (production)
  EBAY_CLIENT_SECRET    eBay developer Cert ID (production)
  DISCORD_WEBHOOK_URL   Discord channel webhook URL
  PRICECHARTING_TOKEN   optional - paid PriceCharting API token for PSA 10 sold comps
  DRY_RUN               optional - "1" prints alerts instead of posting
"""
from __future__ import annotations

import base64
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("SNIPER_CONFIG", HERE / "config.json"))
STATE_PATH = Path(os.environ.get("SNIPER_STATE", HERE / "state" / "state.json"))

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
PRICECHARTING_URL = "https://www.pricecharting.com/api/product"

PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

# Titles containing any of these are skipped outright.
EXCLUDE_PATTERNS = [
    r"\blot\b", r"\blots\b", r"\bbundle\b", r"\bbgs\b", r"\bcgc\b", r"\bsgc\b",
    r"\btag\s*\d", r"\bbeckett\b", r"psa\s*ready", r"not\s+psa", r"\breprint\b",
    r"\bproxy\b", r"\bcustom\b", r"\borica\b", r"\bfan\s*art\b", r"\bpick\b",
    r"\byou\s+choose\b", r"\bchoose\b", r"\bmystery\b", r"\brepack\b",
    r"\bbreak\b", r"\bslab\s+only\b", r"\bempty\s+slab\b", r"\bcase\s+only\b",
    r"\bpre[-\s]?grade", r"\bwould\s+grade\b", r"\bpotential\b", r"\bdna\s+only\b",
    r"\bsealed\b", r"\bbooster\b", r"\bbox\b(?!\s*topper)", r"\bpack\b(?!\s*fresh)",
    r"\bauthentic\s+altered\b", r"\bpsa\s*/?\s*dna\b",
]

NOISE_WORDS = {
    "psa", "gem", "mint", "mt", "gem-mt", "gem-mint", "pristine", "graded", "grade",
    "card", "cards", "tcg", "ccg", "rare", "hot", "invest", "investment", "pop",
    "low", "wow", "look", "nice", "beautiful", "free", "shipping", "ship", "fast",
    "new", "the", "and", "of", "a", "-", "|", "/", "poor", "pr", "fair",
    "trading", "single", "slab", "slabbed", "cert", "certified", "case", "w",
}


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------
@dataclass
class Listing:
    item_id: str
    title: str
    url: str
    price: float
    shipping: Optional[float]
    is_auction: bool
    bids: int
    end: Optional[datetime]
    seller: str
    feedback_score: int
    feedback_pct: Optional[float]
    image: Optional[str]
    category: str = ""
    grade: Optional[str] = None
    grail: bool = False
    grail_reasons: list = field(default_factory=list)
    watch: Optional[dict] = None

    @property
    def all_in(self) -> float:
        return round(self.price + (self.shipping if self.shipping is not None else 5.0), 2)

    def minutes_left(self, now: datetime) -> Optional[float]:
        if not self.end:
            return None
        return (self.end - now).total_seconds() / 60.0


@dataclass
class Comp:
    value: float
    count: int
    source: str
    label: str
    thin: bool = False
    url: Optional[str] = None


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def money(v) -> Optional[float]:
    try:
        return float(v["value"]) if isinstance(v, dict) else float(v)
    except (TypeError, ValueError, KeyError):
        return None


def parse_grade(title: str) -> Optional[str]:
    """Return '10' or '1' if the title is clearly a single PSA 10 / PSA 1, else None."""
    t = title.lower()
    grades = re.findall(r"\bpsa\s*[-:#]?\s*(\d+(?:\.\d)?)(?![\d.])", t)
    if not grades:
        return None
    unique = set(grades)
    if len(unique) != 1:
        return None  # multiple grades mentioned -> likely lot/comparison
    g = unique.pop()
    return g if g in ("10", "1") else None


def is_excluded(title: str) -> bool:
    t = title.lower()
    return any(re.search(p, t) for p in EXCLUDE_PATTERNS)


def tokens(title: str) -> set:
    t = title.lower()
    t = re.sub(r"[^\w#/\-\s]", " ", t)
    out = set()
    for w in t.split():
        w = w.strip("-")
        if not w or w in NOISE_WORDS:
            continue
        if re.fullmatch(r"\d{1,2}", w) and w in ("10", "1", "9", "8"):
            continue
        out.add(w)
    return out


def card_numbers(title: str, sports: bool = False) -> set:
    """Card numbers like '#4' or TCG '4/102' -> {'4'}; used to avoid mismatched comps.
    On sports cards 'x/25' is a serial (differs per copy), so only '#' numbers count."""
    t = title.lower()
    nums = set(re.findall(r"#\s*([a-z]*\d+[a-z]?)(?!\s*/)", t))
    if not sports:
        nums |= {m[0] for m in re.findall(r"\b(\d{1,3})\s*/\s*(\d{2,3})\b", t)}
    return {n.lstrip("0") or "0" for n in nums}


def clean_query(title: str, grade: str) -> str:
    words = [w for w in re.sub(r"[^\w#/\-\s]", " ", title).split()
             if w.lower() not in NOISE_WORDS and not re.fullmatch(r"(10|1|9)", w)]
    q = " ".join(words[:9])
    return f"{q} PSA {grade}"[:100]


def similarity(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def grail_check(title: str, rules: dict, sports: bool = False) -> tuple[bool, list]:
    t = title.lower()
    reasons = []
    for kw in rules.get("strong_keywords", []):
        if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", t):
            reasons.append(kw)
    max_serial = rules.get("max_serial", 99)
    if re.search(r"\b1\s*/\s*1\b", t) or "1 of 1" in t:
        reasons.append("1/1")
    else:
        # Standalone "/25" is a serial everywhere. "12/25" is a serial on sports cards,
        # but on TCG cards "4/102" or "58/64" is just the set number, so skip it there.
        serials = re.findall(r"(?<![\d/])/\s*(\d{1,3})\b", t)
        if sports:
            serials += [d for _, d in re.findall(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b", t)]
        for s in serials:
            if 1 < int(s) <= max_serial:
                reasons.append(f"/{int(s)} serial")
                break
    years = [int(y) for y in re.findall(r"\b(19[0-9]{2}|20[0-2][0-9])\b", t)]
    year = min(years) if years else None
    if year and year <= rules.get("vintage_year_max", 1980):
        reasons.append(f"vintage {year}")
    is_pokemon = "pokemon" in t or "pokémon" in t
    chase_mon = next((p for p in rules.get("chase_pokemon", [])
                      if re.search(r"\b" + re.escape(p) + r"\b", t)), None)
    chase_var = next((v for v in rules.get("chase_variants", [])
                      if re.search(r"\b" + re.escape(v) + r"\b", t)), None)
    lo, hi = rules.get("wotc_years", [1999, 2003])
    if is_pokemon and year and lo <= year <= hi and ("holo" in t or chase_mon):
        reasons.append(f"WOTC-era {year}")
    if chase_mon and chase_var:
        reasons.append(f"{chase_mon} {chase_var}")
    if re.search(r"\b(rc|rookie)\b", t) and any(r.endswith("serial") or r == "1/1" or r.startswith("vintage") for r in reasons):
        reasons.append("key rookie")
    reasons = list(dict.fromkeys(reasons))
    return (len(reasons) > 0, reasons)


def fmt_pt(dt: datetime) -> str:
    # "%-I" is glibc-only; strip the leading zero by hand so it also works on Windows.
    return dt.astimezone(PT).strftime("%I:%M %p PT").lstrip("0")


def fmt_left(minutes: float) -> str:
    m = max(0, int(minutes))
    h, mm = divmod(m, 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


# ----------------------------------------------------------------------------
# eBay
# ----------------------------------------------------------------------------
class Ebay:
    def __init__(self, client_id: str, client_secret: str, session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.s = session or requests.Session()
        self.token = None
        self.calls = 0

    def auth(self):
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = self.s.post(EBAY_TOKEN_URL, headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, data={"grant_type": "client_credentials", "scope": EBAY_SCOPE}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def search(self, q: str, category: Optional[str], auction: bool, max_price: float,
               end_before: Optional[datetime] = None, limit: int = 100) -> list[dict]:
        if not self.token:
            self.auth()
        filters = [
            "buyingOptions:{AUCTION}" if auction else "buyingOptions:{FIXED_PRICE|BEST_OFFER}",
            f"price:[..{int(max_price)}]",
            "priceCurrency:USD",
            "deliveryCountry:US",
        ]
        if end_before:
            filters.append("itemEndDate:[.." + end_before.strftime("%Y-%m-%dT%H:%M:%SZ") + "]")
        params = {
            "q": q[:100],
            "filter": ",".join(filters),
            "sort": "endingSoonest" if auction else "newlyListed",
            "limit": str(limit),
        }
        if category:
            params["category_ids"] = category
        for attempt in range(3):
            self.calls += 1
            r = self.s.get(EBAY_SEARCH_URL, params=params, headers={
                "Authorization": f"Bearer {self.token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS",
            }, timeout=30)
            if r.status_code == 401 and attempt == 0:
                self.auth()
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json().get("itemSummaries", []) or []
        return []


def to_listing(raw: dict, category: str = "") -> Optional[Listing]:
    opts = raw.get("buyingOptions", []) or []
    is_auction = "AUCTION" in opts
    price = money(raw.get("currentBidPrice")) if is_auction else None
    if price is None:
        price = money(raw.get("price"))
    if price is None:
        return None
    ship = None
    for so in raw.get("shippingOptions", []) or []:
        c = money(so.get("shippingCost"))
        if c is not None:
            ship = c if ship is None else min(ship, c)
    seller = raw.get("seller", {}) or {}
    try:
        fpct = float(seller.get("feedbackPercentage")) if seller.get("feedbackPercentage") is not None else None
    except ValueError:
        fpct = None
    return Listing(
        item_id=raw.get("itemId", ""),
        title=raw.get("title", ""),
        url=raw.get("itemWebUrl", ""),
        price=price,
        shipping=ship,
        is_auction=is_auction,
        bids=int(raw.get("bidCount", 0) or 0),
        end=parse_dt(raw.get("itemEndDate")),
        seller=seller.get("username", "?"),
        feedback_score=int(seller.get("feedbackScore", 0) or 0),
        feedback_pct=fpct,
        image=(raw.get("image") or {}).get("imageUrl"),
        category=category,
    )


# ----------------------------------------------------------------------------
# Comps
# ----------------------------------------------------------------------------
class Comps:
    def __init__(self, ebay: Ebay, cfg: dict, cache: dict, pc_token: Optional[str], session=None):
        self.ebay = ebay
        self.cfg = cfg
        self.cache = cache  # persisted: key -> {"t": ts, "comp": {...}}
        self.pc_token = pc_token
        self.s = session or requests.Session()
        self.lookups = 0

    def _store(self, key: str, comp: Optional[Comp]):
        self.cache[key] = {"t": time.time(), "comp": comp.__dict__ if comp else None}

    def get(self, lst: Listing) -> Optional[Comp]:
        if lst.watch and lst.watch.get("comp_value"):
            return Comp(float(lst.watch["comp_value"]), 0, "watchlist",
                        "your watchlist comp value")
        q = clean_query(lst.title, lst.grade)
        key = f"{lst.grade}|{q.lower()}"
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < 12 * 3600:
            return Comp(**hit["comp"]) if hit["comp"] else None
        if self.lookups >= self.cfg.get("max_comp_lookups_per_run", 20):
            return None
        self.lookups += 1
        comp = None
        if self.pc_token and lst.grade == "10":
            comp = self._pricecharting(q)
        if comp is None:
            comp = self._active_asks(lst, q)
        self._store(key, comp)
        return comp

    def _pricecharting(self, q: str) -> Optional[Comp]:
        try:
            pq = re.sub(r"\bPSA\s*10\b", "", q, flags=re.I).strip()
            r = self.s.get(PRICECHARTING_URL, params={"t": self.pc_token, "q": pq}, timeout=30)
            time.sleep(1.1)  # PriceCharting allows ~1 call/sec
            if r.status_code != 200:
                return None
            d = r.json()
            if d.get("status") != "success" or not d.get("manual-only-price"):
                return None
            val = d["manual-only-price"] / 100.0
            name = f'{d.get("console-name", "")} {d.get("product-name", "")}'.strip()
            return Comp(val, 0, "PriceCharting",
                        f"PriceCharting PSA 10 sold avg (matched: {name})",
                        url=f'https://www.pricecharting.com/game/{d.get("id", "")}')
        except (requests.RequestException, ValueError, KeyError):
            return None

    def _active_asks(self, lst: Listing, q: str) -> Optional[Comp]:
        try:
            raws = self.ebay.search(q, lst.category or None, auction=False,
                                    max_price=max(lst.all_in * 20, 200), limit=50)
        except requests.RequestException:
            return None
        me = tokens(lst.title)
        sports = lst.category == self.cfg["categories"].get("sports")
        my_nums = card_numbers(lst.title, sports)
        prices = []
        for raw in raws:
            o = to_listing(raw, lst.category)
            if not o or o.item_id == lst.item_id or is_excluded(o.title):
                continue
            if parse_grade(o.title) != lst.grade:
                continue
            if my_nums and not (my_nums & card_numbers(o.title, sports)):
                continue
            if similarity(me, tokens(o.title)) < 0.45:
                continue
            prices.append(o.all_in)
        if len(prices) < self.cfg.get("min_active_comps", 4):
            return None
        prices.sort()
        # use lower-half median of asks to stay conservative (asks run above sold prices)
        lower = prices[: max(3, len(prices) // 2 + 1)]
        val = statistics.median(lower)
        return Comp(round(val, 2), len(prices), "eBay active",
                    f"median of lower-half asks, {len(prices)} matching active PSA {lst.grade} listings (no sold data)",
                    thin=len(prices) < 6,
                    url="https://www.ebay.com/sch/i.html?" + requests.compat.urlencode(
                        {"_nkw": q, "LH_Sold": "1", "LH_Complete": "1"}))


# ----------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------
def classify(lst: Listing, cfg: dict) -> Optional[str]:
    """Return 'steal', 'grail', or None before comps are checked."""
    if lst.watch:
        return "grail" if lst.watch.get("grail") else ("steal" if lst.all_in < cfg["steal_max_all_in"] else "watch")
    if lst.all_in < cfg["steal_max_all_in"]:
        return "steal"
    if lst.grail and lst.all_in <= cfg["grail_max_all_in"]:
        return "grail"
    return None


def red_flags(lst: Listing, comp: Comp, cfg: dict) -> list:
    flags = []
    if lst.feedback_score < 100:
        flags.append(f"newer seller ({lst.feedback_score} feedback)")
    if lst.feedback_pct is not None and lst.feedback_pct < 99:
        flags.append(f"{lst.feedback_pct}% positive")
    if lst.shipping is None:
        flags.append("shipping not shown (estimated $5)")
    if comp.source == "eBay active":
        flags.append("comps are active asks, check sold")
    if comp.thin:
        flags.append("thin comps")
    if lst.is_auction and lst.bids == 0:
        flags.append("no bids yet, price likely to rise")
    return flags


def scan(cfg: dict, ebay: Ebay, comps: Comps, state: dict, now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(UTC)
    window_end = now + timedelta(hours=cfg["auction_window_hours"])
    max_price = cfg["grail_max_all_in"]
    rules = cfg.get("grail_rules", {})
    alerted = state.setdefault("alerted", {})
    seen: dict[str, Listing] = {}

    jobs = []
    for s in cfg["searches"]:
        cat = cfg["categories"].get(s.get("category", ""), None)
        jobs.append((s["q"], cat, None))
    for w in cfg.get("watchlist", []):
        if "EXAMPLE" in w.get("name", ""):
            continue
        jobs.append((w["q"], None, w))

    for q, cat, watch in jobs:
        cap = watch.get("max_all_in", max_price) if watch else max_price
        for auction in (True, False):
            try:
                raws = ebay.search(q, cat, auction=auction, max_price=cap,
                                   end_before=window_end if auction else None)
            except requests.RequestException as e:
                print(f"search failed [{q}] auction={auction}: {e}", file=sys.stderr)
                continue
            for raw in raws:
                lst = to_listing(raw, cat or "")
                if not lst or lst.item_id in seen:
                    continue
                if is_excluded(lst.title):
                    continue
                lst.grade = parse_grade(lst.title)
                if lst.grade is None:
                    continue
                if watch and str(watch.get("grade", lst.grade)) != lst.grade:
                    continue
                if lst.is_auction and (not lst.end or lst.end > window_end or lst.end < now):
                    continue
                if lst.feedback_score < cfg["min_seller_feedback_score"]:
                    continue
                if lst.feedback_pct is not None and lst.feedback_pct < cfg["min_seller_feedback_pct"]:
                    continue
                lst.watch = watch
                lst.grail, lst.grail_reasons = grail_check(
                    lst.title, rules, sports=(cat == cfg["categories"].get("sports")))
                if watch and watch.get("grail"):
                    lst.grail = True
                    lst.grail_reasons = lst.grail_reasons or ["on your watchlist"]
                if lst.all_in > cap:
                    continue
                seen[lst.item_id] = lst

    # Check soonest-ending auctions first so they get comp lookups before the budget runs out.
    ordered = sorted(seen.values(), key=lambda l: (
        0 if l.is_auction else 1,
        l.minutes_left(now) if l.is_auction else 0,
        0 if l.grail else 1,
    ))

    deals = []
    for lst in ordered:
        tier = classify(lst, cfg)
        if tier is None:
            continue
        mins = lst.minutes_left(now) if lst.is_auction else None
        final = lst.is_auction and mins is not None and mins <= cfg["final_call_minutes"]
        stage = "final" if final else "found"
        key = f"{lst.item_id}|{stage}"
        if key in alerted:
            continue
        if not final and f"{lst.item_id}|found" in alerted:
            continue
        comp = comps.get(lst)
        if comp is None or comp.value <= 0:
            continue
        limit = comp.value * (1 - cfg["discount_threshold"])
        if lst.all_in > limit:
            continue
        discount = 1 - lst.all_in / comp.value
        ship = lst.shipping if lst.shipping is not None else 5.0
        ceiling = round(limit - ship, 2) if lst.is_auction else None
        deals.append({
            "listing": lst,
            "tier": tier,
            "pg": tier == "steal" and lst.grail,
            "comp": comp,
            "discount": discount,
            "ceiling": ceiling,
            "minutes_left": mins,
            "final": final,
            "flags": red_flags(lst, comp, cfg),
            "key": key,
        })
    return deals


# ----------------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------------
COLORS = {"final": 0xE53935, "grail": 0xFFB300, "pg": 0x8E24AA, "steal": 0x43A047}


def embed_for(d: dict, now: datetime) -> dict:
    l: Listing = d["listing"]
    c: Comp = d["comp"]
    tag = ""
    if d["final"]:
        tag = "⏰ FINAL CALL · "
    if d["tier"] == "grail":
        tag += "🏆 GRAIL · "
    elif d["pg"]:
        tag += "🏆 PG · "
    else:
        tag += "💰 STEAL · "
    ship_txt = f"${l.shipping:.2f}" if l.shipping is not None else "~$5 est."
    lines = []
    if l.is_auction:
        lines.append(f"⏰ **Ends {fmt_pt(l.end)}** (in {fmt_left(d['minutes_left'])}) · {l.bids} bids")
        lines.append(f"Current bid ${l.price:.2f} + {ship_txt} ship = **${l.all_in:.2f} all-in**")
        lines.append(f"Deal ceiling: **bid up to ${d['ceiling']:.2f}** to stay 30%+ below comps")
    else:
        lines.append(f"Buy It Now: ${l.price:.2f} + {ship_txt} ship = **${l.all_in:.2f} all-in**")
    lines.append(f"Comps: **${c.value:.2f}** ({c.label}) · **{d['discount']*100:.0f}% below**")
    if l.grail_reasons and (d["tier"] == "grail" or d["pg"]):
        lines.append("Grail notes: " + ", ".join(l.grail_reasons))
    fb = f"{l.feedback_score}" + (f" ({l.feedback_pct}%)" if l.feedback_pct is not None else "")
    lines.append(f"Seller: {l.seller} · {fb}")
    lines.append("Red flags: " + (", ".join(d["flags"]) if d["flags"] else "none spotted"))
    lines.append("Verify the cert # on psacard.com before bidding.")
    if c.url:
        lines.append(f"[Check sold comps]({c.url})")
    e = {
        "title": (tag + l.title)[:256],
        "url": l.url,
        "description": "\n".join(lines)[:4000],
        "color": COLORS["final"] if d["final"] else COLORS["grail"] if d["tier"] == "grail" else COLORS["pg"] if d["pg"] else COLORS["steal"],
        "footer": {"text": f"PSA {l.grade} · eBay · item {l.item_id}"},
    }
    if l.image:
        e["thumbnail"] = {"url": l.image}
    return e


def order_deals(deals: list[dict]) -> list[dict]:
    tier_rank = {"grail": 0, "watch": 1, "steal": 2}
    return sorted(deals, key=lambda d: (
        0 if d["final"] else 1,
        0 if d["listing"].is_auction else 1,
        d["minutes_left"] if d["minutes_left"] is not None else 1e9,
        tier_rank.get(d["tier"], 3),
        0 if d["pg"] else 1,
        -d["discount"],
    ))


def post_discord(webhook: str, deals: list[dict], now: datetime, dry_run: bool, session=None) -> int:
    s = session or requests.Session()
    deals = order_deals(deals)
    sent = 0
    n_grail = sum(d["tier"] == "grail" for d in deals)
    n_pg = sum(d["pg"] for d in deals)
    n_ending = sum(1 for d in deals if d["minutes_left"] is not None and d["minutes_left"] <= 60)
    best = max((d["discount"] for d in deals), default=0)
    summary = (f"**PSA Sniper:** {len(deals)} deal(s) · {n_grail} grail · {n_pg} PG · "
               f"{n_ending} auction(s) ending within 1h · best {best*100:.0f}% below comps")
    for i in range(0, len(deals), 10):
        chunk = deals[i:i + 10]
        payload = {"username": "PSA Sniper",
                   "content": summary if i == 0 else None,
                   "embeds": [embed_for(d, now) for d in chunk]}
        if any(d["final"] for d in chunk):
            payload["content"] = ("@here ⏰ ENDING SOON\n" + (payload["content"] or "")).strip()
        if dry_run:
            print(json.dumps(payload, indent=2, default=str))
            sent += len(chunk)
            continue
        for attempt in range(3):
            r = s.post(webhook, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 2)))
                continue
            r.raise_for_status()
            sent += len(chunk)
            break
    return sent


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def prune(state: dict, days: int = 10):
    cutoff = time.time() - days * 86400
    state["alerted"] = {k: v for k, v in state.get("alerted", {}).items() if v > cutoff}
    state["comps"] = {k: v for k, v in state.get("comps", {}).items() if v.get("t", 0) > time.time() - 86400}


def sample_deal(now: datetime) -> dict:
    """A clearly-labelled fake alert, used to check the Discord webhook without eBay."""
    lst = Listing(
        item_id="TEST", title="TEST ALERT (not a real listing) - 2023 Pokemon 151 Charizard ex #199 PSA 10",
        url="https://www.ebay.com", price=30.0, shipping=4.0, is_auction=True, bids=3,
        end=now + timedelta(minutes=20), seller="test-seller", feedback_score=500,
        feedback_pct=99.8, image=None, grade="10", grail=True,
        grail_reasons=["charizard special illustration"])
    comp = Comp(67.5, 7, "test", "sample numbers only")
    return {"listing": lst, "tier": "steal", "pg": True, "comp": comp,
            "discount": 1 - lst.all_in / comp.value, "ceiling": round(67.5 * 0.7 - 4, 2),
            "minutes_left": 20.0, "final": True, "flags": ["this is a test message"],
            "key": "TEST|final"}


def main() -> int:
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        print(f"config not found: {CONFIG_PATH}", file=sys.stderr)
        return 2
    cid, sec = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    dry = os.environ.get("DRY_RUN") == "1"
    if not hook and not dry:
        print("Missing DISCORD_WEBHOOK_URL", file=sys.stderr)
        return 2
    if os.environ.get("DISCORD_TEST") == "1":
        now = datetime.now(UTC)
        post_discord(hook, [sample_deal(now)], now, dry)
        print("test alert sent to Discord")
        return 0
    if not cid or not sec:
        # Not an error yet: lets the schedule run quietly until the eBay keys are added.
        print("::warning::EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set yet; skipping scan")
        return 0

    state = load_json(STATE_PATH, {})
    prune(state)
    ebay = Ebay(cid, sec)
    comps = Comps(ebay, cfg, state.setdefault("comps", {}), os.environ.get("PRICECHARTING_TOKEN"))
    now = datetime.now(UTC)

    deals = scan(cfg, ebay, comps, state, now)
    if deals:
        post_discord(hook, deals, now, dry)
        for d in deals:
            state["alerted"][d["key"]] = time.time()
            state["alerted"][f'{d["listing"].item_id}|found'] = time.time()
    print(f"scan done: {len(deals)} deal(s), {ebay.calls} eBay calls, {comps.lookups} comp lookups")

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
