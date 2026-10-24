import os
import re
from urllib.parse import urlparse, quote_plus
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
TIMEOUT = 12
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()


def validate_amazon_url(url):
    """Accept common Amazon India URLs only; reject arbitrary URLs to reduce SSRF risk."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        allowed = {"amazon.in", "www.amazon.in", "m.amazon.in"}
        if parsed.scheme not in {"http", "https"} or host not in allowed:
            return False
        return bool(re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)", parsed.path, re.I))
    except Exception:
        return False


def extract_asin(url):
    match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)", url, re.I)
    return match.group(1).upper() if match else None


def amazon_metadata(url):
    """Best-effort metadata extraction. Amazon may block automated requests."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    def meta(*selectors):
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                value = tag.get("content") or tag.get_text(" ", strip=True)
                if value:
                    return value.strip()
        return ""

    title = meta('meta[property="og:title"]', "#productTitle")
    image = meta('meta[property="og:image"]', "#landingImage", "#imgBlkFront")
    description = meta('meta[name="description"]')
    # Remove common Amazon title suffixes where possible.
    title = re.sub(r"\s*:\s*Amazon\.in:.*$", "", title, flags=re.I).strip()
    if not title:
        title = "Amazon product " + (extract_asin(url) or "")
    return {"title": title, "image": image, "description": description}


def parse_price(value):
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


ACCESSORY_TERMS = {
    # Phone/tablet accessories
    "case", "cases", "cover", "covers", "back cover", "backcover", "back-case",
    "screen protector", "tempered glass", "protector", "guard", "skin", "skins",
    "phone skin", "vinyl skin", "charger", "charging cable", "usb cable", "lightning cable",
    "usb-c cable", "data cable", "adapter", "power adapter", "power bank", "earbuds case",
    "holder", "stand", "mount", "pouch", "sleeve", "replacement screen", "display protector",
    "lens protector", "camera protector", "ring holder", "replacement battery", "spare part",
    "spare parts", "repair kit", "phone holder", "car mount", "stylus tip", "remote cover",
    "camera lens", "camera guard", "charging dock", "charging stand",
    # Computer/TV/console accessories
    "cooling pad", "laptop stand", "monitor stand", "monitor arm", "privacy screen",
    "ssd enclosure", "hard drive enclosure", "caddy", "keyboard cover", "keycap set",
    "mouse pad", "mouse feet", "replacement ear pads", "ear tips", "controller skin",
    "console cover", "replacement shell", "hdmi cable", "displayport cable", "vga cable",
    "wall mount", "tv stand", "projector mount", "remote control",
    # Common accessory/parts listing cues
    "compatible with", "for iphone", "for apple iphone", "replacement part",
    "accessory kit", "accessories", "protective film", "screen film", "strap only",
    "band only", "replacement strap", "replacement band", "spare cable",
}

# Generic product families: if the search asks for a core product, avoid related
# products that merely mention it (for example, an RTX 5070 laptop when searching
# for an RTX 5070 graphics card).
PRODUCT_FAMILIES = [
    {
        "name": "graphics card",
        "query_terms": {"rtx", "gtx", "geforce", "radeon", "gpu", "graphics card", "video card"},
        "exclude_terms": {"laptop", "notebook", "gaming laptop", "desktop pc", "prebuilt pc", "computer"},
    },
    {
        "name": "processor",
        "query_terms": {"cpu", "processor", "ryzen", "core i3", "core i5", "core i7", "core i9", "intel core ultra"},
        "exclude_terms": {"laptop", "notebook", "gaming laptop", "desktop pc", "prebuilt pc", "motherboard"},
    },
    {
        "name": "mobile phone",
        "query_terms": {"iphone", "smartphone", "mobile phone", "android phone", "galaxy s", "pixel phone"},
        "exclude_terms": {"case", "cover", "screen protector", "tempered glass", "charger", "phone holder"},
    },
    {
        "name": "laptop",
        "query_terms": {"laptop", "notebook", "macbook"},
        "exclude_terms": {"laptop bag", "sleeve", "skin", "replacement screen", "charger", "keyboard cover"},
    },
    {
        "name": "monitor",
        "query_terms": {"monitor", "computer monitor", "gaming monitor"},
        "exclude_terms": {"laptop", "screen replacement", "monitor stand", "monitor arm", "privacy screen"},
    },
    {
        "name": "storage drive",
        "query_terms": {"ssd", "nvme", "hard drive", "hdd", "solid state drive"},
        "exclude_terms": {"laptop", "external enclosure", "ssd enclosure", "case", "caddy"},
    },
    {
        "name": "memory module",
        "query_terms": {"ram", "ddr4", "ddr5", "sodimm", "memory module"},
        "exclude_terms": {"laptop", "desktop computer", "motherboard"},
    },
    {
        "name": "headphones",
        "query_terms": {"headphones", "headset", "earbuds", "airpods", "earphones"},
        "exclude_terms": {"case", "cover", "replacement ear pads", "ear tips", "charging case"},
    },
    {
        "name": "game console",
        "query_terms": {"playstation", "ps5", "ps4", "xbox", "nintendo switch"},
        "exclude_terms": {"controller skin", "console cover", "case", "charging dock", "replacement shell"},
    },
]

def _normal_words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())

def _contains_phrase(text, phrase):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])", text or ""))

def _family_for_query(query):
    q = (query or "").lower()
    for family in PRODUCT_FAMILIES:
        if any(_contains_phrase(q, term) for term in family["query_terms"]):
            return family
    return None

def filter_text_search_results(results, query):
    """
    Filter obvious accessories and related-but-different product types.
    No retailer allowlist is applied. Price sanity checks only reject highly
    implausible low-priced listings for a few high-value product families.
    """
    q = (query or "").lower().strip()
    q_words = _normal_words(q)
    q_accessory_search = any(_contains_phrase(q, term) for term in ACCESSORY_TERMS)
    if q_accessory_search:
        return results

    # Keep numeric/model identifiers exact (e.g. 15 should not match 150).
    model_tokens = [w for w in q_words if any(ch.isdigit() for ch in w)]
    family = _family_for_query(q)
    filtered = []

    for item in results:
        title = (item.get("title") or "").lower()
        price = item.get("price")
        title_words = set(_normal_words(title))

        # Exclude accessory listings even when the main model name is present.
        if any(_contains_phrase(title, term) for term in ACCESSORY_TERMS):
            continue

        # If a specific model number was requested, require it as a whole token.
        if model_tokens and not all(token in title_words for token in model_tokens):
            continue

        # Product-family mismatch exclusions.
        if family:
            explicit_exclusion = any(_contains_phrase(q, term) for term in family["exclude_terms"])
            if not explicit_exclusion and any(_contains_phrase(title, term) for term in family["exclude_terms"]):
                continue

        # Price sanity guard for clearly mislabeled high-value phone listings.
        # It keeps plausible used/refurbished phones but rejects implausibly cheap
        # "iPhone 15" offers that are often cases/cables with incorrect titles.
        if "iphone" in q and any(t in q_words for t in ("15", "14", "13", "16", "17")):
            if isinstance(price, (int, float)) and price < 10000:
                continue

        # A matching model token alone is not enough when the listing title is
        # clearly an accessory or a different family. Require at least one useful
        # non-numeric search term when such terms exist.
        key_words = [
            w for w in q_words
            if len(w) > 1 and not any(ch.isdigit() for ch in w)
            and w not in {"the", "and", "for", "with", "from", "new", "best", "buy", "online", "price"}
        ]
        if key_words and not any(word in title_words for word in key_words):
            continue

        filtered.append(item)

    return filtered


def shopping_results(query):
    """Fetch Google Shopping offers without an approved-retailer allowlist.

    The user's requested retailer list is a guide to relevant ecommerce sources,
    not a safety allowlist. Google Shopping decides which stores appear for each query.
    """
    if not SERPAPI_KEY:
        return [], "Add SERPAPI_KEY to your .env file to enable live comparison prices."
    params = {
        "engine": "google_shopping",
        "q": query,
        "gl": "in",
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params,
                            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        results = []
        for item in payload.get("shopping_results", [])[:40]:
            price_text = item.get("price")
            price = parse_price(price_text)
            link = item.get("link") or item.get("product_link") or ""
            source = (item.get("source") or "").strip()
            if not source:
                source = urlparse(link).hostname or "Online store"
                source = source.removeprefix("www.")

            results.append({
                "store": source,
                "title": item.get("title", "Product listing"),
                "price": price,
                "price_text": price_text or "Price unavailable",
                "link": link,
                "image": item.get("thumbnail", ""),
                "rating": item.get("rating"),
                "reviews": item.get("reviews"),
            })
        results.sort(key=lambda x: (x["price"] is None, x["price"] if x["price"] is not None else float("inf")))
        return results, None
    except requests.RequestException as exc:
        return [], f"Price search failed: {exc.__class__.__name__}. Check your API key/network and try again."
    except (ValueError, TypeError):
        return [], "The price search provider returned an unexpected response."


@app.route("/")
def home():
    return render_template("index.html")


@app.post("/api/compare")
def compare():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or body.get("url") or "").strip()
    if not user_query:
        return jsonify({"error": "Enter a product name or paste an Amazon India product URL."}), 400

    # If the user pasted a URL, keep the Amazon-specific metadata workflow.
    is_url = user_query.lower().startswith(("http://", "https://"))
    if is_url:
        if not validate_amazon_url(user_query):
            return jsonify({"error": "Enter a valid Amazon India product URL, or type a product name instead."}), 400
        url = user_query
        asin = extract_asin(url)
        try:
            product = amazon_metadata(url)
        except requests.RequestException:
            product = {
                "title": "Amazon product " + (asin or ""),
                "image": "",
                "description": "Amazon did not allow direct page metadata access. Search results are based on the product ASIN."
            }
        query = product["title"]
        if not query or query.startswith("Amazon product"):
            query = asin or url
    else:
        # Text searches go straight to Google Shopping; no Amazon URL is required.
        url = ""
        asin = None
        query = user_query
        product = {"title": user_query, "image": "", "description": "Search results based on the product name you entered."}

    results, warning = shopping_results(query)

    # Filter text searches so cheap accessories and mismatched model numbers do not
    # appear as the lowest price for the requested product.
    if not is_url:
        results = filter_text_search_results(results, user_query)
        if not results and not warning:
            warning = (
                "No matching listings remained after filtering obvious accessories. Try a more specific product name, "
                "or include the accessory type if that is what you want to compare."
            )

    # For text searches, use the first remaining relevant shopping result as preview.
    if not is_url and results:
        product["title"] = results[0].get("title") or user_query
        product["image"] = results[0].get("image") or ""

    # Include an Amazon shortcut for URL searches; for text searches, provide an Amazon search link.
    amazon_link = url if is_url else "https://www.amazon.in/s?k=" + quote_plus(user_query)
    if is_url:
        results.append({
            "store": "Amazon India",
            "title": product["title"],
            "price": None,
            "price_text": "Open Amazon to check current price",
            "link": amazon_link,
            "image": product.get("image", ""),
            "rating": None,
            "reviews": None,
        })

    results.sort(key=lambda x: (x["price"] is None, x["price"] if x["price"] is not None else float("inf")))
    numeric = [x for x in results if isinstance(x.get("price"), (int, float))]
    cheapest = numeric[0] if numeric else None
    return jsonify({
        "product": {**product, "asin": asin, "url": amazon_link},
        "results": results,
        "cheapest": cheapest,
        "warning": warning,
        "source_note": "Listings are supplied by Google Shopping and are not restricted to an approved-retailer allowlist. Check the store, seller, exact model, warranty, return policy, and checkout domain before buying. Prices can be delayed or vary by product variant."
    })

@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "1") == "1", host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
