"""
Gold: star schema gần chuẩn — dim từ snapshot bronze MySQL, fact từ silver (orders + order_details).

Đọc:
  - Bronze Parquet: store, payment_method, products (cùng prefix với bronze_raw).
  - Silver Parquet: orders, order_details (output của silver_orders_enriched.py).

Ghi MinIO (S3A):
  - gold/dim_store/
  - gold/dim_payment/        (bảng nguồn MySQL: payment_method)
  - gold/dim_products/
  - gold/fact_orders/        một dòng / dòng order_detail, kèm cột header đơn hàng + enrich từ dim

Biến môi trường:
  MINIO_BRONZE_BUCKET, MINIO_SILVER_BUCKET, MINIO_GOLD_BUCKET (mặc định = silver bucket)
  BRONZE_READ_PREFIX (mặc định bronze/raw)
  SILVER_WRITE_PREFIX (mặc định silver)
  GOLD_WRITE_PREFIX (mặc định gold)

Chạy sau bronze_raw + silver_orders_enriched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, row_number
from pyspark.sql.window import Window

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))

MINIO_BRONZE_BUCKET = os.getenv("MINIO_BRONZE_BUCKET", "bronze-raw")
MINIO_SILVER_BUCKET = os.getenv("MINIO_SILVER_BUCKET", "bronze-raw")
MINIO_GOLD_BUCKET = os.getenv("MINIO_GOLD_BUCKET", MINIO_SILVER_BUCKET)
BRONZE_PREFIX = os.getenv("BRONZE_READ_PREFIX", "bronze/raw")
SILVER_PREFIX = os.getenv("SILVER_WRITE_PREFIX", "silver")
GOLD_PREFIX = os.getenv("GOLD_WRITE_PREFIX", "gold")


def _normalize_prefix(p: str) -> str:
    return p if p.endswith("/") else p + "/"


def _s3a(base: str, prefix: str) -> str:
    return f"s3a://{base}/{_normalize_prefix(prefix)}"


def _dedupe_latest_bronze(df, partition_cols: list[str]):
    if "bronze_ingested_at" not in df.columns:
        return df
    w = Window.partitionBy(*partition_cols).orderBy(col("bronze_ingested_at").desc())
    return df.withColumn("_rn", row_number().over(w)).filter(col("_rn") == 1).drop("_rn")


def _read_dim_store(spark: SparkSession):
    path = _s3a(MINIO_BRONZE_BUCKET, f"{BRONZE_PREFIX}/store")
    df = spark.read.parquet(path)
    return _dedupe_latest_bronze(df, ["id"])


def _read_dim_payment(spark: SparkSession):
    path = _s3a(MINIO_BRONZE_BUCKET, f"{BRONZE_PREFIX}/payment_method")
    df = spark.read.parquet(path)
    return _dedupe_latest_bronze(df, ["id"])


def _read_dim_products(spark: SparkSession):
    path = _s3a(MINIO_BRONZE_BUCKET, f"{BRONZE_PREFIX}/products")
    df = spark.read.parquet(path)
    return _dedupe_latest_bronze(df, ["id"])


def _read_silver_orders(spark: SparkSession):
    path = _s3a(MINIO_SILVER_BUCKET, f"{SILVER_PREFIX}/orders")
    df = spark.read.parquet(path)
    return _dedupe_latest_bronze(df, ["id"])


def _read_silver_order_details(spark: SparkSession):
    path = _s3a(MINIO_SILVER_BUCKET, f"{SILVER_PREFIX}/order_details")
    df = spark.read.parquet(path)
    return _dedupe_latest_bronze(df, ["order_id", "product_id"])


def build_fact_orders(spark: SparkSession, orders, order_details, dim_store, dim_payment, dim_products):
    o = orders.alias("o")
    d = order_details.alias("d")
    st = dim_store.alias("st")
    pm = dim_payment.alias("pm")
    pr = dim_products.alias("pr")

    base = d.join(o, col("d.order_id") == col("o.id"), "inner")

    fact = (
        base.join(st, col("o.store_id") == col("st.id"), "left")
        .join(pm, col("o.payment_method_id") == col("pm.id"), "left")
        .join(pr, col("d.product_id") == col("pr.id"), "left")
    )

    fact = fact.select(
        col("d.order_id"),
        col("d.product_id"),
        col("d.quantity").alias("line_quantity"),
        col("d.discount_percent").alias("line_discount_percent"),
        col("d.subtotal").alias("line_subtotal"),
        col("d.is_suggestion").alias("line_is_suggestion_mysql"),
        col("d.is_suggestion_silver"),
        col("d.rule_suggestion_accepted"),
        col("d.rule_suggestion_source"),
        col("d.rule_suggestion_line_is_suggestion"),
        col("d.rule_suggestion_kafka_topic"),
        col("d.rule_suggestion_kafka_offset"),
        col("d.rule_suggestion_event_store_id"),
        col("d.rule_suggestion_bronze_source"),
        col("d.silver_ingested_at").alias("line_silver_ingested_at"),
        col("o.timestamp").alias("order_timestamp"),
        col("o.store_id"),
        col("o.customer_id"),
        col("o.payment_method_id"),
        col("o.num_product").alias("order_num_product"),
        col("o.status").alias("order_status"),
        col("o.rule_discount_percent"),
        col("o.rule_discount_status"),
        col("o.rule_discount_event_ts"),
        col("o.rule_discount_kafka_offset"),
        col("o.rule_discount_kafka_topic"),
        col("o.rule_discount_source_topic"),
        col("o.rule_unlocked_by_accepted_suggestion"),
        col("o.rule_kafka_store_id"),
        col("o.rule_kafka_customer_id"),
        col("o.rule_kafka_payment_method_id"),
        col("o.rule_kafka_num_product"),
        col("o.rule_kafka_quantity"),
        col("o.rule_kafka_total_price_before"),
        col("o.rule_kafka_total_price_after"),
        col("o.rule_kafka_product_ids_before"),
        col("o.rule_kafka_product_ids_after"),
        col("o.rule_kafka_is_suggestion"),
        col("o.rule_kafka_event_type"),
        col("o.rule_kafka_bronze_source"),
        col("o.silver_ingested_at").alias("order_silver_ingested_at"),
        col("st.name").alias("store_name"),
        col("st.city").alias("store_city"),
        col("st.district").alias("store_district"),
        col("pm.method_name").alias("payment_method_name"),
        col("pm.bank").alias("payment_bank"),
        col("pr.name").alias("product_name"),
        col("pr.category_id").alias("product_category_id"),
        col("pr.unit_price").alias("product_unit_price"),
    )

    return fact.withColumn("gold_ingested_at", current_timestamp())


def main() -> None:
    s3_key = os.getenv("MINIO_ROOT_USER")
    s3_secret = os.getenv("MINIO_ROOT_PASSWORD")
    s3_endpoint = os.getenv("MINIO_S3_ENDPOINT", "http://minio:9000")

    spark = (
        SparkSession.builder.appName("gold-dim-fact-star-schema")
        .config("spark.hadoop.fs.s3a.endpoint", s3_endpoint)
        .config("spark.hadoop.fs.s3a.access.key", s3_key)
        .config("spark.hadoop.fs.s3a.secret.key", s3_secret)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse"))
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir"))
        .getOrCreate()
    )

    try:
        dim_store = _read_dim_store(spark).withColumn("gold_ingested_at", current_timestamp())
        dim_payment = _read_dim_payment(spark).withColumn("gold_ingested_at", current_timestamp())
        dim_products = _read_dim_products(spark).withColumn("gold_ingested_at", current_timestamp())

        orders = _read_silver_orders(spark)
        order_details = _read_silver_order_details(spark)

        orders = orders.withColumn("id", col("id").cast("string"))
        order_details = order_details.withColumn("order_id", col("order_id").cast("string"))
        order_details = order_details.withColumn("product_id", col("product_id").cast("string"))

        fact_orders = build_fact_orders(
            spark, orders, order_details, dim_store, dim_payment, dim_products
        )

        out_dim_store = _s3a(MINIO_GOLD_BUCKET, f"{GOLD_PREFIX}/dim_store")
        out_dim_payment = _s3a(MINIO_GOLD_BUCKET, f"{GOLD_PREFIX}/dim_payment")
        out_dim_products = _s3a(MINIO_GOLD_BUCKET, f"{GOLD_PREFIX}/dim_products")
        out_fact = _s3a(MINIO_GOLD_BUCKET, f"{GOLD_PREFIX}/fact_orders")

        dim_store.write.mode("overwrite").format("parquet").save(out_dim_store)
        dim_payment.write.mode("overwrite").format("parquet").save(out_dim_payment)
        dim_products.write.mode("overwrite").format("parquet").save(out_dim_products)
        fact_orders.write.mode("overwrite").format("parquet").save(out_fact)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
