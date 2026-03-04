import pandas as pd
from pathlib import Path
import sys

import mysql.connector
from mysql.connector import errorcode


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from scripts.utils import get_mysql_config

def connect_database(user,password,host,database):
    try:
        conn = mysql.connector.connect( 
            user = user, 
            password = password,
            host = host,
            database = database,
            allow_local_infile=True
        ) 
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

def load_file_data(cursor, table_name,csv_path):
    csv_path_str = csv_path.replace("\\","/")

    load_file_query = f"""
    LOAD DATA LOCAL INFILE '{csv_path_str}'
    INTO TABLE `{table_name}`
    FIELDS TERMINATED BY ','
    ENCLOSED BY '"'
    IGNORE 1 ROWS
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
    mysql = get_mysql_config()

    conn = connect_database(**mysql)
    cursor = conn.cursor()
    table_and_file = {
        'store' : str(BASE_DIR / 'data' / 'store.csv'),
        'payment_method' : str(BASE_DIR / 'data' / 'payment_method.csv'),  
        'product_category' : str(BASE_DIR / 'data' / 'product_category.csv'),
        'products' : str(BASE_DIR / 'data' / 'products.csv'),
        'customers' : str(BASE_DIR / 'data' / 'customers.csv'),
    }

    for table , csv_path in table_and_file.items():
        load_file_data(cursor,table,csv_path)
        print(f"load ok table {table}")
    
    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
