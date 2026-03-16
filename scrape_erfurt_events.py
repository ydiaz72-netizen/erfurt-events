#!/usr/bin/env python3
"""
Erfurt Events Scraper
Generates a bilingual DE/EN HTML dashboard for events in Erfurt, Germany.
Sources: Stadt Erfurt, Frauenzentrum, Theater Erfurt, Anger Museum, EGA Park
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os, re, json, hashlib, html as html_lib, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime

try:
    import pdfplumber
    PDF_OK = True
except ImportError:
    PDF_OK = False
    print("Warning: pdfplumber not installed. PDF events will be skipped.")

try:
    import anthropic, base64
    _ANTHROPIC_KEY = ""
    _env_candidates = [
        r"C:\Users\YDR\conflict_image_scraper\.env",
        r"C:\Users\YDR\.env",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ]
    for _ep in _env_candidates:
        if os.path.exists(_ep):
            for _line in open(_ep, encoding="utf-8", errors="ignore"):
                if _line.startswith("ANTHROPIC_API_KEY="):
                    _ANTHROPIC_KEY = _line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if _ANTHROPIC_KEY:
            break
    VISION_OK = bool(_ANTHROPIC_KEY)
    if VISION_OK:
        print("Vision API ready (date extraction from images enabled)")
    else:
        print("Warning: ANTHROPIC_API_KEY not found – image date extraction disabled")
except ImportError:
    VISION_OK = False
    print("Warning: anthropic package not installed – image date extraction disabled")

def extract_date_from_image(img_path):
    """Use Claude Vision to extract a date printed on an event poster image."""
    if not VISION_OK or not img_path or not os.path.exists(img_path):
        return ""
    try:
        ext = os.path.splitext(img_path)[1].lower().lstrip(".")
        media_type = {"jpg":"image/jpeg","jpeg":"image/jpeg",
                      "png":"image/png","webp":"image/webp",
                      "gif":"image/gif"}.get(ext, "image/jpeg")
        with open(img_path, "rb") as fh:
            img_data = base64.standard_b64encode(fh.read()).decode("utf-8")
        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": img_data}},
                {"type": "text", "text": (
                    "Is there a date visible in this image? "
                    "Extract it as DD.MM.YYYY. "
                    "If no date is visible, reply exactly: none. "
                    "Reply only with the date or none."
                )}
            ]}]
        )
        result = msg.content[0].text.strip()
        if result.lower() != "none" and re.search(r'\d{1,2}\.\d{1,2}', result):
            return result
    except Exception as e:
        print(f"  Vision date extraction failed: {e}")
    return ""

PDF_PATH = r"C:\Users\YDR\OneDrive\Documents\News Scraper\Programm_Maerz.pdf"

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False
    print("Warning: selenium not installed. JS-rendered sites will be skipped.")

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--lang=de-DE")
    opts.add_argument(f"user-agent={HEADERS['User-Agent']}")
    return webdriver.Chrome(options=opts)

def GET_JS(driver, url, wait_selector=None, pause=3):
    """Load a page with Selenium, return BeautifulSoup."""
    try:
        driver.get(url)
        if wait_selector:
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except Exception:
                pass
        time.sleep(pause)
        return BeautifulSoup(driver.page_source, "html.parser")
    except Exception as e:
        print(f"  Selenium error {url}: {e}")
        return None

# ─── Paths ────────────────────────────────────────────────────────────────────
SAVE_DIR   = r"C:\Users\YDR\erfurt-events"
OUTPUT_DIR = os.path.join(SAVE_DIR, "index_files")
HTML_OUT   = os.path.join(SAVE_DIR, "index.html")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── HTTP ─────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 20

def GET(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  Fetch error {url}: {e}")
        return None

# ─── Categories ───────────────────────────────────────────────────────────────
CATEGORY_KEYWORDS = {
    "Konzerte":        ["konzert","musik","band","orchester","oper","jazz","rock",
                        "philharmonie","sinfonie","chor","musical","festival","recital","lied"],
    "Sport":           ["sport","fußball","lauf","marathon","turnier","fitness",
                        "schwimm","tennis","radtour","yoga","wanderung","sportfest"],
    "Kultur":          ["museum","ausstellung","theater","kunst","kino","film","tanz",
                        "lesung","literatur","galerie","vernissage","aufführung","schauspiel",
                        "ballett","ballet","führung","bildende"],
    "Politik":         ["wahl","bürger","rat","politik","verwaltung","stadtrat","demo",
                        "demonstration","partei","podium","diskussion","bürgerdialog"],
    "Märkte":          ["markt","messe","flohmarkt","wochenmarkt","weihnacht","advent",
                        "ostermarkt","bauernmarkt","krammarkt","händler","basar"],
    "Sozialprogramme": ["sozial","beratung","hilfe","workshop","seminar","kurs","vortrag",
                        "gruppe","selbsthilfe","frauen","kinder","jugend","senioren",
                        "integration","erziehung","pflege","gesundheit"],
}
CATEGORY_ICONS  = {"Konzerte":"🎵","Sport":"⚽","Kultur":"🎭","Politik":"🏛️",
                    "Märkte":"🛒","Sozialprogramme":"🤝","Sonstige":"📅"}
CATEGORY_EN     = {"Konzerte":"Concerts","Sport":"Sports","Kultur":"Culture",
                    "Politik":"Politics","Märkte":"Markets",
                    "Sozialprogramme":"Social Programs","Sonstige":"Other"}
CATEGORY_COLORS = {"Konzerte":"#7c3aed","Sport":"#059669","Kultur":"#c0392b",
                    "Politik":"#2563eb","Märkte":"#d97706",
                    "Sozialprogramme":"#db2777","Sonstige":"#475569"}

def classify_category(title, hint=""):
    text = (title + " " + hint).lower()
    scores = {c: sum(1 for kw in kws if kw in text)
              for c, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Sonstige"

def detect_pricing(price_text):
    t = (price_text or "").lower()
    return {
        "free":    any(w in t for w in ["kostenlos","frei","gratis","eintritt frei"]),
        "senior":  any(w in t for w in ["senior","senioren","rentner","65+"]),
        "citizen": any(w in t for w in ["bürger","bürgerpreis","ermäßig"]),
    }

# ─── Image download ────────────────────────────────────────────────────────────
def download_image(url, title):
    if not url or url.startswith("data:"):
        return None
    try:
        slug = re.sub(r"[^\w]", "_", title[:35])
        ext  = os.path.splitext(urlparse(url).path)[1][:5] or ".jpg"
        fname = f"{slug}_{hashlib.md5(url.encode()).hexdigest()[:6]}{ext}"
        fpath = os.path.join(OUTPUT_DIR, fname)
        if not os.path.exists(fpath):
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            if r.status_code == 200:
                with open(fpath, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
        return fname
    except Exception as e:
        print(f"  Image failed: {e}")
        return None

def best_img(soup, item, base):
    """Try several strategies to get the best image URL."""
    # 1. og:image from page head
    og = soup.find("meta", property="og:image") if soup else None
    if og and og.get("content"):
        return urljoin(base, og["content"])
    # 2. img inside the item
    img = item.find("img") if item else None
    if img:
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and not src.endswith(".svg"):
            return urljoin(base, src)
    return None

def parse_date_iso(date_str):
    """Parse German date string to YYYY-MM-DD for JS filtering. Returns '' if unparseable."""
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', date_str or "")
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""

def make_event(source, stype, title, date_str, location, price, url,
               img_file, description="", hint=""):
    return {
        "source":      source,
        "type":        stype,
        "title":       title.strip(),
        "date_str":    date_str.strip(),
        "date_iso":    parse_date_iso(date_str),
        "location":    location.strip() or "Erfurt",
        "price":       price.strip(),
        "url":         url,
        "img_file":    img_file,
        "category":    classify_category(title, hint),
        "pricing":     detect_pricing(price),
        "description": description.strip()[:300],
    }

# ─── Scrapers ─────────────────────────────────────────────────────────────────
def scrape_erfurt_de():
    events = []
    base = "https://www.erfurt.de"
    print("\n-- Scraping Stadt Erfurt --")

    for path in ["/ef/de/erleben/veranstaltungen/index.html",
                 "/ef/de/erleben/veranstaltungen/",
                 "/veranstaltungen", "/"]:
        soup = GET(base + path)
        if not soup:
            continue

        # Multiple selector patterns for different CMS versions
        items = (
            soup.select(".tx-cal-controller li.veranstaltung") or
            soup.select(".event-list .event") or
            soup.select("article[class*='event']") or
            soup.select(".veranstaltungen-liste li") or
            soup.select(".c-event-teaser") or
            soup.select(".news-list-item") or
            soup.select(".c-teaser, .teaser-item") or
            []
        )

        # Generic fallback: elements containing German date pattern
        if not items:
            candidates = soup.find_all(["article", "li", "div"])
            items = [t for t in candidates
                     if t.find(["h2","h3","h4"]) and
                     re.search(r'\d{1,2}\.\d{1,2}\.', t.get_text())][:30]

        print(f"  Found {len(items)} items at {base+path}")
        if items:
            break

    seen = set()
    for item in items[:25]:
        a = item.find("a", href=True)
        title_el = item.find(["h2","h3","h4","strong"])
        title = ""
        if title_el:
            title = title_el.get_text(strip=True)
        elif a:
            title = a.get_text(strip=True)
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)

        link = urljoin(base, a["href"]) if a else base + path
        date_m = re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}', item.get_text())
        date_str = date_m.group().strip() if date_m else ""

        loc_el = item.find(class_=re.compile(r"location|ort|venue", re.I))
        location = loc_el.get_text(strip=True) if loc_el else "Erfurt"

        price_el = item.find(class_=re.compile(r"preis|price|eintritt", re.I))
        price_m  = re.search(r'\d+[,.]?\d*\s*€|kostenlos|frei', item.get_text(), re.I)
        price = (price_el.get_text(strip=True) if price_el
                 else price_m.group() if price_m else "")

        img_url  = best_img(None, item, base)
        img_file = download_image(img_url, title)

        events.append(make_event("Stadt Erfurt","staatlich",title,date_str,
                                  location,price,link,img_file))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events found (site may require JavaScript)")
    return events


def scrape_frauenzentrum():
    events = []
    base = "https://www.frauenzentrum-erfurt.de"
    url  = base + "/termine-fuer-frauen/"
    print("\n-- Scraping Frauenzentrum Erfurt --")

    soup = GET(url)
    if not soup:
        return events

    # WordPress + The Events Calendar plugin patterns
    items = (
        soup.select(".tribe-events-list .tribe-event-list-widget-events__event") or
        soup.select("article.tribe_events_cat") or
        soup.select(".tribe-event") or
        soup.select("article.post, article.event") or
        soup.select(".termin, .event-item") or
        []
    )

    if not items:
        # Fallback: paragraphs/divs near dates
        items = []
        for tag in soup.find_all(["div","p","li"]):
            if re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', tag.get_text()):
                items.append(tag)
        items = items[:20]

    print(f"  Found {len(items)} items")
    seen = set()

    for item in items[:20]:
        title_el = item.find(["h2","h3","h4","strong"])
        a = item.find("a", href=True)
        if title_el:
            title = title_el.get_text(strip=True)
        elif a:
            title = a.get_text(strip=True)
        else:
            continue
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)

        link = urljoin(base, a["href"]) if a else url
        date_m = re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', item.get_text())
        date_str = date_m.group() if date_m else ""

        desc_el = item.find("p")
        description = desc_el.get_text(strip=True)[:200] if desc_el else ""

        price_m = re.search(r'\d+[,.]?\d*\s*€|kostenlos|frei', item.get_text(), re.I)
        price = price_m.group() if price_m else "Kostenlos"

        img_url  = best_img(None, item, base)
        img_file = download_image(img_url, title)

        events.append(make_event(
            "Frauenzentrum Erfurt","privat",title,date_str,
            "Frauenzentrum Erfurt",price,link,img_file,description,
            hint="frauen sozial beratung workshop seminar kurs"
        ))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events found")
    return events


def scrape_theater_erfurt(driver=None):
    events = []
    base = "https://www.theater-erfurt.de"
    print("\n-- Scraping Theater Erfurt (Selenium) --")

    if not SELENIUM_OK or not driver:
        print("  Selenium not available – skipping")
        return events

    # Confirmed JS-rendered paths from investigation
    soup = None
    candidates = []
    for path in ["/stuecke", "/grosses-haus/alle-stuecke", "/grosses-haus/premieren", "/"]:
        url  = base + path
        soup = GET_JS(driver, url, wait_selector=".event, article, h2, h3", pause=5)
        if not soup:
            continue

        candidates = (
            soup.select(".event-card, .stueck-card, .production-card") or
            soup.select("[class*='stueck'], [class*='event'], [class*='vorstellung']") or
            soup.select("article.production, article.event") or
            soup.select("article") or
            []
        )
        candidates = [c for c in candidates if c.find(["h2","h3","h4"])]
        if len(candidates) > 30:
            candidates = [c for c in candidates
                          if re.search(r'\d{1,2}\.\d{1,2}', c.get_text())][:20]

        print(f"  Found {len(candidates)} items at {url}")
        if candidates:
            break

    NAV_TERMS = ("gutschein", "kontakt", "anreise", "unterkunft", "impressum",
                 "datenschutz", "newsletter", "agb", "barrierefreiheit",
                 "tickethotline", "in kontakt")

    seen = set()
    for item in (candidates if 'candidates' in dir() and candidates else []):
        title_el = item.find(["h2","h3","h4"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 3 or title in seen:
            continue
        if any(t in title.lower() for t in NAV_TERMS):
            continue
        seen.add(title)

        a = item.find("a", href=True)
        link = urljoin(base, a["href"]) if a else base

        date_m = re.search(r'\d{1,2}\.\s*\d{1,2}\.(\s*\d{4})?', item.get_text())
        date_str = date_m.group().strip() if date_m else ""

        price_el = item.find(class_=re.compile(r"price|preis|ticket", re.I))
        price_m  = re.search(r'\d+[,.]?\d*\s*€', item.get_text())
        price = (price_el.get_text(strip=True) if price_el
                 else price_m.group() if price_m else "")

        img_url  = best_img(None, item, base)
        img_file = download_image(img_url, title)

        events.append(make_event(
            "Theater Erfurt","staatlich",title,date_str,
            "Theater Erfurt",price,link,img_file,
            hint="theater oper konzert musik schauspiel ballett"
        ))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events found")
    return events


def scrape_anger_museum(driver=None):
    events = []
    base      = "https://kunstmuseen.erfurt.de"
    img_base  = base  # images served from same host
    events_url = base + "/km/de/angermuseum/veranstaltungen/index.itl"
    print("\n-- Scraping Anger Museum (Kunstmuseen Erfurt) --")

    soup = GET(events_url)
    if not soup:
        print("  Static fetch failed")
        return events

    # Confirmed selector: article.item.ym-clearfix
    articles = soup.select("article.item.ym-clearfix")
    print(f"  Found {len(articles)} event articles")

    seen = set()
    for art in articles[:20]:
        # Title and link are inside h3 > a.ef-news-headline
        title_a = art.select_one("h3 a.ef-news-headline")
        if not title_a:
            continue
        title = title_a.get_text(strip=True)
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)

        link = urljoin(base, title_a["href"])

        # Date: span.ef-meta-date  e.g. "18.03.2026 13:00 – 18.03.2026 13:15"
        date_el  = art.select_one("span.ef-meta-date")
        date_str = date_el.get_text(strip=True).split("–")[0].strip() if date_el else ""

        # Description: the <p> that is NOT class ef-meta-info and NOT contains Weiterlesen
        desc_p = art.find("p", class_=lambda c: not c or "ef-meta" not in " ".join(c))
        description = desc_p.get_text(strip=True)[:250] if desc_p else ""

        # Price: check description text
        price_m = re.search(r'\d+[,.]?\d*\s*€|kostenlos|frei', description, re.I)
        price = price_m.group() if price_m else ("kostenlos" if "kostenlos" in description.lower() or "frei" in description.lower() else "")

        # Image: uses data-src (lazy-loaded)
        img_el  = art.select_one("img.lazyload")
        img_src = img_el.get("data-src") if img_el else None
        img_url = urljoin(img_base, img_src) if img_src else None
        img_file = download_image(img_url, title)

        events.append(make_event(
            "Anger Museum","staatlich",title,date_str,
            "Anger Museum Erfurt",price,link,img_file,description,
            hint="museum ausstellung kunst führung galerie"
        ))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events found")
    return events


def scrape_egapark(driver=None):
    events = []
    base       = "https://www.egapark-erfurt.de"
    # Confirmed redirect URL from investigation
    events_url = base + "/pb/egapark/veranstaltungen"
    print("\n-- Scraping EGA Park Erfurt --")

    SKIP_TILES = ("veranstaltungskalender", "veranstaltungsreihen", "zum veranstaltung",
                  "65 jahre", "information", "service", "anfahrt", "öffnungszeit",
                  "eintritt", "newsletter", "barrierefreiheit", "unsere veranstaltung")

    soup = GET(events_url)
    if not soup and SELENIUM_OK and driver:
        soup = GET_JS(driver, events_url, wait_selector="a.tile__element, h5", pause=4)

    candidates = []
    if soup:
        # Confirmed selector from investigation: a.tile__element with h5 title
        tiles = soup.select("a.tile__element")
        print(f"  Found {len(tiles)} tiles")
        for tile in tiles:
            h5 = tile.select_one("h5")
            if not h5:
                continue
            if any(sk in h5.get_text(strip=True).lower() for sk in SKIP_TILES):
                continue
            candidates.append(tile)

    seen = set()
    for tile in candidates[:20]:
        h5    = tile.select_one("h5")
        title = h5.get_text(strip=True) if h5 else ""
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)

        link = tile.get("href", base)
        if link.startswith("/"):
            link = urljoin(base, link)

        full_text = tile.get_text(" ", strip=True)
        date_m = re.search(r'\d{1,2}\.\s*\d{1,2}\.(\s*\d{4})?', full_text)
        date_str = date_m.group().strip() if date_m else ""

        price_m = re.search(r'\d+[,.]?\d*\s*€|kostenlos|frei', full_text, re.I)
        price = price_m.group() if price_m else ""

        img_el  = tile.select_one("img")
        img_src = img_el.get("src") or img_el.get("data-src") if img_el else None
        img_url = urljoin(base, img_src) if img_src else None
        img_file = download_image(img_url, title)

        # If no date found in tile, fetch the event detail page
        if not date_str and link and link != base:
            detail = GET(link)
            if detail:
                detail_text = detail.get_text(" ")
                dm = re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*\d{4}', detail_text)
                if dm:
                    date_str = dm.group().strip()
                    print(f"    Date from detail page: {date_str}")
                # Also try to get a better image from detail page
                if not img_file:
                    og = detail.find("meta", property="og:image")
                    if og and og.get("content"):
                        img_url = urljoin(base, og["content"])
                        img_file = download_image(img_url, title)

        events.append(make_event(
            "EGA Park","staatlich",title,date_str,
            "EGA Park Erfurt",price,link,img_file,
            hint="park garten natur freizeit festival"
        ))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events found")
    return events


# ─── HTML Generator ───────────────────────────────────────────────────────────
def js_str(s):
    """Escape a string for use inside a JS single-quoted string."""
    return (s or "").replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", "")

def generate_html(events):
    today = datetime.now().strftime("%d.%m.%Y")
    total = len(events)

    # ── Event cards ───────────────────────────────────────────────────────────
    cards_html = ""
    for ev in events:
        cat       = ev["category"]
        cat_en    = CATEGORY_EN.get(cat, cat)
        cat_icon  = CATEGORY_ICONS.get(cat, "📅")
        cat_color = CATEGORY_COLORS.get(cat, "#475569")
        pricing   = ev["pricing"]

        # Image / placeholder
        if ev["img_file"]:
            img_html = f'<img src="index_files/{html_lib.escape(ev["img_file"])}" alt="" loading="lazy">'
        else:
            img_html = (
                '<div class="no-img">'
                '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" '
                'stroke="#64748b" stroke-width="1.2">'
                '<rect x="3" y="3" width="18" height="18" rx="2"/>'
                '<circle cx="8.5" cy="8.5" r="1.5"/>'
                '<polyline points="21 15 16 10 5 21"/>'
                '</svg>'
                '<span class="de">Kein Bild</span>'
                '<span class="en">No Image</span>'
                '</div>'
            )

        # Type badge
        if ev["type"] == "staatlich":
            type_badge = ('<span class="badge-type staatlich">'
                          '<span class="de">Staatlich</span>'
                          '<span class="en">Government</span></span>')
        else:
            type_badge = ('<span class="badge-type privat">'
                          '<span class="de">Privat</span>'
                          '<span class="en">Private</span></span>')

        # Category badge
        cat_badge = (f'<span class="badge-cat" style="background:{cat_color}">'
                     f'{cat_icon} <span class="de">{html_lib.escape(cat)}</span>'
                     f'<span class="en">{html_lib.escape(cat_en)}</span></span>')

        # Price section
        price_parts = []
        if ev["price"]:
            price_parts.append(f'<span class="price-amount">{html_lib.escape(ev["price"])}</span>')
        if pricing["free"]:
            price_parts.append('<span class="badge-special free"><span class="de">Kostenlos</span><span class="en">Free Entry</span></span>')
        if pricing["senior"]:
            price_parts.append('<span class="badge-special senior"><span class="de">Seniorenrabatt</span><span class="en">Senior Discount</span></span>')
        if pricing["citizen"]:
            price_parts.append('<span class="badge-special citizen"><span class="de">Bürgerpreis</span><span class="en">Citizen Price</span></span>')
        if not price_parts:
            price_parts.append('<span class="price-unknown"><span class="de">Preis auf Anfrage</span><span class="en">Price on request</span></span>')
        price_html = " ".join(price_parts)

        # Date display
        date_display = html_lib.escape(ev["date_str"]) if ev["date_str"] else (
            '<em class="de">Datum unbekannt</em><em class="en">Date unknown</em>')

        cards_html += f"""
  <div class="event-card" data-category="{html_lib.escape(cat)}" data-type="{ev['type']}" data-date="{ev['date_iso']}" data-source="{html_lib.escape(ev['source'])}">
    <div class="card-img-wrap">
      {img_html}
      <div class="card-badges">
        {type_badge}
        {cat_badge}
      </div>
    </div>
    <div class="card-body">
      <h3 class="card-title">
        <a href="{html_lib.escape(ev['url'])}" target="_blank" rel="noopener">{html_lib.escape(ev['title'])}</a>
      </h3>
      <div class="card-meta">
        <span class="meta-item"><span class="meta-icon">📅</span>{date_display}</span>
        <span class="meta-item"><span class="meta-icon">📍</span>{html_lib.escape(ev['location'])}</span>
        <span class="meta-item"><span class="meta-icon">🏢</span>{html_lib.escape(ev['source'])}</span>
      </div>
      <div class="card-price">{price_html}</div>
      <div class="card-actions">
        <a href="{html_lib.escape(ev['url'])}" target="_blank" rel="noopener" class="btn-link">
          <span class="de">Mehr erfahren →</span>
          <span class="en">Learn more →</span>
        </a>
        <button class="btn-cal" onclick="addToCalendar('{js_str(ev['title'])}','{js_str(ev['date_str'])}','{js_str(ev['location'])}','{js_str(ev['description'])}','{js_str(ev['url'])}')">
          📅 <span class="de">Kalender</span><span class="en">Calendar</span>
        </button>
      </div>
    </div>
  </div>"""

    # ── Filter buttons ────────────────────────────────────────────────────────
    cat_btns = """    <button class="filter-btn active" data-cat="all" onclick="filterCat(this)">
      <span class="de">Alle</span><span class="en">All</span>
    </button>"""
    for cat in ["Konzerte","Sport","Kultur","Politik","Märkte","Sozialprogramme"]:
        icon = CATEGORY_ICONS[cat]
        en   = CATEGORY_EN[cat]
        cat_btns += f"""
    <button class="filter-btn" data-cat="{html_lib.escape(cat)}" onclick="filterCat(this)">
      {icon} <span class="de">{html_lib.escape(cat)}</span><span class="en">{html_lib.escape(en)}</span>
    </button>"""

    # ── Source filter buttons ─────────────────────────────────────────────────
    unique_sources = sorted(set(ev["source"] for ev in events))
    src_btns = """    <button class="filter-btn active" data-src="all" onclick="filterSrc(this)">
      <span class="de">Alle Quellen</span><span class="en">All Sources</span>
    </button>"""
    for src in unique_sources:
        src_btns += f"""
    <button class="filter-btn" data-src="{html_lib.escape(src)}" onclick="filterSrc(this)">
      {html_lib.escape(src)}
    </button>"""

    if not events:
        cards_html = """
  <div class="no-events">
    <div style="font-size:3.5rem;margin-bottom:1rem">📭</div>
    <p class="de">Keine Veranstaltungen gefunden. Die Webseiten könnten JavaScript-Rendering erfordern oder ihre Struktur geändert haben.</p>
    <p class="en">No events found. The websites may require JavaScript rendering or may have changed their structure.</p>
  </div>"""

    # ── Full HTML ─────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="de" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Erfurt Veranstaltungen / Events</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ font-family: 'Inter', sans-serif; font-size: 15px; scroll-behavior: smooth; }}

/* ── CSS Variables ── */
html.dark {{
  --bg:       #1a0a0d;
  --surface:  #2a1218;
  --surface2: #351820;
  --border:   #4d2028;
  --text:     #f0e8e9;
  --text-muted: #c4909a;
  --accent:   #c0172c;
  --accent-h: #96101f;
  --header-bg:#0d0507;
  --badge-gov:#1d4ed8;
  --badge-prv:#92400e;
  --shadow:   0 4px 20px rgba(0,0,0,.45);
  --card-shadow: 0 2px 12px rgba(0,0,0,.35);
}}
html.light {{
  --bg:       #fdf5f5;
  --surface:  #ffffff;
  --surface2: #fef0f0;
  --border:   #f0d0d4;
  --text:     #1a0508;
  --text-muted: #7a4048;
  --accent:   #c0172c;
  --accent-h: #96101f;
  --header-bg:#8b0016;
  --badge-gov:#1d4ed8;
  --badge-prv:#b45309;
  --shadow:   0 4px 20px rgba(0,0,0,.10);
  --card-shadow: 0 2px 8px rgba(0,0,0,.08);
}}

/* ── Language ── */
html[lang="de"] .en {{ display: none; }}
html[lang="en"] .de {{ display: none; }}

/* ── Body ── */
body {{
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  line-height: 1.5;
}}

/* ── Header ── */
header {{
  background: var(--header-bg);
  border-bottom: 2px solid var(--accent);
  padding: 0;
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: var(--shadow);
}}
.header-inner {{
  max-width: 1400px;
  margin: 0 auto;
  padding: 1rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}}
.header-logo {{
  display: flex;
  align-items: center;
  gap: .75rem;
  flex: 1 1 auto;
}}
.header-logo .city-icon {{
  font-size: 2rem;
  line-height: 1;
}}
.header-logo h1 {{
  font-size: 1.2rem;
  font-weight: 700;
  color: #fff;
  line-height: 1.2;
}}
.header-logo .subtitle {{
  font-size: .75rem;
  color: #94a3b8;
  margin-top: .1rem;
}}
.header-controls {{
  display: flex;
  gap: .5rem;
  align-items: center;
}}
.btn-ctrl {{
  background: rgba(255,255,255,.1);
  border: 1px solid rgba(255,255,255,.2);
  color: #fff;
  padding: .4rem .8rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: .8rem;
  font-weight: 500;
  font-family: inherit;
  transition: background .15s;
  white-space: nowrap;
}}
.btn-ctrl:hover {{ background: rgba(255,255,255,.2); }}
.btn-ctrl.active {{ background: var(--accent); border-color: var(--accent); }}

/* ── Stats bar ── */
.stats-bar {{
  background: rgba(0,0,0,.2);
  padding: .4rem 1.5rem;
  font-size: .75rem;
  color: #94a3b8;
  display: flex;
  gap: 1rem;
  max-width: 1400px;
  margin: 0 auto;
  flex-wrap: wrap;
}}
.stats-bar span {{ display: flex; align-items: center; gap: .3rem; }}

/* ── Filters ── */
.filters-wrap {{
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 72px;
  z-index: 90;
}}
.filters-inner {{
  max-width: 1400px;
  margin: 0 auto;
  padding: .5rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: .4rem;
}}
.filter-row {{
  display: flex;
  gap: .5rem;
  flex-wrap: wrap;
  align-items: center;
}}
.filter-label {{
  font-size: .7rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .05em;
  white-space: nowrap;
  padding-right: .25rem;
}}
.filter-divider {{
  width: 1px;
  height: 1.2rem;
  background: var(--border);
  flex-shrink: 0;
}}
.filter-btn {{
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: .35rem .75rem;
  border-radius: 20px;
  cursor: pointer;
  font-size: .78rem;
  font-family: inherit;
  font-weight: 500;
  transition: all .15s;
  white-space: nowrap;
}}
.filter-btn:hover {{
  border-color: var(--accent);
  color: var(--accent);
}}
.filter-btn.active {{
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}}

/* ── Main grid ── */
main {{
  max-width: 1400px;
  margin: 0 auto;
  padding: 1.5rem;
}}
.events-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.25rem;
}}

/* ── Event card ── */
.event-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: var(--card-shadow);
  transition: transform .2s, box-shadow .2s;
}}
.event-card:hover {{
  transform: translateY(-3px);
  box-shadow: var(--shadow);
}}
.event-card.hidden {{ display: none; }}

/* ── Card image ── */
.card-img-wrap {{
  position: relative;
  height: 185px;
  overflow: hidden;
  background: var(--surface2);
  flex-shrink: 0;
}}
.card-img-wrap img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform .35s;
}}
.event-card:hover .card-img-wrap img {{ transform: scale(1.04); }}
.no-img {{
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: .5rem;
  color: var(--text-muted);
  font-size: .78rem;
  background: repeating-linear-gradient(
    45deg, var(--surface2), var(--surface2) 10px, var(--surface) 10px, var(--surface) 20px);
}}
.card-badges {{
  position: absolute;
  top: .6rem;
  left: .6rem;
  right: .6rem;
  display: flex;
  gap: .4rem;
  flex-wrap: wrap;
}}

/* ── Badges ── */
.badge-type, .badge-cat {{
  display: inline-flex;
  align-items: center;
  gap: .25rem;
  padding: .2rem .55rem;
  border-radius: 4px;
  font-size: .67rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .04em;
  color: #fff;
  backdrop-filter: blur(6px);
}}
.badge-type.staatlich {{ background: rgba(29,78,216,.85); }}
.badge-type.privat    {{ background: rgba(180,83,9,.85); }}
.badge-cat            {{ font-size: .7rem; text-transform: none; letter-spacing: 0; }}

/* ── Card body ── */
.card-body {{
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: .6rem;
  flex: 1;
}}
.card-title {{
  font-size: .92rem;
  font-weight: 600;
  line-height: 1.35;
}}
.card-title a {{
  color: var(--text);
  text-decoration: none;
  transition: color .15s;
}}
.card-title a:hover {{ color: var(--accent); }}

/* ── Meta ── */
.card-meta {{
  display: flex;
  flex-direction: column;
  gap: .3rem;
}}
.meta-item {{
  display: flex;
  align-items: flex-start;
  gap: .4rem;
  font-size: .78rem;
  color: var(--text-muted);
  line-height: 1.4;
}}
.meta-icon {{ flex-shrink: 0; font-size: .85rem; margin-top: .05rem; }}

/* ── Price ── */
.card-price {{
  display: flex;
  flex-wrap: wrap;
  gap: .35rem;
  align-items: center;
  margin-top: auto;
  padding-top: .4rem;
  border-top: 1px solid var(--border);
}}
.price-amount {{
  font-size: .82rem;
  font-weight: 600;
  color: var(--text);
}}
.price-unknown {{ font-size: .78rem; color: var(--text-muted); font-style: italic; }}
.badge-special {{
  display: inline-block;
  padding: .15rem .45rem;
  border-radius: 4px;
  font-size: .68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .04em;
}}
.badge-special.free    {{ background: #14532d; color: #86efac; }}
html.light .badge-special.free {{ background: #dcfce7; color: #166534; }}
.badge-special.senior  {{ background: #1e3a5f; color: #93c5fd; }}
html.light .badge-special.senior {{ background: #dbeafe; color: #1e40af; }}
.badge-special.citizen {{ background: #4a1d96; color: #c4b5fd; }}
html.light .badge-special.citizen {{ background: #ede9fe; color: #6d28d9; }}

/* ── Card actions ── */
.card-actions {{
  display: flex;
  gap: .5rem;
  flex-wrap: wrap;
}}
.btn-link {{
  flex: 1;
  text-align: center;
  padding: .45rem .75rem;
  border-radius: 7px;
  background: var(--accent);
  color: #fff;
  font-size: .78rem;
  font-weight: 600;
  text-decoration: none;
  transition: background .15s;
  white-space: nowrap;
}}
.btn-link:hover {{ background: var(--accent-h); }}
.btn-cal {{
  padding: .45rem .75rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface2);
  color: var(--text);
  font-size: .78rem;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: border-color .15s, color .15s;
  white-space: nowrap;
}}
.btn-cal:hover {{ border-color: var(--accent); color: var(--accent); }}

/* ── No events state ── */
.no-events {{
  grid-column: 1 / -1;
  text-align: center;
  padding: 4rem 2rem;
  color: var(--text-muted);
  font-size: .95rem;
  line-height: 1.8;
}}

/* ── Count bar ── */
.count-bar {{
  max-width: 1400px;
  margin: 0 auto 1rem;
  padding: 0 1.5rem;
  font-size: .8rem;
  color: var(--text-muted);
}}
#visible-count {{ font-weight: 600; color: var(--text); }}

/* ── Footer ── */
footer {{
  max-width: 1400px;
  margin: 2rem auto 0;
  padding: 1.5rem;
  border-top: 1px solid var(--border);
  font-size: .75rem;
  color: var(--text-muted);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: .5rem;
}}
footer a {{ color: var(--accent); text-decoration: none; }}
footer a:hover {{ text-decoration: underline; }}

/* ── Responsive ── */
@media (max-width: 900px) {{
  .events-grid {{ grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }}
  main {{ padding: 1rem; }}
  .filters-inner {{ padding: .6rem 1rem; }}
  .stats-bar {{ padding: .4rem 1rem; }}
}}
@media (max-width: 600px) {{
  .header-inner {{ padding: .75rem 1rem; }}
  .header-logo h1 {{ font-size: 1rem; }}
  .events-grid {{ grid-template-columns: 1fr; gap: .75rem; }}
  main {{ padding: .75rem; }}
  .filters-inner {{ gap: .4rem; padding: .5rem .75rem; }}
  .filter-btn {{ padding: .3rem .6rem; font-size: .73rem; }}
  .card-img-wrap {{ height: 160px; }}
  .stats-bar {{ display: none; }}
  .filters-wrap {{ top: 64px; }}
}}
</style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="header-logo">
      <span class="city-icon">🏰</span>
      <div>
        <h1><span class="de">Veranstaltungen in Erfurt</span><span class="en">Events in Erfurt</span></h1>
        <div class="subtitle">
          <span class="de">Ihr Veranstaltungskalender für die Landeshauptstadt Thüringens</span>
          <span class="en">Your event calendar for the capital of Thuringia</span>
        </div>
      </div>
    </div>
    <div class="header-controls">
      <button class="btn-ctrl" id="lang-btn" onclick="toggleLang()">EN</button>
      <button class="btn-ctrl" id="theme-btn" onclick="toggleTheme()">☀️</button>
    </div>
  </div>
  <div class="stats-bar">
    <span>📅 <span class="de">Stand</span><span class="en">Updated</span>: {today}</span>
    <span>📋 <span id="stat-total">{total}</span> <span class="de">Veranstaltungen</span><span class="en">Events</span></span>
    <span>🏛️ 5 <span class="de">Quellen</span><span class="en">Sources</span></span>
  </div>
</header>

<div class="filters-wrap">
  <div class="filters-inner">
    <!-- Row 1: Category -->
    <div class="filter-row">
      <span class="filter-label de">Kategorie</span>
      <span class="filter-label en">Category</span>
      {cat_btns}
    </div>
    <!-- Row 2: Date range -->
    <div class="filter-row">
      <span class="filter-label de">Zeitraum</span>
      <span class="filter-label en">Period</span>
      <button class="filter-btn active" data-date="all" onclick="filterDate(this)">
        <span class="de">Alle</span><span class="en">All</span>
      </button>
      <button class="filter-btn" data-date="today" onclick="filterDate(this)">
        <span class="de">Heute</span><span class="en">Today</span>
      </button>
      <button class="filter-btn" data-date="week" onclick="filterDate(this)">
        <span class="de">Diese Woche</span><span class="en">This Week</span>
      </button>
      <button class="filter-btn" data-date="month" onclick="filterDate(this)">
        <span class="de">Diesen Monat</span><span class="en">This Month</span>
      </button>
    </div>
    <!-- Row 3: Organizer type + Source -->
    <div class="filter-row">
      <span class="filter-label de">Träger</span>
      <span class="filter-label en">Organizer</span>
      <button class="filter-btn active" data-type="all" onclick="filterType(this)">
        <span class="de">Alle</span><span class="en">All</span>
      </button>
      <button class="filter-btn" data-type="staatlich" onclick="filterType(this)">
        🏛️ <span class="de">Staatlich</span><span class="en">Government</span>
      </button>
      <button class="filter-btn" data-type="privat" onclick="filterType(this)">
        🏢 <span class="de">Privat</span><span class="en">Private</span>
      </button>
      <div class="filter-divider"></div>
      <span class="filter-label de">Quelle</span>
      <span class="filter-label en">Source</span>
      {src_btns}
    </div>
  </div>
</div>

<main>
  <div class="count-bar">
    <span id="visible-count">{total}</span>
    <span class="de">Veranstaltungen angezeigt</span>
    <span class="en">events shown</span>
  </div>
  <div class="events-grid" id="events-grid">
{cards_html}
  </div>
</main>

<footer>
  <div>
    <span class="de">Quellen:</span><span class="en">Sources:</span>
    <a href="https://www.erfurt.de/veranstaltungen" target="_blank">Stadt Erfurt</a> ·
    <a href="https://www.frauenzentrum-erfurt.de" target="_blank">Frauenzentrum</a> ·
    <a href="https://www.theater-erfurt.de" target="_blank">Theater Erfurt</a> ·
    <a href="https://kunstmuseen.erfurt.de/angermuseum" target="_blank">Anger Museum</a> ·
    <a href="https://www.egapark-erfurt.de" target="_blank">EGA Park</a>
  </div>
  <div><span class="de">Generiert am</span><span class="en">Generated on</span> {today}</div>
</footer>

<script>
// ── Language toggle ──────────────────────────────────────────────────────────
let currentLang = 'de';
function toggleLang() {{
  currentLang = currentLang === 'de' ? 'en' : 'de';
  document.documentElement.lang = currentLang;
  document.getElementById('lang-btn').textContent = currentLang === 'de' ? 'EN' : 'DE';
  localStorage.setItem('erfurt-lang', currentLang);
}}

// ── Theme toggle ─────────────────────────────────────────────────────────────
let isDark = true;
function toggleTheme() {{
  isDark = !isDark;
  document.documentElement.classList.toggle('dark', isDark);
  document.documentElement.classList.toggle('light', !isDark);
  document.getElementById('theme-btn').textContent = isDark ? '☀️' : '🌙';
  localStorage.setItem('erfurt-theme', isDark ? 'dark' : 'light');
}}

// ── Filters ──────────────────────────────────────────────────────────────────
let activeCat  = 'all';
let activeType = 'all';
let activeDate = 'all';
let activeSrc  = 'all';

function filterCat(btn) {{
  document.querySelectorAll('.filter-btn[data-cat]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeCat = btn.dataset.cat;
  applyFilters();
}}

function filterType(btn) {{
  document.querySelectorAll('.filter-btn[data-type]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeType = btn.dataset.type;
  applyFilters();
}}

function filterDate(btn) {{
  document.querySelectorAll('.filter-btn[data-date]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeDate = btn.dataset.date;
  applyFilters();
}}

function filterSrc(btn) {{
  document.querySelectorAll('.filter-btn[data-src]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeSrc = btn.dataset.src;
  applyFilters();
}}

function applyFilters() {{
  const today = new Date(); today.setHours(0,0,0,0);
  const weekEnd = new Date(today); weekEnd.setDate(today.getDate() + 7);
  const monthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);

  let count = 0;
  document.querySelectorAll('.event-card').forEach(card => {{
    const catOk  = activeCat  === 'all' || card.dataset.category === activeCat;
    const typeOk = activeType === 'all' || card.dataset.type     === activeType;
    const srcOk  = activeSrc  === 'all' || card.dataset.source   === activeSrc;

    let dateOk = true;
    if (activeDate !== 'all' && card.dataset.date) {{
      const d = new Date(card.dataset.date); d.setHours(0,0,0,0);
      if (activeDate === 'today') dateOk = d.getTime() === today.getTime();
      else if (activeDate === 'week')  dateOk = d >= today && d <= weekEnd;
      else if (activeDate === 'month') dateOk = d >= today && d <= monthEnd;
    }}

    const show = catOk && typeOk && dateOk && srcOk;
    card.classList.toggle('hidden', !show);
    if (show) count++;
  }});
  document.getElementById('visible-count').textContent = count;
}}

// ── Add to Calendar (.ics download) ──────────────────────────────────────────
function addToCalendar(title, dateStr, location, description, url) {{
  // Parse German date dd.mm.yyyy or dd.mm.yy or dd.mm.
  let dtStart = '', dtEnd = '';
  const m = dateStr.match(/(\\d{{1,2}})\\.(\\s*)(\\d{{1,2}})\\.(\\s*)(\\d{{2,4}})?/);
  if (m) {{
    const day  = m[1].padStart(2, '0');
    const mon  = m[3].padStart(2, '0');
    let yr     = m[5] || new Date().getFullYear().toString();
    if (yr.length === 2) yr = '20' + yr;
    dtStart = yr + mon + day + 'T100000';
    dtEnd   = yr + mon + day + 'T120000';
  }} else {{
    const now = new Date();
    const pad = n => String(n).padStart(2,'0');
    dtStart = now.getFullYear()+pad(now.getMonth()+1)+pad(now.getDate())+'T100000';
    dtEnd   = dtStart.replace('T100000','T120000');
  }}

  // Check for time in dateStr  hh:mm
  const tm = dateStr.match(/(\\d{{1,2}}):(\\d{{2}})/);
  if (tm && m) {{
    const yr  = dtStart.substring(0,4);
    const mon = dtStart.substring(4,6);
    const day = dtStart.substring(6,8);
    const hh  = tm[1].padStart(2,'0');
    const mm  = tm[2];
    dtStart   = yr+mon+day+'T'+hh+mm+'00';
    // end: +2h
    const endH = String(parseInt(hh)+2).padStart(2,'0');
    dtEnd     = yr+mon+day+'T'+endH+mm+'00';
  }}

  const safe = s => (s||'').replace(/[\\n\\r]/g,' ').replace(/;/g,'\\;').replace(/,/g,'\\,');
  const uid  = 'erfurt-' + Date.now() + '@events.erfurt.de';

  const ics = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Erfurt Events//DE',
    'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT',
    'UID:' + uid,
    'DTSTAMP:' + new Date().toISOString().replace(/[-:.]/g,'').slice(0,15) + 'Z',
    'DTSTART:' + dtStart,
    'DTEND:'   + dtEnd,
    'SUMMARY:' + safe(title),
    'LOCATION:' + safe(location),
    'DESCRIPTION:' + safe(description) + (url ? '\\n\\nMehr: ' + url : ''),
    'URL:' + (url||''),
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\\r\\n');

  const blob = new Blob([ics], {{type:'text/calendar;charset=utf-8'}});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = (title.replace(/[^\\w\\s-]/g,'').trim().replace(/\\s+/g,'_').slice(0,40) || 'event') + '.ics';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}}

// ── Init from localStorage ────────────────────────────────────────────────────
(function() {{
  const savedLang  = localStorage.getItem('erfurt-lang');
  const savedTheme = localStorage.getItem('erfurt-theme');
  if (savedLang && savedLang !== 'de') toggleLang();
  if (savedTheme === 'light') toggleTheme();
}})();
</script>
</body>
</html>"""


# ─── PDF Calendar Scraper ─────────────────────────────────────────────────────
def scrape_pdf_calendar(pdf_path=PDF_PATH):
    """Extract events from a Programm PDF (e.g. Programm_Maerz.pdf)."""
    events = []
    print(f"\n-- Reading PDF Calendar: {os.path.basename(pdf_path)} --")

    if not PDF_OK:
        print("  pdfplumber not installed – skipping")
        return events

    if not os.path.exists(pdf_path):
        print(f"  File not found: {pdf_path}")
        return events

    # Derive source name from filename (Programm_Maerz.pdf → Programm März)
    source_name = (os.path.splitext(os.path.basename(pdf_path))[0]
                   .replace("_", " ")
                   .replace("ae", "ä").replace("oe", "ö").replace("ue", "ü")
                   .replace("Ae", "Ä").replace("Oe", "Ö").replace("Ue", "Ü"))

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_lines = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    all_lines.extend(text.splitlines())
    except Exception as e:
        print(f"  PDF read error: {e}")
        return events

    # Group lines into event blocks:
    # A new block starts whenever we hit a line that begins with a date (dd.mm.)
    # or a standalone heading (ALL CAPS or very short bold-looking line after blank)
    DATE_RE = re.compile(r'^\d{1,2}\.\s*\d{1,2}\.(\s*\d{2,4})?')

    blocks = []
    current = []
    for line in all_lines:
        line = line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if DATE_RE.match(line) and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)

    # Fallback: if no blank-line blocks found, treat each date-starting line as a block
    if len(blocks) <= 1:
        blocks = []
        current = []
        for line in all_lines:
            line = line.strip()
            if not line:
                continue
            if DATE_RE.match(line):
                if current:
                    blocks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append(current)

    print(f"  Extracted {len(blocks)} blocks from PDF")

    seen = set()
    for block in blocks[:50]:
        if not block:
            continue

        date_str   = ""
        title      = ""
        desc_lines = []
        location   = ""
        price      = ""

        for line in block:
            dm = DATE_RE.match(line)
            if dm and not date_str:
                date_str = dm.group().strip()
                rest = line[dm.end():].lstrip(" –-:|")
                if rest and not title:
                    title = rest
            elif not title:
                title = line
            else:
                if re.search(r'\d+[,.]?\d*\s*€|kostenlos|frei|eintritt', line, re.I):
                    price = line
                elif re.search(r'\b(uhr|beginn|einlass)\b', line, re.I):
                    date_str = (date_str + " " + line).strip()
                elif re.search(r'\b(haus|saal|theater|museum|park|kirche|platz|straße|str\.)\b',
                                line, re.I):
                    location = line
                else:
                    desc_lines.append(line)

        title = re.sub(r'\s+', ' ', title).strip()
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)

        description = " ".join(desc_lines)[:200]
        if not location:
            location = "Erfurt"

        events.append(make_event(
            source_name, "privat", title, date_str,
            location, price, "", None, description,
            hint=title + " " + description
        ))
        print(f"  Saved: {title[:75]}")

    if not events:
        print("  No events parsed from PDF")
    return events


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Erfurt Events Scraper  –  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Output: {HTML_OUT}")

    all_events = []
    all_events += scrape_erfurt_de()
    all_events += scrape_frauenzentrum()

    # JS-rendered sources share one Selenium driver
    driver = None
    if SELENIUM_OK:
        print("\nStarting Selenium driver for JS-rendered sites...")
        try:
            driver = make_driver()
        except Exception as e:
            print(f"  Could not start driver: {e}")

    all_events += scrape_theater_erfurt(driver)
    all_events += scrape_anger_museum(driver)
    all_events += scrape_egapark(driver)
    all_events += scrape_pdf_calendar()

    if driver:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\nTotal events collected: {len(all_events)}")
    print("Generating HTML...")

    html_content = generate_html(all_events)
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Saved {HTML_OUT}")
    print("Done!")

if __name__ == "__main__":
    main()
