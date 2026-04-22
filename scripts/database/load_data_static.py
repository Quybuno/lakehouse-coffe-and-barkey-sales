import pandas as pd
from pathlib import Path
import sys

import mysql.connector
from mysql.connector import errorcode


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from scripts.utils import get_mysql_config

def connect_database(user, password, host, database, port=None):
    try:
        kwargs = dict(
            user=user,
            password=password,
            host=host,
            database=database,
            allow_local_infile=True,
        )
        if port is not None:
            kwargs["port"] = int(port)
        conn = mysql.connector.connect(**kwargs) 
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Loi pass or name")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Khong ton tai DB")
        else:
            print(err)
        sys.exit(1)
    else:
        print("Connect database successful")
        return conn

# Mapping giữa các cột CSV (theo thứ tự trong file) và các cột của bảng MySQL.
# Phải khai báo tường minh để LOAD DATA không phụ thuộc vào thứ tự hay số cột
# mặc định của bảng, đồng thời cho phép re-load ghi đè dữ liệu cũ.
TABLE_LOADERS = {
    "store": {
        "csv": "store.csv",
        "columns": ["id", "name", "address", "district", "city"],
    },
    "payment_method": {
        "csv": "payment_method.csv",
        "columns": ["id", "method_name", "bank"],
    },
    "product_category": {
        "csv": "product_category.csv",
        "columns": ["id", "name"],
    },
    "products": {
        "csv": "products.csv",
        "columns": ["id", "name", "category_id", "unit_price"],
    },
    "customers": {
        "csv": "customers.csv",
        "columns": ["id", "name", "phone_number", "tier", "@updated_at_csv"],
    },
}


def load_file_data(cursor, table_name, csv_path, columns):
    csv_path_str = csv_path.replace("\\", "/")
    col_list = ", ".join(columns)

    load_file_query = f"""
    LOAD DATA LOCAL INFILE '{csv_path_str}'
    REPLACE
    INTO TABLE `{table_name}`
    FIELDS TERMINATED BY ',' ENCLOSED BY '"'
    LINES TERMINATED BY '\\n'
    IGNORE 1 ROWS
    ({col_list})
    SET updated_at = CURRENT_TIMESTAMP;
    """

    try:
        cursor.execute(load_file_query)
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            print("Loi pass or username")
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            print("Khong ton tai DB")
        else:
            print(err)
            sys.exit(1)


def main():
    mysql_cfg = get_mysql_config()

    conn = connect_database(**mysql_cfg)
    cursor = conn.cursor()
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")

    try:
        for table, cfg in TABLE_LOADERS.items():
            csv_path = str(BASE_DIR / "data" / cfg["csv"])
            load_file_data(cursor, table, csv_path, cfg["columns"])
            print(f"load ok table {table}")
        conn.commit()
    finally:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()