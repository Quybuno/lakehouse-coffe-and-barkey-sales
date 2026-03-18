import os
import sys
from pathlib import Path
from dotenv import load_dotenv

import mysql.connector
from mysql.connector import errorcode

BASE_DIR = Path(__file__).resolve().parent.parent.parent

TABLE = {}

TABLE["store"] = (
    "CREATE TABLE `store` ("
        "`id` int PRIMARY KEY,"
        "`name` varchar(20) NOT NULL,"
        "`address` varchar(50) NOT NULL,"
        "`district` varchar(50) NOT NULL,"
        "`city` varchar(50) NOT NULL,"
        "`updated_at` DATETIME"
    ") ENGINE = InnoDB"
)

TABLE["payment_method"] = (
    "CREATE TABLE `payment_method` ("
        "`id` int PRIMARY KEY,"
        "`method_name` varchar(20) NOT NULL,"
        "`bank` varchar(20) NOT NULL,"
        "`updated_at` DATETIME"
    ") ENGINE = InnoDB"
)

TABLE["customers"] = (
    "CREATE TABLE `customers` ("
        "`id` int PRIMARY KEY,"
        "`name` varchar(20) NOT NULL,"
        "`phone_number` varchar(15) NOT NULL,"
        "`updated_at` DATETIME"
    ") ENGINE = InnoDB"
)

TABLE["product_category"] = (
    "CREATE TABLE `product_category` ("
        "`id` int PRIMARY KEY,"
        "`name` varchar(20) NOT NULL,"
        "`updated_at` DATETIME"
    ") ENGINE = InnoDB"
)

TABLE["products"] = (
    "CREATE TABLE `products` ("
        "`id` varchar(250) PRIMARY KEY,"
        "`name` varchar(20) NOT NULL,"
        "`category_id` int NOT NULL,"
        "`unit_price` int NOT NULL," 
        "`updated_at` DATETIME,"
        "FOREIGN KEY (category_id) REFERENCES product_category(id)"
    ") ENGINE = InnoDB"
)

TABLE["orders"] = (
    "CREATE TABLE `orders` ("
        "`id` varchar(250) PRIMARY KEY,"
        "`timestamp` datetime NOT NULL,"
        "`store_id` int NOT NULL,"
        "`customer_id` int NOT NULL,"
        "`payment_method_id` int NOT NULL,"
        "`num_product` int NOT NULL,"
        "`status` char(5),"
        "FOREIGN KEY (store_id) REFERENCES store(id),"
        "FOREIGN KEY (customer_id) REFERENCES customers(id),"
        "FOREIGN KEY (payment_method_id) REFERENCES payment_method(id)"
    ") ENGINE = InnoDB"
)

TABLE['order_details'] = (
    "CREATE TABLE `order_details` (" \
    "   `order_id` varchar(250) NOT NULL," \
    "   `product_id` varchar(250) NOT NULL," \
    "   `quantity` int NOT NULL," \
    "   `discount_percent` int NOT NULL DEFAULT 0," \
    "   `subtotal` int NOT NULL," \
    "   `is_suggestion` BOOLEAN NOT NULL DEFAULT FALSE," \
    "   CONSTRAINT `order_details_fk_1` FOREIGN KEY (`order_id`)" \
    "       REFERENCES `orders` (`id`) ON DELETE CASCADE," \
    "   CONSTRAINT `order_details_fk_2` FOREIGN KEY (`product_id`)" \
    "       REFERENCES `products` (`id`) ON DELETE CASCADE" \
    ") ENGINE=InnoDB"
)

def connect_database(user,password,host,database):
    try:
        conn = mysql.connector.connect(
            user=user,
            password=password,
            host=host,
            database=database
        )
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Loi username or password")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Khon ton tai DB")
        else:
            print(err)
        sys.exit(1)
    else:
        print("Connect successful")
        return conn

def create_table(cursor):
    for table_name in TABLE:
        table = TABLE[table_name]
        try:
            print(f"Creating table {table_name}: ", end='')
            cursor.execute(table)
        except mysql.connector.Error as err:
            if err.errno == errorcode.ER_TABLE_EXISTS_ERROR:
                print("table already exists")
            else:
                print(err.msg)
        else: 
            print("OK")

if __name__ == "__main__":
    dotenv_path = BASE_DIR / '.env'
    load_dotenv(dotenv_path)

    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    host = os.getenv("MYSQL_HOST")
    database = os.getenv("MYSQL_DATABASE")
    
    conn = connect_database(user=user,password=password, host=host, database= database)
    cursor = conn.cursor()

    try:
        create_table(cursor)
    finally:
        cursor.close()
        conn.close()