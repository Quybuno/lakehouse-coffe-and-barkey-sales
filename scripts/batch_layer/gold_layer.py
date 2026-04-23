"""
Gold layer — ghi star schema dưới dạng Iceberg table qua REST catalog.

Catalog: `iceberg` (trùng tên catalog Trino). Namespace: `gold`.
Bảng:
  - iceberg.gold.dim_store, dim_customers, dim_payment, dim_products
  - iceberg.gold.fact_orders (partition theo `order_date`)

Chạy sau bronze_raw.py + silver_layer.py.

Yêu cầu spark-submit phải kèm:
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.6.1,\\
             org.apache.iceberg:iceberg-aws-bundle:1.6.1,\\
             org.apache.hadoop:hadoop-aws:3.3.1
Config catalog có thể set ở đây (SparkSession.builder) hoặc qua --conf từ DAG.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.functions import col, current_timestamp, row_number
from pyspark.sql.window import Window

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MINIO_BRONZE_BUCKET = "bronze-raw"
MINIO_SILVER_BUCKET = "silver"
SILVER_PREFIX = "silver"

ICEBERG_CATALOG = os.getenv("ICEBERG_CATALOG", "iceberg")
ICEBERG_NAMESPACE = os.getenv("ICEBERG_NAMESPACE", "gold")
ICEBERG_REST_URI = os.getenv("ICEBERG_REST_URI", "http://iceberg-rest:8181")
ICEBERG_WAREHOUSE = os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
# AWS SDK v2 bắt buộc region dù MinIO không dùng — fallback us-east-1.
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# AWS SDK v2 đọc region từ env đầu tiên; set để tránh SdkClientException.
os.environ.setdefault("AWS_REGION", AWS_REGION)
os.environ.setdefault("AWS_DEFAULT_REGION", AWS_REGION)


def s3a_path(bucket: str, *cac_phan_path: str) -> str:
    """Build s3a://bucket/part1/part2 — skip empty parts."""
    phan = "/".join(p.strip("/") for p in cac_phan_path if p)
    return f"s3a://{bucket}/{phan}"


def fqn(table_name: str) -> str:
    """Fully-qualified name: iceberg.gold.<table>."""
    return f"{ICEBERG_CATALOG}.{ICEBERG_NAMESPACE}.{table_name}"


def create_spark_session() -> SparkSession:
    builder = (
        SparkSession.builder.appName("gold-star-schema-iceberg")
        .config("spark.hadoop.fs.s3a.endpoint", MINIO_ENDPOINT)
        .config("spark.hadoop.fs.s3a.access.key", MINIO_ACCESS_KEY)
        .config("spark.hadoop.fs.s3a.secret.key", MINIO_SECRET_KEY)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        # Iceberg extensions + REST catalog
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(
            f"spark.sql.catalog.{ICEBERG_CATALOG}",
            "org.apache.iceberg.spark.SparkCatalog",
        )
        .config(
            f"spark.sql.catalog.{ICEBERG_CATALOG}.catalog-impl",
            "org.apache.iceberg.rest.RESTCatalog",
        )
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.uri", ICEBERG_REST_URI)
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.warehouse", ICEBERG_WAREHOUSE)
        .config(
            f"spark.sql.catalog.{ICEBERG_CATALOG}.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO",
        )
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.s3.endpoint", MINIO_ENDPOINT)
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.s3.path-style-access", "true")
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.client.region", AWS_REGION)
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.s3.region", AWS_REGION)
        .config(f"spark.sql.catalog.{ICEBERG_CATALOG}.s3.access-key-id", MINIO_ACCESS_KEY)
        .config(
            f"spark.sql.catalog.{ICEBERG_CATALOG}.s3.secret-access-key", MINIO_SECRET_KEY
        )
        .config("spark.sql.defaultCatalog", ICEBERG_CATALOG)
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse"))
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir"))
    )
    return builder.getOrCreate()


def dedupe_latest_by_keys(df, keys: list[str]):
    condition = []
    for column in ["updated_at", "timestamp"]:
        if column in df.columns:
            condition.append(F.col(column).desc_nulls_last())
    for column in ["year", "month", "day"]:
        if column in df.columns:
            condition.append(F.col(column).desc())
    if not condition:
        return df
    window = Window.partitionBy(*keys).orderBy(*condition)
    return df.withColumn("row_number", row_number().over(window)).filter(col("row_number") == 1).drop("row_number")


def read_dim_store(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_BRONZE_BUCKET, "store"))
    return dedupe_latest_by_keys(df, ["id"])


def read_dim_customers(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_BRONZE_BUCKET, "customers"))
    return dedupe_latest_by_keys(df, ["id"])


def read_dim_payment(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_BRONZE_BUCKET, "payment_method"))
    return dedupe_latest_by_keys(df, ["id"])


def read_dim_products(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_BRONZE_BUCKET, "products"))
    return dedupe_latest_by_keys(df, ["id"])


def read_silver_orders(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_SILVER_BUCKET, SILVER_PREFIX, "orders"))
    return dedupe_latest_by_keys(df, ["id"])


def read_silver_order_details(spark: SparkSession):
    df = spark.read.parquet(s3a_path(MINIO_SILVER_BUCKET, SILVER_PREFIX, "order_details"))
    return dedupe_latest_by_keys(df, ["order_id", "product_id"])


def build_fact_orders(orders, order_details, dim_store, dim_customers, dim_payment, dim_products):
    o = orders.alias("o")
    d = order_details.alias("d")
    st = dim_store.alias("st")
    cu = dim_customers.alias("cu")
    pm = dim_payment.alias("pm")
    pr = dim_products.alias("pr")

    co_ban = d.join(o, col("d.order_id") == col("o.id"), "inner")
    co_dim = (
        co_ban.join(st, col("o.store_id") == col("st.id"), "inner")
        .join(cu, col("o.customer_id") == col("cu.id"), "inner")
        .join(pm, col("o.payment_method_id") == col("pm.id"), "inner")
        .join(pr, col("d.product_id") == col("pr.id"), "inner")
    )

    return co_dim.select(
        F.to_date(col("o.timestamp")).alias("order_date"),
        F.hour(col("o.timestamp")).alias("order_hour"),
        F.dayofweek(col("o.timestamp")).alias("order_dow"),
        col("o.id").cast("string").alias("order_id"),
        col("o.customer_id").cast("int").alias("customer_id"),
        col("o.store_id").cast("int").alias("store_key"),
        col("o.payment_method_id").cast("int").alias("payment_method_key"),
        col("pr.id").cast("string").alias("product_key"),
        col("d.quantity").cast("int").alias("quantity"),
        col("d.subtotal").cast("int").alias("subtotal"),
        F.coalesce(col("d.is_suggestion"), F.lit(False)).alias("is_suggestion"),
    )


def ensure_namespace(spark: SparkSession) -> None:
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {ICEBERG_CATALOG}.{ICEBERG_NAMESPACE}")
    logger.info("[Gold] ensured namespace %s.%s", ICEBERG_CATALOG, ICEBERG_NAMESPACE)


def write_iceberg_table(df, ten_bang: str, partitions: list | None = None) -> None:
    """
    Ghi Iceberg kiểu createOrReplace — lần đầu tạo, các lần sau atomic swap.

    Đồ án không cần MERGE/incremental cho gold; giữ logic đơn giản, tương đương
    overwrite toàn bảng nhưng atomic (Trino đọc không bao giờ thấy table rỗng giữa chừng).
    """
    ten_day_du = fqn(ten_bang)
    writer = df.writeTo(ten_day_du).using("iceberg")
    if partitions:
        writer = writer.partitionedBy(*partitions)
    writer.createOrReplace()
    logger.info("[Gold] wrote %s", ten_day_du)


def main() -> None:
    spark = create_spark_session()
    try:
        ensure_namespace(spark)

        ts = current_timestamp()
        dim_store = read_dim_store(spark).withColumn("gold_ingested_at", ts)
        dim_customers = read_dim_customers(spark).withColumn("gold_ingested_at", ts)
        dim_payment = read_dim_payment(spark).withColumn("gold_ingested_at", ts)
        dim_products = read_dim_products(spark).withColumn("gold_ingested_at", ts)

        orders = read_silver_orders(spark)
        order_details = read_silver_order_details(spark)

        orders = orders.withColumn("id", col("id").cast("string"))
        order_details = order_details.withColumn("order_id", col("order_id").cast("string"))
        order_details = order_details.withColumn("product_id", col("product_id").cast("string"))

        fact = build_fact_orders(
            orders, order_details, dim_store, dim_customers, dim_payment, dim_products
        )

        write_iceberg_table(dim_store, "dim_store")
        write_iceberg_table(dim_customers, "dim_customers")
        write_iceberg_table(dim_payment, "dim_payment")
        write_iceberg_table(dim_products, "dim_products")
        write_iceberg_table(fact, "fact_orders", partitions=[col("order_date")])
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
