"""
Bronze layer — bước đầu trong data lake (Medallion).

Thứ tự chạy:
  1) Read các bảng dimension từ MySQL → write Parquet lên MinIO (bucket bronze).
  2) Read orders + order_details → write Parquet (incremental nếu đã có dữ liệu cũ).
  3) Read topic Kafka orders_accept_rule → write Parquet (luật gợi ý / unlock).

Cần .env: MYSQL_*, MINIO_*, KAFKA_BOOTSTRAP_SERVERS (optional).
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    current_timestamp,
    day,
    from_json,
    hour,
    max,
    month,
    year,
)
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    StringType,
    StructField,
    StructType,
)

# Thư mục gốc project (scripts → batch_layer → scripts → ROOT)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")


def _fix_typo_msql_keys_from_dotenv() -> None:
    """
    `.env` ghi nhầm MSQL_* thay MYSQL_*.
    - MSQL_PASSWORD luôn map → MYSQL_PASSWORD (ghi đè), vì sai tên hay khiến compose dùng mật khẩu default.
    - Các key khác chỉ map khi MYSQL_* đang trống (tránh MSQL_HOST=127.0.0.1 ghi đè MYSQL_HOST=mysql trong Docker).
    """
    path = BASE_DIR / ".env"
    if not path.is_file():
        return
    vals = dotenv_values(path)
    typo_to_real = (
        ("MSQL_HOST", "MYSQL_HOST"),
        ("MSQL_PORT", "MYSQL_PORT"),
        ("MSQL_USER", "MYSQL_USER"),
        ("MSQL_PASSWORD", "MYSQL_PASSWORD"),
        ("MSQL_DATABASE", "MYSQL_DATABASE"),
    )
    for wrong, right in typo_to_real:
        v = vals.get(wrong)
        if v is None or str(v).strip() == "":
            continue
        v = str(v).strip()
        if right == "MYSQL_PASSWORD":
            os.environ[right] = v
            continue
        cur = os.environ.get(right)
        if cur is None or str(cur).strip() == "":
            os.environ[right] = v


_fix_typo_msql_keys_from_dotenv()

from scripts.utils import check_minio_has_data  # noqa: E402 — after load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


BRONZE_BUCKET ="bronze-raw"

ORDERS_ACCEPT_RULE_SCHEMA = StructType(
    [
        StructField("order_id", StringType(), True),
        StructField("store_id", StringType(), True),
        StructField("customer_id", StringType(), True),
        StructField("payment_method_id", StringType(), True),
        StructField("accepted_product_ids", ArrayType(StringType()), True),
        StructField("is_suggestion", BooleanType(), True),
        StructField("unlocked_by_accepted_suggestion", BooleanType(), True),
    ]
)


def add_partition_ymd_columns(df):
    """Add columns year, month, day = ngày chạy job (để partition trên MinIO)."""
    now = current_timestamp()
    return (
        df.withColumn("year", year(now))
        .withColumn("month", month(now))
        .withColumn("day", day(now))
    )


def spark_scratch_directory() -> Path:
    """
    Spark cần thư mục write tạm. User airflow trong Docker đôi khi không write được project dir,
    nên mặc định dùng /tmp/...
    """
    tuy_chon = (os.getenv("BRONZE_SPARK_WORKDIR") or "").strip()
    if tuy_chon:
        root = Path(tuy_chon)
    else:
        root = Path(os.getenv("TMPDIR", "/tmp")) / "bronze_raw_spark"
    (root / "sql_warehouse").mkdir(parents=True, exist_ok=True)
    (root / "local_dir").mkdir(parents=True, exist_ok=True)
    return root


def create_spark_session():
    return SparkSession.builder \
        .appName("Bronze Raw") \
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000") \
        .config("spark.hadoop.fs.s3a.access.key", os.getenv("MINIO_ROOT_USER", "minioadmin")) \
        .config("spark.hadoop.fs.s3a.secret.key", os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")) \
        .config("spark.hadoop.fs.s3a.path.style.access", "true") \
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false") \
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse")) \
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir")) \
        .getOrCreate()

def read_mysql_table(spark: SparkSession, table: str):
    host = (os.getenv("MYSQL_HOST") or "mysql").strip()
    port = (os.getenv("MYSQL_PORT") or "3306").strip()
    user = (os.getenv("MYSQL_USER") or "root").strip()
    password = (os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQL_ROOT_PASSWORD") or "").strip()
    database = (os.getenv("MYSQL_DATABASE") or "kd_bakery_coffee").strip()
    if not password:
        raise RuntimeError(
            "MYSQL_PASSWORD is empty. Use MYSQL_* (not MSQL_*) in .env; in Docker set MYSQL_HOST=mysql."
        )

    url = (
        f"jdbc:mysql://{host}:{port}/{database}"
        "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC"
    )

    return (
        spark.read.format("jdbc")
        .option("url", url)
        .option("driver", "com.mysql.cj.jdbc.Driver")
        .option("dbtable", table)
        .option("user", user)
        .option("password", password)
        .load()
    )

def sync_dim_table_to_bronze(spark: SparkSession, ten_bang: str, cot_thoi_gian: str = "updated_at"):
    """
    Sync một bảng dimension lên MinIO.
    - Lần đầu: full write.
    - Lần sau: incremental write — chỉ các dòng có cot_thoi_gian > max đã có trên MinIO.
    """
    duong_dan = f"s3a://{BRONZE_BUCKET}/{ten_bang}"
    logger.info("[Bronze] Bắt đầu bảng %s", ten_bang)

    df_mysql = read_mysql_table(spark, ten_bang)

    # Không có cột time → không incremental → full read rồi full write
    cot_dung_de_loc = cot_thoi_gian
    if cot_dung_de_loc not in df_mysql.columns:
        logger.warning("[Bronze] Bảng %s không có cột %s → load full", ten_bang, cot_thoi_gian)
        cot_dung_de_loc = None

    da_co_du_lieu_tren_minio = check_minio_has_data(spark, BRONZE_BUCKET, ten_bang)

    if cot_dung_de_loc and da_co_du_lieu_tren_minio:
        lan_cuoi = spark.read.parquet(duong_dan).select(max(cot_dung_de_loc)).collect()[0][0]  
        df_can_ghi = df_mysql.filter(col(cot_dung_de_loc) > lan_cuoi)
        logger.info("[Bronze] %s incremental, mốc thời gian: %s", ten_bang, lan_cuoi)
    else:
        df_can_ghi = df_mysql
        logger.info("[Bronze] %s full load", ten_bang)

    if df_can_ghi.limit(1).count() == 0:
        logger.info("[Bronze] %s không có dòng mới", ten_bang)
        return

    df_ghi = add_partition_ymd_columns(df_can_ghi)
    so_dong = df_can_ghi.count()
    logger.info("[Bronze] %s ghi %s dòng", ten_bang, so_dong)
    df_ghi.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(duong_dan)
    logger.info("[Bronze] %s xong", ten_bang)


def sync_orders_and_details_to_bronze(spark: SparkSession):
    """
    Read orders + order_details, inner join theo order id, write 2 folder parquet.

    Lưu ý: bảng orders có thể không có updated_at — copy timestamp → updated_at để incremental
    dùng một tên cột thống nhất.
    """
    logger.info("[Bronze] orders + order_details: bắt đầu")
    orders = read_mysql_table(spark, "orders")
    chi_tiet = read_mysql_table(spark, "order_details")
    path_orders = f"s3a://{BRONZE_BUCKET}/orders"

    if "updated_at" not in orders.columns:
        orders = orders.withColumn("updated_at", col("timestamp"))

    if check_minio_has_data(spark, BRONZE_BUCKET, "orders"):
        moc = spark.read.parquet(path_orders).select(max("updated_at")).collect()[0][0]
        orders_moi = orders.filter(col("updated_at") > moc)
    else:
        orders_moi = orders

    if orders_moi.limit(1).count() == 0:
        logger.info("[Bronze] orders: không có đơn mới")
        return

    orders_co_partition = (
        orders_moi.withColumn("year", year(current_timestamp()))
        .withColumn("month", month(current_timestamp()))
        .withColumn("day", day(current_timestamp()))
        .withColumn("hour", hour(current_timestamp()))
    )

    # Inner join: chỉ giữ order_details thuộc các orders vừa sync
    chi_tiet_ghep = (
        orders_co_partition.join(
            chi_tiet,
            orders_co_partition["id"] == chi_tiet["order_id"],
            "inner",
        ).select(
            "order_id",
            "product_id",
            "quantity",
            "discount_percent",
            "subtotal",
            "is_suggestion",
            "updated_at",
            "year",
            "month",
            "day",
        )
    )

    orders_co_partition.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(
        path_orders
    )
    chi_tiet_ghep.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(
        f"s3a://{BRONZE_BUCKET}/order_details"
    )
    logger.info("[Bronze] orders + order_details: xong")


def sync_kafka_orders_accept_rule(spark: SparkSession):
    """Read Kafka topic orders_accept_rule (batch mode) → write parquet lên MinIO."""
    bootstrap = "kafka-1:9092,kafka-2:9092,kafka-3:9092"
    logger.info("[Bronze] Kafka orders_accept_rule: bắt đầu")
    raw = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", "accept_rule")
        .option("startingOffsets", "earliest")
        .load()
    )
    # Kafka value: bytes → cast string JSON → from_json theo schema
    parsed = (
        raw.selectExpr("CAST(value AS string) AS json_string")
        .select(from_json(col("json_string"), ORDERS_ACCEPT_RULE_SCHEMA).alias("payload"))
        .select("payload.*")
    )
    out = add_partition_ymd_columns(parsed)
    out.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(
        f"s3a://{BRONZE_BUCKET}/accept_rule"
    )
    logger.info("[Bronze] Kafka orders_accept_rule: xong")


def main():
    logger.info("[Bronze] === START ===")
    spark = create_spark_session()
    try:
        cac_bang_dim = ("store", "product_category", "products", "payment_method", "customers")
        for ten in cac_bang_dim:
            sync_dim_table_to_bronze(spark, ten)
        sync_orders_and_details_to_bronze(spark)
        sync_kafka_orders_accept_rule(spark)
        logger.info("[Bronze] === DONE ===")
    except Exception:
        logger.exception("[Bronze] Lỗi")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
