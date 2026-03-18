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


def pre_order(products):
    num_orders = random.choices([1,2,3,4], weights= [0.5 , 0.25 , 0.15, 0.1])[0]
    so_do_goi = random.sample(products,num_orders)
    
    order_items = []
    for product in so_do_goi:
        quantity = random.choices([1,2,3,4],weights=[0.65 , 0.2 , 0.1 , 0.05])[0]
        subtotal = product['unit_price'] * quantity
        product_id = product['id']
        order_items.append((product_id,quantity,subtotal))
    
    return order_items


def main():
    with get_conn_cursor() as (conn,cursor):
        product = get_products(cursor)

        while True:
            id = fake.uuid4()
            timestamp = datetime.now()
            store_id = random.randint(1,1000)
            customer_id = random.randint(1,1000200)
            payment_method_id = random.randint(1,12)
            order_items = pre_order(product)
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
                    print(f"created order {id} with {num_product} item(s)")
            except Exception as insert_err:    
                conn.rollback
                print(f"false insert order {id} : {insert_err}")

            time.sleep(0.0001)
            

if __name__ == "__main__":
    main()