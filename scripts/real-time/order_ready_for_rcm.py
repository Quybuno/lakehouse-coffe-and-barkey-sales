
# Rule discount:
#     - A1: total_price >= 400k và có Oolong Milk Tea (T04) => 10%
#     - A2: total_price > 300k, tier diamond, golden hour (17h–20h), TPBank (payment_method_id=12) => 5% (max với A1)

# Recommend (gộp, ưu tiên R1 → R2 → R3):
#     - R1: total_price >= 400k nhưng chưa có T04 → gợi ý T04 để đủ điều kiện A1.
#     - R2: total_price < 300k nhưng đã đủ diamond + golden hour + TPBank → gợi ý thêm món (theo giá)
#       để vượt ngưỡng A2.
#     - R3: gợi ý theo cặp sản phẩm hay cùng xuất hiện trong một đơn (MySQL order_details).


from __future__ import annotations

import logging
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from kafka_get import KafkaHandler
import redis

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

redis_static = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

BOOTSTRAP_SERVERS = ["localhost:29092", "localhost:29093", "localhost:29094"]
TOPIC_RULE_INPUT = "rule_topic"
TOPIC_ACCEPT_RULE = "accept_rule"

# Mô phỏng tỉ lệ khách chấp nhận từng món gợi ý 
ACCEPT_PROB = 0.3

#cho cột orders.status khi thỏa luật discount
ORDER_STATUS_DISCOUNT_OK = "rdisc"

# Oolong Milk Tea product id = T04
OOLONG_MILK_TEA_PRODUCT_ID = "T04"

# TPBank có id=12 
TPBANK_PAYMENT_METHOD_ID = 12

# A1/A2 thresholds
A1_MIN_TOTAL_INCLUSIVE = 400000
A1_DISCOUNT_PERCENT = 10

# Ngưỡng A2: cần total > 300_000
A2_MIN_TOTAL_EXCLUSIVE = 300000
A2_DISCOUNT_PERCENT = 5

MAX_RECOMMENDATIONS = 8
R2_MAX_BRIDGE_ITEMS = 6
R3_PAIR_LIMIT = 4
R3_SEED_MAX = 8

# cặp cố định
FIXED_COPURCHASE_PAIRS: list[tuple[str, str]] = [
    ("CF05", "C03"),
    ("C05", "CF03"),
    ("CF05", "C05"),
    ("CF04", "C01"),
    ("T01", "C02"),
    ("T03", "C04"),
    ("Y01", "C01"),
    ("Y02", "C01"),
    ("Y03", "C01"),
]


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _is_golden_hour(now: datetime | None = None) -> bool:
    # Golden hour: 17h–20h
    now = now or datetime.now()
    return 17 <= now.hour < 20


def _is_diamond_customer(customer_id: Any) -> bool:
    # `diamond_customers` loaded by `load_redis_static.py` as a SET of customer ids
    if customer_id is None:
        return False
    return str(customer_id) in redis_static.smembers("diamond_customers")


def _get_unit_prices(product_ids: Iterable[str]) -> dict[str, int | None]:

    # nhận product_id từ list product_ids và lấy unit_price từ redis_static

    pids = [str(pid) for pid in product_ids]
    pipe = redis_static.pipeline()
    for pid in pids:
        pipe.hget(f"product:{pid}", "unit_price")
    raw_prices = pipe.execute()
    out: dict[str, int | None] = {}
    for pid, raw in zip(pids, raw_prices):
        out[pid] = _safe_int(raw, default=0) if raw is not None else None
    return out


def rule_discount(message: dict[str, Any], now: datetime | None = None) -> int:

    # A1: total_price >= 400k và có T04 => 10%
    # A2: total_price > 300k, diamond, golden hour, TPBank(id=12) => 5%

    total_price = _safe_int(message.get("total_price"))
    product_ids = [str(x) for x in (message.get("product_ids") or [])]
    payment_method_id = _safe_int(message.get("payment_method_id"))
    store_id = _safe_int(message.get("store_id"))
    customer_id = message.get("customer_id")

    a1_ok = total_price >= A1_MIN_TOTAL_INCLUSIVE and (OOLONG_MILK_TEA_PRODUCT_ID in product_ids)
    a2_ok = (
        total_price > A2_MIN_TOTAL_EXCLUSIVE
        and payment_method_id == TPBANK_PAYMENT_METHOD_ID
        and store_id == 1
        and _is_diamond_customer(customer_id)
        and _is_golden_hour(now)
    )

    discount = 0
    if a1_ok:
        discount = max(discount, A1_DISCOUNT_PERCENT)
    if a2_ok:
        discount = max(discount, A2_DISCOUNT_PERCENT)
    return discount
    
    
# rcm theo rule discount
def build_recommendations(message: dict[str, Any], now: datetime | None = None) -> list[str]:

    # total_price >= 400k nhưng chưa có T04 -> gợi ý T04
    # R2: total_price < 300k nhưng đủ điều kiện A2 (trừ total) -> gợi ý thêm món để vượt ngưỡng A2

    now = now or datetime.now()
    total_price = _safe_int(message.get("total_price"))
    product_ids = [str(x) for x in (message.get("product_ids") or [])]
    payment_method_id = _safe_int(message.get("payment_method_id"))
    store_id = _safe_int(message.get("store_id"))
    customer_id = message.get("customer_id")

    recs: list[str] = []

    # R1
    if total_price >= A1_MIN_TOTAL_INCLUSIVE and OOLONG_MILK_TEA_PRODUCT_ID not in product_ids:
        recs.append(OOLONG_MILK_TEA_PRODUCT_ID)

    a2_context_ok = (
        payment_method_id == TPBANK_PAYMENT_METHOD_ID
        and store_id == 1
        and _is_diamond_customer(customer_id)
        and _is_golden_hour(now)
    )
    # 0.8 *  300k <total_price < 300k
    if a2_context_ok and int(A2_MIN_TOTAL_EXCLUSIVE * 0.8) < total_price < A2_MIN_TOTAL_EXCLUSIVE:
        gap = A2_MIN_TOTAL_EXCLUSIVE - total_price + 1  

        candidates: list[str] = []
        for seed in product_ids[:8]:
            try:
                candidates.extend(redis_static.zrevrange(f"copurchase:{seed}", 0, R3_PAIR_LIMIT - 1))
            except Exception:
                continue

        seen = set(product_ids)
        uniq_candidates: list[str] = []
        for pid in candidates:
            pid = str(pid)
            if pid in seen:
                continue
            seen.add(pid)
            uniq_candidates.append(pid)
            if len(uniq_candidates) >= MAX_RECOMMENDATIONS:
                break

        if uniq_candidates:
            prices = _get_unit_prices(uniq_candidates)
            # Choose cheapest-first to bridge 
            bridge = sorted(
                [pid for pid in uniq_candidates if prices.get(pid) not in (None, 0)],
                key=lambda pid: prices[pid] or 10**18,
            )
            running = 0
            # so san pham max dc de xuat cua r2
            for pid in bridge[:R2_MAX_BRIDGE_ITEMS]:
                price = prices.get(pid)
                if not price:
                    continue
                recs.append(pid)
                running += price
                if running >= gap:
                    break

    return recs[:MAX_RECOMMENDATIONS]



def recommend_pair(message: dict[str, Any]) -> list[str]:
    # Nếu giỏ có A thì gợi ý B tránh gợi ý món đã có trong giỏ.

    product_ids = [str(x) for x in (message.get("product_ids") or [])]
    if not product_ids:
        return []

    recs: list[str] = []
    in_cart = set(product_ids)

    # Build adjacency from fixed pairs (both directions)
    adj: dict[str, list[str]] = {}
    for a, b in FIXED_COPURCHASE_PAIRS:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    for seed in product_ids[:R3_SEED_MAX]:
        for pid in adj.get(seed, [])[:R3_PAIR_LIMIT]:
            if pid in in_cart:
                continue
            recs.append(pid)
            in_cart.add(pid)
            if len(recs) >= MAX_RECOMMENDATIONS:
                return recs
    return recs


def _random_accepted(product_ids: list[str]) -> list[str]:
    # Mỗi product_id trong list được chấp nhận độc lập với xác suất ACCEPT_PROB
    return [pid for pid in product_ids if random.random() < ACCEPT_PROB]


def _merge_accepted_ordered(
    rule_order: list[str], accepted_rule: list[str], pair_order: list[str], accepted_pair: list[str]
) -> list[str]:
    # thứ tự rule trước pair sau,không trùng id
    seen: set[str] = set()
    out: list[str] = []
    ar = set(accepted_rule)
    for pid in rule_order:
        if pid in ar and pid not in seen:
            seen.add(pid)
            out.append(pid)
    ap = set(accepted_pair)
    for pid in pair_order:
        if pid in ap and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def handle_rule_message(message_value: dict[str, Any], producer) -> None:
    now = datetime.now()
    order_id = str(message_value.get("order_id"))

    rule_recs = build_recommendations(message_value, now=now)
    pair_recs_all = recommend_pair(message_value)
    rule_set = set(rule_recs)
    pair_exclusive = [p for p in pair_recs_all if p not in rule_set]

    accepted_rule = _random_accepted(rule_recs)
    accepted_pair = _random_accepted(pair_exclusive)
    accepted_merge = _merge_accepted_ordered(
        rule_recs, accepted_rule, pair_exclusive, accepted_pair
    )

    product_ids_before = [str(x) for x in (message_value.get("product_ids") or [])]
    total_price_before = _safe_int(message_value.get("total_price"))

    product_ids_after = list(product_ids_before)
    total_price_after = total_price_before
    if accepted_merge:
        prices = _get_unit_prices(accepted_merge)
        for pid in accepted_merge:
            p = prices.get(pid)
            if p:
                product_ids_after.append(pid)
                total_price_after += p

    updated_message = dict(message_value)
    updated_message["product_ids"] = product_ids_after
    updated_message["total_price"] = total_price_after
    discount_after = rule_discount(updated_message, now=now)

    is_suggestion = bool(accepted_rule or accepted_pair)
    only_pair_accept = bool(accepted_pair) and not bool(accepted_rule)
    #nhánh cặp không cập nhật status, discount thỏa mãn → status
    emit_status = discount_after > 0 and not only_pair_accept

    # Chỉ gửi accept_rule khi accept recommend or thỏa rule discount hoặc cả hai
    accepted_recommend = is_suggestion
    rule_discount_ok = discount_after > 0
    emit_accept_rule = accepted_recommend or rule_discount_ok
    if not emit_accept_rule:
        logger.debug(
            "skip accept_rule: order_id=%s (no accepted recommend and no discount rule)",
            order_id,
        )
        return

    accepted_rule_set = set(accepted_rule)
    suggestion_lines: list[dict[str, Any]] = [
        {
            "product_id": pid,
            "is_suggestion": True,
            "source": "rule" if pid in accepted_rule_set else "pair",
        }
        for pid in accepted_merge
    ]

    event: dict[str, Any] = {
        "order_id": order_id,
        "store_id": message_value.get("store_id"),
        "customer_id": message_value.get("customer_id"),
        "payment_method_id": message_value.get("payment_method_id"),
        "num_product": message_value.get("num_product"),
        "quantity": message_value.get("quantity"),
        "product_ids_before": product_ids_before,
        "product_ids_after": product_ids_after,
        "product_ids": product_ids_after,
        "total_price_before": total_price_before,
        "total_price_after": total_price_after,
        "ts": now.isoformat(),
        "is_suggestion": is_suggestion,
        "discount_percent": discount_after,
    }
    if emit_status:
        event["status"] = ORDER_STATUS_DISCOUNT_OK
    if is_suggestion:
        event["event_type"] = "suggestion_accepted"
        event["accepted_product_ids"] = accepted_merge
        event["suggestion_lines"] = suggestion_lines
        event["unlocked_by_accepted_suggestion"] = bool(accepted_rule)
    print(f"[READY] Order {order_id} is complete. Sending to rule_topic...")
    producer.send(TOPIC_ACCEPT_RULE, event)


def main() -> None:
    kafka_client = KafkaHandler(BOOTSTRAP_SERVERS)
    producer = kafka_client.get_producer()
    consumer = kafka_client.get_consumer(topic=TOPIC_RULE_INPUT, group_id="order_ready_for_rcm")

    logger.info("order_ready_for_rcm started; topic=%s", TOPIC_RULE_INPUT)
    try:
        while True:
            message_pack = consumer.poll(timeout_ms=1000)
            for _, messages in message_pack.items():
                for msg in messages:
                    try:
                        handle_rule_message(msg.value, producer)
                    except Exception as e:
                        logger.error("Failed handling rule message: %s", e)
    finally:
        try:
            producer.flush()
            producer.close()
        except Exception:
            pass
        try:
            consumer.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
