import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.functions import *

LOG_FILE = BASE_DIR / "logs" / "batch.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename=LOG_FILE,
)
logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")


mapping_unit_price = {
    "C01": 40000,
    "C02": 45000,
    "C03": 55000,
    "C04": 50000,
    "C05": 50000,
    "CF01": 30000,
    "CF02": 35000,
    "CF03": 40000,
    "CF04": 45000,
    "CF05": 50000,
    "CF06": 55000,
    "CF07": 55000,
    "J01": 45000,
    "J02": 45000,
    "J03": 45000,
    "J04": 45000,
    "T01": 45000,
    "T02": 45000,
    "T03": 50000,
    "T04": 45000,
    "Y01": 40000,
    "Y02": 45000,
    "Y03": 45000,
    "Y04": 45000,
}


def create_spark_session():
    return SparkSession.builder \
        .appName("Silver Layer") \
        .config("spark.hadoop.fs.s3a.endpoint", os.getenv("MINIO_ENDPOINT", "minio:9000")) \
        .config("spark.hadoop.fs.s3a.access.key", os.getenv("MINIO_ROOT_USER")) \
        .config("spark.hadoop.fs.s3a.secret.key", os.getenv("MINIO_ROOT_PASSWORD")) \
        .config("spark.hadoop.fs.s3a.path.style.access", "true") \
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse")) \
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir")) \
        .getOrCreate()

def read_bronze_tables(spark: SparkSession, table_name: str):
    return spark.read.parquet(f"s3a://{BUCKET}/{table_name}")

def write_silver_tables_dim(spark,silver_path: str, table_name: str):
    # các bảng dim gần như clean rồi chỉ chuyển lên lớp trên
    try:
        logger.info(f"[Silver Layer]{table_name} : start_processing")
        source_data = read_bronze_tables(spark, table_name)

        if check_minio_has_data(spark, BUCKET, f"{table_name}"):
            logger.info(f"[Silver Layer]{table_name} : data already exists")
            existing_data = spark.read.parquet(f"s3a://{BUCKET}/{table_name}")
            last_time = existing_data.select(max("updated_at")).collect()[0][0]
            new_data = source_data.filter(col("updated_at") > last_time)
        else:
            logger.info(f"[Silver Layer]{table_name} : data does not exist -> load full data")
            new_data = source_data
        new_data.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(f"s3a://{BUCKET}/{table_name}")
        logger.info(f"[Silver Layer]{table_name} : data saved")
    except Exception as e:
        logger.error(f"[Silver Layer]{table_name} : error: {e}")
        raise e

def write_silver_orders_tables(spark,silver_path: str, table_name: str):
    # bóc tách từ massage rồi ghép vào bảng orders
    try:
        source_data = read_bronze_tables(spark, table_name)
        