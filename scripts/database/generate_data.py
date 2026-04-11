import os
import sys
import random
import time
from pathlib import Path
from dotenv import load_dotenv
from contextlib import contextmanager
import mysql.connector
from mysql.connector import errorcode
from faker import Faker
from datetime import datetime
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from scripts.utils import get_mysql_config

MYSQL_CONFIG = get_mysql_config()

fake = Faker()
@contextmanager
def get_conn_cursor():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor(dictionary=True)

    try:
        yield conn,cursor
    finally:
        cursor.close()
        conn.close()

        
def get_products(cursor):
    cursor.execute("SELECT id, name, unit_price FROM products")
    return cursor.fetchall()


def create_order(cursor, order_id,timestamp,store_id,customer_id,payment_method_id, num_product):
    cursor.execute(
        """
            INSERT INTO orders (id,timestamp,store_id,customer_id,payment_method_id,num_product)
            VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (order_id,timestamp,store_id,customer_id,payment_method_id, num_product)
    )


def build_order_line_items(products):
#  trong 1 order thì có thể gọi nhiều sản phẩm
    if not products:
        return []

    # Số lượt gọi trong 1 đơn 
    num_picks = random.choices(
        [1, 2, 3, 4, 5, 6, 7, 8],
        weights=[0.12, 0.18, 0.2, 0.18, 0.12, 0.1, 0.05, 0.05],
    )[0]

    buckets = {}
    for _ in range(num_picks):
        row = random.choice(products)
        piece_qty = random.choices([1, 2, 3, 4], weights=[0.65, 0.2, 0.1, 0.05])[0]
        pid = row["id"]
        if pid not in buckets:
            buckets[pid] = {"qty": 0, "unit_price": row["unit_price"]}
        buckets[pid]["qty"] += piece_qty

    order_items = []
    for pid, info in buckets.items():
        qty = info["qty"]
        subtotal = info["unit_price"] * qty
        order_items.append((pid, qty, subtotal))

    return order_items


def main():
    with get_conn_cursor() as (conn,cursor):
        products = get_products(cursor)
        if not products:
            print("Không có sản phẩm trong bảng products; hãy load dữ liệu trước.")
            return

        while True:
            id = fake.uuid4()
            timestamp = datetime.now()
            store_id = random.randint(1,1000)
            customer_id = random.randint(1,1000200)
            payment_method_id = random.randint(1,12)
            order_items = build_order_line_items(products)
            if not order_items:
                continue
            # Số dòng order_details 
            num_product = len(order_items)
            try:
                create_order(cursor,id,timestamp,store_id,customer_id,payment_method_id,num_product)
                for product_id, quantity, subtotal in order_items:
                    cursor.execute(
                        """
                        INSERT INTO order_details(order_id,product_id, quantity,subtotal,is_suggestion)
                        VALUES(%s, %s, %s, %s, %s )
                        """,
                        (id,product_id,quantity,subtotal,False)
                    )
                conn.commit()
                total_pieces = sum(q for _, q, _ in order_items)
                print(
                    f"created order {id}: {num_product} line(s), {total_pieces} item(s) total",
                    flush=True,
                )
            except Exception as insert_err:
                conn.rollback()
                print(f"false insert order {id} : {insert_err}", flush=True)

            time.sleep(0.001)
            

if __name__ == "__main__":
    main()