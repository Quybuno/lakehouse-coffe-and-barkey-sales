"""
Demo trực quan:
- Consume topic `rule_topic` (payload từ `Check.check_and_trigger`)
- Tra Redis (`discount_result:{order_id}`, `recommendation_result:{order_id}`)
- In ra discount + danh sách gợi ý (kèm tên sản phẩm nếu tra được)

Chạy sau khi đã bật:
- `consumer_orders.py`
- `consumer_order_details.py`
- `order_ready_for_rcm.py` (đã viết logic ghi Redis kết quả)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from kafka import KafkaConsumer
import redis
import mysql.connector

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.utils import get_mysql_config

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "test.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Cùng bootstrap với docker-compose (Kafka EXTERNAL)
BOOTSTRAP_SERVERS = ["localhost:29092", "localhost:29093", "localhost:29094"]
TOPIC_READY = "rule_topic"

redis_dynamic = redis.Redis(host="localhost", port=6379, db=1, decode_responses=True)
redis_static = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

MYSQL_CONFIG = get_mysql_config()


def _product_name(product_id: str) -> str:
    info = redis_static.hgetall(f"product:{product_id}")
    if info:
        return str(info.get("name", product_id))

    # Fallback MySQL nếu redis_static chưa populate
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT name FROM products WHERE id=%s", (str(product_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return str((row or {}).get("name", product_id))
    except Exception:
        return product_id


def _wait_redis_json(key: str, timeout_s: float = 8.0) -> dict | None:
    import time

    start = time.time()
    while time.time() - start <= timeout_s:
        raw = redis_dynamic.get(key)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return None
        time.sleep(0.25)
    return None


def _fmt_products(product_ids: list) -> str:
    parts: list[str] = []
    for pid in product_ids:
        s_pid = str(pid)
        parts.append(f"{s_pid}({ _product_name(s_pid) })")
    return ", ".join(parts)


def main() -> None:
    consumer = KafkaConsumer(
        TOPIC_READY,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        group_id="test_suggestion_viewer_gui",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    logger.info("Đang lắng nghe %s — Ctrl+C để dừng", TOPIC_READY)
    try:
        for msg in consumer:
            payload = msg.value
            order_id = payload.get("order_id")

            if not order_id:
                continue

            product_ids = payload.get("product_ids") or []
            store_id = payload.get("store_id")
            customer_id = payload.get("customer_id")
            total_price = payload.get("total_price")
            timestamp = payload.get("timestamp")
            payment_method_id = payload.get("payment_method_id")
            tier = payload.get("tier")

            # Kết quả discount/recommendation do `order_ready_for_rcm.py` ghi vào Redis
            discount_key = f"discount_result:{order_id}"
            rec_key = f"recommendation_result:{order_id}"

            discount_result = _wait_redis_json(discount_key)
            recommendation_result = _wait_redis_json(rec_key)

            rec_product_ids: list[str] = []
            if isinstance(recommendation_result, dict):
                rec_product_ids = recommendation_result.get("recommended_product_ids") or []

            print("\n" + "=" * 90)
            print("ĐƠN SẴN SÀNG CHECKING -> DISCOUNT + TOP-3 RECOMMEND")
            print("=" * 90)
            print(f"order_id          : {order_id}")
            print(f"store_id          : {store_id}")
            print(f"customer_id       : {customer_id}")
            print(f"payment_method_id: {payment_method_id}")
            print(f"total_price       : {total_price}")
            print(f"timestamp         : {timestamp}")
            print(f"tier(from payload): {tier}")

            print("-" * 90)
            print(f"products in order : {_fmt_products(product_ids)}")

            print("-" * 90)
            if isinstance(discount_result, dict) and discount_result.get("applied") is True:
                dp = discount_result.get("discount_percent")
                reason = discount_result.get("reason", "")
                print(f"discount applied  : YES ({dp}%)")
                print(f"discount reason   : {reason}")
            else:
                print("discount applied  : NO")

            print("-" * 90)
            if rec_product_ids:
                print("Gợi ý sản phẩm:")
                for i, pid in enumerate(rec_product_ids[:8], start=1):
                    print(f"  {i}. {pid} -> {_product_name(pid)}")
            else:
                print("Gợi ý sản phẩm: (chưa có / điều kiện chưa thỏa)")

            logger.info(
                "GUI order_id=%s discount_applied=%s rec=%s",
                order_id,
                bool(discount_result and discount_result.get("applied")),
                rec_product_ids,
            )
    except KeyboardInterrupt:
        logger.info("Dừng consumer.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
