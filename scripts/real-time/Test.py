"""
Consumer demo: đọc topic order_ready_for_checking và in gợi ý sản phẩm kèm đơn giản.

Chạy sau khi đã bật consumer_orders + consumer_order_details (và có dữ liệu CDC).

    cd scripts/real-time && python Test.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from kafka import KafkaConsumer

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

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
TOPIC_READY = "order_ready_for_checking"

# Demo: map product_id (string) -> gợi ý kèm (chỉnh theo DB products của bạn)
SUGGEST_BY_PRODUCT: dict[str, str] = {
    "1": "Thử thêm: nước suối / trà đổi vị",
    "2": "Đi kèm: bánh mì ngọt",
    "3": "Gợi ý: bánh croissant",
}


def suggest_for_order(product_ids: list) -> list[str]:
    """Gợi ý đơn giản: mỗi product_id có thể có 1 gợi ý cố định; fallback chung."""
    out: list[str] = []
    seen: set[str] = set()
    for pid in product_ids:
        key = str(pid)
        if key in seen:
            continue
        seen.add(key)
        if key in SUGGEST_BY_PRODUCT:
            out.append(SUGGEST_BY_PRODUCT[key])
        else:
            out.append(f"Món #{key}: gợi ý thêm topping / size lớn (demo)")
    return out


def main() -> None:
    consumer = KafkaConsumer(
        TOPIC_READY,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        group_id="test_suggestion_viewer",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    logger.info("Đang lắng nghe %s — Ctrl+C để dừng", TOPIC_READY)
    try:
        for msg in consumer:
            payload = msg.value
            order_id = payload.get("order_id")
            product_ids = payload.get("product_ids") or []
            customer_id = payload.get("customer_id")
            suggestions = suggest_for_order(product_ids)
            print("\n--- Đơn sẵn sàng đề xuất ---")
            print(f"  order_id:      {order_id}")
            print(f"  customer_id:   {customer_id}")
            print(f"  product_ids:   {product_ids}")
            print("  Gợi ý (demo):")
            for line in suggestions:
                print(f"    - {line}")
            logger.info(
                "Đề xuất cho order_id=%s customer=%s products=%s",
                order_id,
                customer_id,
                product_ids,
            )
    except KeyboardInterrupt:
        logger.info("Dừng consumer.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
