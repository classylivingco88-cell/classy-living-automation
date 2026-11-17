import os
import csv
import json
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "system_state.json")
ORDERS_FILE = os.path.join(BASE_DIR, "warhead_orders.csv")
ENGINE_SCRIPT = os.path.join(BASE_DIR, "classy_living_engine.py")


class ExtensionBridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        if self.path == "/status":
            state = {"total_orders_received": 0, "orders_with_tracking": 0}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)

            pending_orders = []
            if os.path.exists(ORDERS_FILE):
                with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if not str(row.get("Tracking_Number", "")).strip():
                            pending_orders.append({
                                "order_id": row.get("Order_ID", ""),
                                "supplier": row.get("Supplier", ""),
                                "sku": row.get("SKU", ""),
                                "product": row.get("Product_Title", ""),
                                "qty": row.get("Quantity", "1"),
                                "customer": row.get("Customer_Name", ""),
                                "address": row.get("Shipping_Address", "")
                            })

            self._send_json({
                "total_orders": state.get("total_orders_received", 0),
                "tracked_orders": state.get("orders_with_tracking", 0),
                "pending_orders": pending_orders
            })

        elif self.path == "/run-now":
            subprocess.run(["python", ENGINE_SCRIPT], check=False)
            self._send_json({"status": "completed"})

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8765), ExtensionBridgeHandler)
    print("Classy Living Chrome Bridge running on http://127.0.0.1:8765")
    server.serve_forever()
