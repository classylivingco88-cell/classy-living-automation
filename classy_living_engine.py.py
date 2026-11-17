import os
import re
import csv
import json
import time
import shutil
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

# ==============================================================================
# CLASSY LIVING LLC - LIVE SITE CRAWLER + PROFIT-FIRST AUTOMATION ENGINE
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
OUTPUT_DIR = os.path.join(BASE_DIR, "warhead_ready_uploads")
LOGS_DIR = os.path.join(BASE_DIR, "tax_and_tracking_logs")
STATE_FILE = os.path.join(BASE_DIR, "system_state.json")

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def safe_float(val, default=0.0):
    try:
        if val is None:
            return default
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else default
    except ValueError:
        return default


def safe_int(val, default=0):
    try:
        if val is None:
            return default
        cleaned = str(val).replace(",", "").strip()
        return int(float(cleaned)) if cleaned else default
    except ValueError:
        return default


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Missing config.json at {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("smtp_server") or "://" in str(cfg.get("smtp_server")):
        cfg["smtp_server"] = "smtp." + "gmail.com"
    if not cfg.get("store_url"):
        cfg["store_url"] = "https://classylivingco.com"
    return cfg


def get_supplier_rules(supplier_name, config):
    defaults = config.get("default_supplier_rules", {
        "enforce_map": True,
        "portal_url": "Check Bookmarks"
    })
    overrides = config.get("supplier_overrides", {})
    for key, rules in overrides.items():
        if key.lower() == str(supplier_name).strip().lower():
            merged = dict(defaults)
            merged.update(rules)
            return merged
    return defaults


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "batch_cursor": 0,
        "crawl_cursor": 0,
        "total_orders_received": 0,
        "orders_with_tracking": 0,
        "processed_order_ids": [],
        "notified_tracking_ids": [],
        "logged_tax_order_ids": []
    }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send_email(config, to_email, subject, body_text, attachment_paths=None):
    attachment_paths = attachment_paths or []
    if config.get("email_app_password") == "YOUR_APP_PASSWORD_HERE":
        print(f"[EMAIL PREVIEW -> {to_email}] {subject}")
        if attachment_paths:
            print(f"  Attachments: {[os.path.basename(p) for p in attachment_paths]}")
        print(f"{body_text}\n{'-' * 50}")
        return True
    try:
        msg = MIMEMultipart()
        msg["From"] = config["business_email"]
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain"))

        for file_path in attachment_paths:
            if os.path.exists(file_path):
                with open(file_path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(file_path)}")
                msg.attach(part)

        with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["business_email"], config["email_app_password"])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Failed sending to {to_email}: {e}")
        return False


def fetch_html(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore"), resp.status
    except Exception:
        return "", 0


def check_url_alive(url):
    if not url or not str(url).startswith("http"):
        return False
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status < 400
    except Exception:
        _, status = fetch_html(url, timeout=6)
        return 0 < status < 400


def fetch_live_buyer_keywords(product_title):
    clean_query = " ".join(str(product_title).split()[:4])
    if not clean_query:
        return ["home furniture", "outdoor patio furniture", "home decor"]
    base_host = "suggestqueries." + "google.com"
    url = f"http://{base_host}/complete/search?client=firefox&q=" + urllib.parse.quote(clean_query)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
            return suggestions[:5] if suggestions else [clean_query, f"buy {clean_query} online", f"best {clean_query}"]
    except Exception:
        return [clean_query, f"buy {clean_query} online", f"best {clean_query}"]


def build_ai_json_ld_schema(title, sku, description, supplier, price_str, stock_status, page_url=""):
    schema_host = "schema" + ".org"
    availability = f"https://{schema_host}/InStock" if stock_status == "InStock" else f"https://{schema_host}/OutOfStock"
    schema = {
        "@context": f"https://{schema_host}/",
        "@type": "Product",
        "name": title,
        "sku": sku,
        "url": page_url,
        "description": description,
        "brand": {"@type": "Brand", "name": supplier or "Classy Living"},
        "offers": {
            "@type": "Offer",
            "priceCurrency": "USD",
            "price": price_str,
            "availability": availability
        }
    }
    return schema


# ==============================================================================
# LIVE WEBSITE CRAWLER + GOOGLE TAG MANAGER AUTO-PATCH (NO CSV NEEDED TO SCAN)
# ==============================================================================
def crawl_live_storefront_and_generate_patch(config, state, exceptions):
    store_url = config.get("store_url", "https://classylivingco.com").rstrip("/")
    print(f"[CRAWLER] Scanning live storefront: {store_url}")

    candidate_urls = set()

    # 1. Try sitemap.xml first
    sitemap_xml, status = fetch_html(f"{store_url}/sitemap.xml")
    if status == 200 and "<loc>" in sitemap_xml:
        locs = re.findall(r"<loc>(.*?)</loc>", sitemap_xml, re.IGNORECASE)
        for loc in locs:
            loc = loc.strip()
            if store_url in loc and not loc.endswith(".xml"):
                candidate_urls.add(loc)

    # 2. Also scan homepage links if sitemap is small or missing
    home_html, home_status = fetch_html(store_url)
    if home_status == 200 and home_html:
        hrefs = re.findall(r'href=["\'](.*?)["\']', home_html, re.IGNORECASE)
        for h in hrefs:
            full = urllib.parse.urljoin(store_url + "/", h.split("#")[0])
            if full.startswith(store_url) and len(full) > len(store_url) + 1:
                candidate_urls.add(full)
    elif home_status == 0:
        exceptions.append(f"SITE REACHABILITY ALERT: Could not reach {store_url}. Check internet connection or site status.")
        return

    all_pages = sorted(list(candidate_urls))
    if not all_pages:
        print("[CRAWLER] No internal product/category URLs found on homepage yet.")
        return

    batch_size = config.get("daily_batch_size", 25)
    start = state.get("crawl_cursor", 0) % len(all_pages)
    batch_urls = [all_pages[(start + i) % len(all_pages)] for i in range(min(batch_size, len(all_pages)))]
    state["crawl_cursor"] = (start + batch_size) % len(all_pages)

    gtm_patches = {}

    for page_url in batch_urls:
        html, p_status = fetch_html(page_url)
        if p_status >= 400 or p_status == 0:
            exceptions.append(f"BROKEN PAGE LINK (HTTP {p_status}): {page_url}")
            continue

        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        raw_title = re.sub(r"<.*?>", "", title_match.group(1)).strip() if title_match else page_url.split("/")[-1]
        clean_title = raw_title.split("|")[0].split("-")[0].strip()

        # Detect out-of-stock indicators on live page
        lower_html = html.lower()
        is_oos = any(phrase in lower_html for phrase in ("out of stock", "sold out", "currently unavailable"))
        stock_status = "OutOfStock" if is_oos else "InStock"

        # Extract price if displayed on page
        price_match = re.search(r"\$([0-9,]+\.[0-9]{2})", html)
        detected_price = price_match.group(1).replace(",", "") if price_match else "0.00"

        # Check product images on page for broken links
        img_srcs = re.findall(r'<img[^>]+src=["\'](.*?)["\']', html, re.IGNORECASE)
        broken_imgs = []
        for src in img_srcs[:8]:
            full_img = urllib.parse.urljoin(page_url, src)
            if full_img.startswith("http") and not check_url_alive(full_img):
                broken_imgs.append(full_img)

        if broken_imgs:
            exceptions.append(f"BROKEN IMAGE ON LIVE PAGE ({page_url}): {broken_imgs}")

        keywords = fetch_live_buyer_keywords(clean_title)
        faq_text = (
            f"Shopper Q&A for {clean_title}: Popular searches include {', '.join(keywords[:3])}. "
            f"Currently listed as {'Out of Stock' if is_oos else 'In Stock'} at Classy Living."
        )
        schema_obj = build_ai_json_ld_schema(
            title=clean_title,
            sku=page_url.rstrip("/").split("/")[-1],
            description=faq_text,
            supplier="Classy Living",
            price_str=detected_price,
            stock_status=stock_status,
            page_url=page_url
        )

        path_key = urllib.parse.urlparse(page_url).path or "/"
        gtm_patches[path_key] = {
            "title": clean_title,
            "stock_status": stock_status,
            "keywords": keywords[:4],
            "faq_html": faq_text,
            "json_ld": schema_obj,
            "updated": datetime.now().strftime("%Y-%m-%d")
        }

    # Write self-contained Google Tag Manager auto-patch script
    patch_js_path = os.path.join(OUTPUT_DIR, "ai_seo_patch.js")
    js_payload = f"""// Auto-generated by Classy Living Engine on {datetime.now().strftime('%Y-%m-%d %H:%M')}
(function() {{
  var patches = {json.dumps(gtm_patches, indent=2)};
  var data = patches[window.location.pathname];
  if (!data) return;

  // 1. Inject AI-Readable JSON-LD Product Schema
  var script = document.createElement('script');
  script.type = 'application/ld+json';
  script.text = JSON.stringify(data.json_ld);
  document.head.appendChild(script);

  // 2. Append Visible Shopper Q&A Block if not already present
  if (!document.getElementById('cl-ai-shopper-qa')) {{
    var container = document.querySelector('.product-description, #product-description, main, article') || document.body;
    var qaBox = document.createElement('div');
    qaBox.id = 'cl-ai-shopper-qa';
    qaBox.style.cssText = 'margin-top:16px;padding:12px;border-top:1px solid #e2e8f0;font-size:14px;color:#333;';
    qaBox.textContent = data.faq_html;
    container.appendChild(qaBox);
  }}
}})();
"""
    with open(patch_js_path, "w", encoding="utf-8") as jf:
        jf.write(js_payload)
    print(f"[CRAWLER SUCCESS] Scanned {len(batch_urls)} live pages and updated: {patch_js_path}")


# ==============================================================================
# ZERO-LOSS PROFIT & MAP AUDIT (WHEN WARHEAD PRODUCT CSV IS PRESENT)
# ==============================================================================
def create_backup_snapshot(catalog_csv_path):
    if not os.path.exists(catalog_csv_path):
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"catalog_snapshot_{timestamp}.csv")
    shutil.copy2(catalog_csv_path, backup_path)
    return backup_path


def calculate_safe_retail_price(product, config, exceptions):
    sku = str(product.get("SKU", "UNKNOWN")).strip()
    supplier = str(product.get("Supplier", "General Supplier")).strip()
    existing_retail = safe_float(product.get("Retail_Price"), 0.0)
    wholesale = safe_float(product.get("Wholesale_Cost"), 0.0)
    est_shipping = safe_float(product.get("Supplier_Shipping"), 0.0)
    map_price = safe_float(product.get("MAP_Price"), 0.0)

    if existing_retail <= 0:
        exceptions.append(f"MISSING RETAIL PRICE [{sku} - {product.get('Title')}]: Retail_Price is $0 or blank.")
        return None, 0.0, "NO_DISCOUNT_ALLOWED"

    sup_rules = get_supplier_rules(supplier, config)
    freight_buffer = safe_float(product.get("Freight_Buffer"), config["default_freight_buffer_usd"])
    total_landed_cost = wholesale + est_shipping + freight_buffer
    target_price = existing_retail

    if sup_rules.get("enforce_map", True) and map_price > 0 and target_price < map_price:
        exceptions.append(
            f"MAP ALERT [{sku} - {product.get('Title')}]: Existing price (${target_price:.2f}) was below MAP (${map_price:.2f}). Adjusted to MAP."
        )
        target_price = map_price

    if wholesale > 0:
        processor_fee = (target_price * config["payment_fee_percent"]) + config["payment_fee_fixed"]
        net_profit = round(target_price - total_landed_cost - processor_fee, 2)
        if net_profit < config["minimum_net_profit_usd"]:
            exceptions.append(
                f"PROFIT LOCK [{sku} - {product.get('Title')}]: Net profit (${net_profit:.2f}) at ${target_price:.2f} "
                f"is below your ${config['minimum_net_profit_usd']:.2f} minimum floor. Listing frozen."
            )
            return None, net_profit, "NO_DISCOUNT_ALLOWED"
    else:
        net_profit = 0.0

    discounted_price = target_price * (1.0 - config.get("promo_discount_buffer_percent", 0.10))
    below_map_if_discounted = sup_rules.get("enforce_map", True) and map_price > 0 and discounted_price < map_price
    below_profit_if_discounted = False
    if wholesale > 0:
        discounted_fee = (discounted_price * config["payment_fee_percent"]) + config["payment_fee_fixed"]
        below_profit_if_discounted = (discounted_price - total_landed_cost - discounted_fee) < config["minimum_net_profit_usd"]

    if below_map_if_discounted or below_profit_if_discounted:
        coupon_eligibility = "EXCLUDED_FROM_COUPONS_MAP_LOCKED"
    else:
        coupon_eligibility = "COUPON_ELIGIBLE"

    return target_price, net_profit, coupon_eligibility


def sync_desktop_images_by_sku(config, supplier, sku):
    safe_supplier_folder = "".join(c if c.isalnum() or c in (" ", "_", "-") else "_" for c in supplier).strip() or "General"
    desktop_root = os.path.expanduser(f"~/Desktop/Supplier_Images_Sorted/{safe_supplier_folder}")
    os.makedirs(desktop_root, exist_ok=True)
    source_folder = os.path.expanduser(f"~/{config.get('dropbox_image_sync_folder', 'Desktop/Anderson_Teak_Images')}")

    matched_files = []
    if os.path.exists(source_folder):
        for root, _, files in os.walk(source_folder):
            for file in files:
                if sku.lower() in file.lower() and file.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    sku_folder = os.path.join(desktop_root, sku)
                    os.makedirs(sku_folder, exist_ok=True)
                    dest_file = os.path.join(sku_folder, file)
                    if not os.path.exists(dest_file):
                        shutil.copy2(os.path.join(root, file), dest_file)
                    matched_files.append(dest_file)
    return matched_files


def upsert_csv_log(log_path, fieldnames, row_dict):
    existing_rows = []
    updated = False
    if os.path.exists(log_path):
        with open(log_path, "r", newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
        for r in existing_rows:
            if r.get("Order_ID") == row_dict["Order_ID"] and r.get("SKU") == row_dict["SKU"]:
                r.update(row_dict)
                updated = True
                break
    if not updated:
        existing_rows.append(row_dict)

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)


def process_orders_tracking_and_tax_logs(orders_csv_path, config, state):
    if not os.path.exists(orders_csv_path):
        return

    with open(orders_csv_path, "r", encoding="utf-8") as f:
        orders = list(csv.DictReader(f))

    mn_tax_log_path = os.path.join(LOGS_DIR, f"MN_Sales_Tax_Log_{datetime.now().year}.csv")
    master_tracking_log_path = os.path.join(LOGS_DIR, f"Master_Order_Tracking_Log_{datetime.now().year}.csv")
    log_columns = [
        "Date", "Order_ID", "Customer_Name", "Shipping_State", "Supplier",
        "SKU", "Quantity", "Order_Subtotal_USD", "Tax_Collected_USD",
        "Chosen_Carrier", "Tracking_Number"
    ]

    send_updated_logs = False

    for order in orders:
        order_id = str(order.get("Order_ID", "")).strip()
        sku = str(order.get("SKU", "")).strip()
        if not order_id:
            continue

        order_key = f"{order_id}_{sku}"
        tracking_num = str(order.get("Tracking_Number", "")).strip()
        carrier = str(order.get("Chosen_Carrier", "Standard")).strip()

        if order_key not in state["processed_order_ids"]:
            state["total_orders_received"] += 1
            supplier = str(order.get("Supplier", "General Supplier")).strip()
            portal = get_supplier_rules(supplier, config).get("portal_url", "Check Bookmarks")

            prep_body = (
                f"NEW ORDER RECEIVED (#{order_id})\n"
                f"Running Total Items Ordered: {state['total_orders_received']} | "
                f"Items With Tracking: {state['orders_with_tracking']}\n\n"
                f"--- READY-TO-PURCHASE SUPPLIER DETAILS ---\n"
                f"Supplier: {supplier}\n"
                f"Bookmarked Portal: {portal}\n"
                f"Product: {order.get('Product_Title')}\n"
                f"Exact SKU: {sku}\n"
                f"Quantity: {order.get('Quantity', '1')}\n\n"
                f"--- SHIP TO CUSTOMER ---\n"
                f"Name: {order.get('Customer_Name')}\n"
                f"Address: {order.get('Shipping_Address')}\n"
                f"State: {order.get('Shipping_State', '')}\n"
                f"Chosen Carrier/Method: {carrier}\n"
            )
            send_email(config, config["business_email"], f"New Order #{order_id} - Action Needed ({supplier})", prep_body)
            state["processed_order_ids"].append(order_key)
            send_updated_logs = True

        if tracking_num and order_key not in state["notified_tracking_ids"]:
            state["orders_with_tracking"] += 1
            cust_email = str(order.get("Customer_Email", "")).strip()

            if cust_email:
                cust_body = (
                    f"Hi {order.get('Customer_Name')},\n\n"
                    f"Great news! Your order (#{order_id}) for {order.get('Product_Title')} has shipped.\n\n"
                    f"Shipping Carrier: {carrier}\n"
                    f"Tracking Number: {tracking_num}\n\n"
                    f"Thank you for shopping with Classy Living!"
                )
                send_email(config, cust_email, f"Your Classy Living Order #{order_id} Has Shipped!", cust_body)
                state["notified_tracking_ids"].append(order_key)
                send_updated_logs = True
            else:
                send_email(
                    config,
                    config["business_email"],
                    f"Unmatched Tracking Alert - Order #{order_id}",
                    f"Tracking {tracking_num} ({carrier}) arrived for Order #{order_id} (SKU: {sku}), but customer email is missing."
                )

        row_data = {
            "Date": order.get("Order_Date", datetime.now().strftime("%Y-%m-%d")),
            "Order_ID": order_id,
            "Customer_Name": order.get("Customer_Name", ""),
            "Shipping_State": str(order.get("Shipping_State", "")).strip().upper(),
            "Supplier": order.get("Supplier", ""),
            "SKU": sku,
            "Quantity": str(order.get("Quantity", "1")),
            "Order_Subtotal_USD": f"{safe_float(order.get('Order_Subtotal'), 0.0):.2f}",
            "Tax_Collected_USD": f"{safe_float(order.get('Tax_Collected'), 0.0):.2f}",
            "Chosen_Carrier": carrier,
            "Tracking_Number": tracking_num
        }

        upsert_csv_log(master_tracking_log_path, log_columns, row_data)
        if row_data["Shipping_State"] in ("MN", "MINNESOTA"):
            upsert_csv_log(mn_tax_log_path, log_columns, row_data)

    if send_updated_logs:
        attachments = [p for p in (master_tracking_log_path, mn_tax_log_path) if os.path.exists(p)]
        log_summary = (
            f"Updated Classy Living LLC Order Tracking & Minnesota Sales Tax Logs ({datetime.now().strftime('%Y-%m-%d')})\n\n"
            f"Total Items Purchased: {state['total_orders_received']}\n"
            f"Items With Tracking Sent: {state['orders_with_tracking']}\n\n"
            f"Attached are your updated CSV records for:\n"
            f"1. Master Order, Supplier & Tracking Log (for wholesale resale audits)\n"
            f"2. Minnesota Sales & Use Tax Log (for your annual MN Department of Revenue return)"
        )
        send_email(
            config,
            config["business_email"],
            f"Classy Living LLC - Updated Tracking & MN Tax Logs ({datetime.now().strftime('%Y-%m-%d')})",
            log_summary,
            attachment_paths=attachments
        )


def run_csv_catalog_batch(catalog_csv_path, config, state, exceptions):
    if not os.path.exists(catalog_csv_path) or os.path.getsize(catalog_csv_path) == 0:
        return

    create_backup_snapshot(catalog_csv_path)

    with open(catalog_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        base_fieldnames = list(reader.fieldnames or [])
        products = list(reader)

    total = len(products)
    if total == 0:
        return

    extra_cols = ["Status", "Coupon_Eligibility", "Est_Net_Profit", "Stock_Status", "AI_JSON_LD_Schema", "Last_Refreshed"]
    all_fieldnames = base_fieldnames + [c for c in extra_cols if c not in base_fieldnames]

    batch_size = config["daily_batch_size"]
    start = state["batch_cursor"] % total
    rolling_indices = [(start + i) % total for i in range(min(batch_size, total))]
    state["batch_cursor"] = (start + batch_size) % total

    updated_rows = []
    for idx, prod in enumerate(products):
        is_out_of_stock = safe_int(prod.get("Inventory_Qty"), 0) <= 0
        if idx not in rolling_indices and not is_out_of_stock:
            continue

        sku = str(prod.get("SKU", "")).strip()
        supplier = str(prod.get("Supplier", "General Supplier")).strip()
        safe_price, net_profit, coupon_flag = calculate_safe_retail_price(prod, config, exceptions)

        if safe_price is None:
            prod["Status"] = "PAUSED_PROFIT_LOCK"
            prod["Coupon_Eligibility"] = coupon_flag
            prod["Est_Net_Profit"] = f"{net_profit:.2f}"
            prod["Stock_Status"] = "OutOfStock" if is_out_of_stock else "InStock"
            prod["AI_JSON_LD_Schema"] = ""
            prod["Last_Refreshed"] = datetime.now().strftime("%Y-%m-%d")
            updated_rows.append(prod)
            continue

        stock_status = "OutOfStock" if is_out_of_stock else "InStock"
        keywords = fetch_live_buyer_keywords(prod.get("Title", ""))

        if not check_url_alive(prod.get("Image_URL", "")):
            local_imgs = sync_desktop_images_by_sku(config, supplier, sku)
            if not local_imgs:
                exceptions.append(
                    f"MISSING/BROKEN IMAGE [{supplier} - {sku}]: Image URL failed check for '{prod.get('Title')}' "
                    f"and no local match found in Desktop image folder."
                )

        faq_block = f"\n\nCommon Buyer Questions: People searching for {', '.join(keywords[:3])} choose {prod.get('Title')} for lasting durability and style."
        if "Common Buyer Questions:" not in str(prod.get("Description", "")):
            prod["Description"] = str(prod.get("Description", "")) + faq_block

        prod["Status"] = "ACTIVE"
        prod["Retail_Price"] = f"{safe_price:.2f}"
        prod["Est_Net_Profit"] = f"{net_profit:.2f}"
        prod["Coupon_Eligibility"] = coupon_flag
        prod["Stock_Status"] = stock_status
        prod["AI_JSON_LD_Schema"] = json.dumps(build_ai_json_ld_schema(
            prod.get("Title", ""), sku, prod.get("Description", ""), supplier, f"{safe_price:.2f}", stock_status
        ))
        prod["Last_Refreshed"] = datetime.now().strftime("%Y-%m-%d")
        updated_rows.append(prod)

    if updated_rows:
        out_file = os.path.join(OUTPUT_DIR, f"warhead_batch_upload_{datetime.now().strftime('%Y%m%d')}.csv")
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(updated_rows)
        print(f"[CSV BATCH SUCCESS] Generated Warhead upload CSV with {len(updated_rows)} products: {out_file}")


if __name__ == "__main__":
    cfg = load_config()
    st = load_state()
    exceptions_log = []

    catalog_file = os.path.join(BASE_DIR, "warhead_products.csv")
    orders_file = os.path.join(BASE_DIR, "warhead_orders.csv")

    # 1. Always crawl the live website directly (works even with zero CSVs)
    crawl_live_storefront_and_generate_patch(cfg, st, exceptions_log)

    # 2. Also audit CSV catalog & process orders/tracking/MN tax logs if present
    process_orders_tracking_and_tax_logs(orders_file, cfg, st)
    run_csv_catalog_batch(catalog_file, cfg, st, exceptions_log)

    if exceptions_log:
        alert_body = (
            f"Daily Classy Living Automation Exception Report ({datetime.now().strftime('%Y-%m-%d')})\n"
            f"Orders Purchased Total: {st['total_orders_received']} | "
            f"With Tracking: {st['orders_with_tracking']}\n\n"
            f"Items Requiring Your Attention:\n- " + "\n- ".join(exceptions_log)
        )
        send_email(cfg, cfg["business_email"], "Action Needed: Storefront Items Flagged by Automation", alert_body)

    save_state(st)
    print(f"Run complete. Total Items Ordered: {st['total_orders_received']} | Tracked: {st['orders_with_tracking']}")
