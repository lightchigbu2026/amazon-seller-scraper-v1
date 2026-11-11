"""
Amazon seller lead finder -> Google Sheets
Columns: Company Name | Business Name | Market Place | Phone | Email | Brands | Seller ID
"""
import os
import re
import json
import time
import random
from urllib.parse import quote

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# ---------------- CONFIG ----------------
SHEET_ID = "1m-dUhuYVWbmIppJM_lyg_XN1_4ozyHxLRwxFjel1IzU"
DOMAIN = "https://www.amazon.de"
MARKET_LABEL = "Amazon.de"

BRANDS = [
    "Samsung", "Nintendo", "JBL", "Pokemon", "HyperX", "Sennheiser", "Gillette",
    "SanDisk", "TP-Link", "Razer", "Canon", "PlayStation", "Xiaomi", "Brother",
    "Philips", "Logitech", "Sony", "Garmin", "DJI", "Oral-B", "Kodak",
    "DualSense", "AMD Ryzen", "Elgato Stream", "Remington",
]
PRODUCTS_PER_BRAND = 8          # products checked per brand
DELAY = (4, 9)                  # seconds between page loads (be gentle)
HEADERS = ["Company Name", "Business Name", "Market Place", "Phone", "Email", "Brands", "Seller ID"]
# ----------------------------------------


class CaptchaHit(Exception):
    pass


def pause():
    time.sleep(random.uniform(*DELAY))


def get_sheet():
    raw = os.environ.get("GOOGLE_CREDS_JSON")
    info = json.loads(raw) if raw else json.load(open("service_account.json"))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    ws = gspread.authorize(creds).open_by_key(SHEET_ID).sheet1
    if ws.row_values(1) != HEADERS:
        ws.update(range_name="A1:G1", values=[HEADERS])
    return ws


def check_captcha(page):
    html = page.content().lower()
    if "captcha" in page.url.lower() or "enter the characters you see" in html \
            or "geben sie die zeichen" in html:
        raise CaptchaHit("Amazon showed a CAPTCHA - stopping this run.")


def get_asins(page, brand):
    page.goto(f"{DOMAIN}/s?k={quote(brand)}", wait_until="domcontentloaded")
    check_captcha(page)
    asins = page.eval_on_selector_all(
        "div[data-asin]", "els => els.map(e => e.getAttribute('data-asin')).filter(a => a)"
    )
    return list(dict.fromkeys(asins))[:PRODUCTS_PER_BRAND]


def get_sellers(page, asin):
    """Returns {seller_id: seller_name} for everyone offering this ASIN."""
    page.goto(
        f"{DOMAIN}/gp/product/ajax/?asin={asin}&experienceId=aodAjaxMain&pc=dp",
        wait_until="domcontentloaded",
    )
    check_captcha(page)
    sellers = {}
    for a in page.query_selector_all('a[href*="seller="]'):
        href = a.get_attribute("href") or ""
        m = re.search(r"seller=([A-Z0-9]{10,16})", href)
        if m:
            sellers[m.group(1)] = (a.inner_text() or "").strip()
    return sellers


def grab(pattern, text):
    m = re.search(pattern, text, re.I)
    return m.group(1).strip() if m else ""


def get_profile(page, seller_id, fallback_name):
    page.goto(f"{DOMAIN}/sp?seller={seller_id}", wait_until="domcontentloaded")
    check_captcha(page)
    text = page.inner_text("body")
    try:
        name = page.inner_text("#seller-name").strip() or fallback_name
    except Exception:
        name = fallback_name
    return {
        "company": name,
        "business": grab(r"(?:Geschäftsname|Business Name)\s*:?\s*([^\n]+)", text),
        "phone": grab(r"(?:Telefonnummer|Phone number)\s*:?\s*([^\n]+)", text),
        "email": grab(r"(?:E-Mail|Email)\s*:?\s*([^\n@\s]+@[^\n\s]+)", text),
    }


def main():
    ws = get_sheet()
    rows = ws.get_all_values()[1:]
    existing = {r[6]: (i + 2, r) for i, r in enumerate(rows) if len(r) > 6 and r[6]}
    profiles_cache = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="de-DE")
        page = ctx.new_page()
        try:
            for brand in BRANDS:
                print(f"== {brand}")
                for asin in get_asins(page, brand):
                    pause()
                    for sid, sname in get_sellers(page, asin).items():
                        if sid in existing:
                            row_no, r = existing[sid]
                            brands = [b.strip() for b in r[5].split(",") if b.strip()]
                            if brand not in brands:
                                brands.append(brand)
                                ws.update_cell(row_no, 6, ", ".join(brands))
                                r[5] = ", ".join(brands)
                            continue
                        pause()
                        prof = profiles_cache.get(sid) or get_profile(page, sid, sname)
                        profiles_cache[sid] = prof
                        new_row = [prof["company"], prof["business"], MARKET_LABEL,
                                   prof["phone"], prof["email"], brand, sid]
                        ws.append_row(new_row, value_input_option="USER_ENTERED")
                        existing[sid] = (len(existing) + 2, new_row)
                        print("  + added", prof["company"])
                    pause()
        except CaptchaHit as e:
            print(e)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
