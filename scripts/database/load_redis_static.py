import logging
import sys
from pathlib import Path
from typing import List, Tuple

import mysql.connector
from mysql.connector import errorcode
import redis

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from scripts.utils import get_mysql_config

redis_static = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# Cặp đồng mua cố định: (product_id_a, product_id_b, score).
# Trùng contract ZSET `copurchase:{id}` với `order_ready_for_rcm.py` (hai chiều, ZREVRANGE theo score).
FIXED_COPURCHASE_PAIRS: List[Tuple[str, str, float]] = [
    ("CF05", "C03", 100.0),
    ("C05", "CF03", 100.0),
    ("CF05", "C05", 100.0),
    ("CF04", "C01", 100.0),
    ("T01", "C02", 100.0),
    ("T03", "C04", 100.0),
    ("Y01", "C01", 100.0),
    ("Y02", "C01", 100.0),
    ("Y03", "C01", 100.0),
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

def connect_database(user, password, host, database):
    try:
        conn = mysql.connector.connect(
            user=user,
            password=password,
            host=host,
            database=database,
        )
        print("Connect database successful")
        return conn
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            logger.error("Lỗi username hoặc password MySQL")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            logger.error("Database MySQL không tồn tại")
        else:
            print(err)
        sys.exit(1)


def load_tier(cursor):
    try:
        cursor.execute(
            """
            SELECT id, tier
            FROM customers
            WHERE LOWER(tier) = 'diamond'
            """
        )
        rows = cursor.fetchall()
        for row in rows:
            customer_id = row["id"]
            tier = row["tier"]
            if str(tier).strip().lower() == "diamond":
                redis_static.sadd("diamond_customers", str(customer_id))

            redis_static.hset(f"customer:{customer_id}", mapping={"tier": tier})
        logger.info(f"Loaded {len(rows)} diamond customers into redis_static")
        # print(f"Loaded {len(rows)} diamond/gold customers into redis_static")
    except Exception as e:
        print(f"Error loading tier: {e}")
        sys.exit(1)


def load_payment_method(cursor):
    try:
        cursor.execute(
            """
            SELECT id, method_name, bank
            FROM payment_method
            WHERE id = 12
            """
        )
        rows = cursor.fetchall()
        for row in rows:
            redis_static.hset(
                f"payment_method:{row['id']}",
                mapping={
                    "method_name": row["method_name"],
                    "bank": row["bank"],
                },
            )
        # print(f"Loaded {len(rows)} TPBank payment_method entries into redis_static")
        logger.info(f"Loaded {len(rows)} TPBank payment_method entries into redis_static")
    except Exception as e:
        # print(f"Error loading payment method: {e}")
        logger.error(f"Error loading payment method: {e}")
        sys.exit(1)


def load_product_category(cursor):
    try:
        cursor.execute("SELECT id, name FROM product_category")
        rows = cursor.fetchall()
        for row in rows:
            redis_static.sadd("product_category_ids", row["id"])
        # print(f"Loaded {len(rows)} product categories into redis_static")
        logger.info(f"Loaded {len(rows)} product categories into redis_static")     
    except Exception as e:
        # print(f"Error loading product category: {e}")
        logger.error(f"Error loading product category: {e}")
        sys.exit(1)


def load_product(cursor):
    try:
        cursor.execute("SELECT id, name, category_id, unit_price FROM products")
        rows = cursor.fetchall()
        for row in rows:
            redis_static.hset(
                f"product:{row['id']}",
                mapping={
                    "name": row["name"],
                    "category_id": row["category_id"],
                    "unit_price": row["unit_price"],
                },
            )
        logger.info(f"Loaded {len(rows)} products into redis_static")
        # print(f"Loaded {len(rows)} products into redis_static")       
    except Exception as e:
        # print(f"Error loading product: {e}")
        logger.error(f"Error loading product: {e}")
        sys.exit(1)


def _clear_copurchase_keys() -> None:
    keys = list(redis_static.scan_iter(match="copurchase:*", count=500))
    if keys:
        redis_static.delete(*keys)
        logger.info("Removed %s existing copurchase:* keys", len(keys))


def load_copurchase_pairs() -> None:
    # load các cặp đơn hành đi cùng với nhau 

    try:
        if not FIXED_COPURCHASE_PAIRS:
            logger.info("FIXED_COPURCHASE_PAIRS empty; skip copurchase ZSETs")
            return

        _clear_copurchase_keys()
        pipe = redis_static.pipeline()
        n = 0
        for p1, p2, score in FIXED_COPURCHASE_PAIRS:
            a, b = str(p1).strip(), str(p2).strip()
            if not a or not b or a == b:
                continue
            s = float(score)
            pipe.zadd(f"copurchase:{a}", {b: s})
            pipe.zadd(f"copurchase:{b}", {a: s})
            n += 1
        pipe.execute()
        logger.info(
            "Loaded %s fixed copurchase pair rows (bidirectional ZSET entries) into redis_static",
            n,
        )
    except Exception as e:
        logger.error("Error loading fixed copurchase pairs: %s", e)
        sys.exit(1)

def main():
    mysql_conf = get_mysql_config()
    conn = None
    cursor = None
    try:
        conn = connect_database(**mysql_conf)
        cursor = conn.cursor(dictionary=True)

        load_tier(cursor)
        load_payment_method(cursor)
        load_product_category(cursor)
        load_product(cursor)
        load_copurchase_pairs()

        conn.commit()
        logger.info("Redis static loaded successfully")
    except Exception as e:
        logger.error(f"false load redis static: {e}")
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()