"""
Gold layer — fact + dim cho star schema.

  - Dim (store, customers, payment, products): read từ bronze Parquet.
  - Fact (fact_orders): mỗi row = một order_detail; inner join orders để có ngày đặt, khách, store, payment.

Chạy sau bronze_raw và silver_layer (silver đã write orders + order_details).
"""

from __future__ import annotations

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

MINIO_BRONZE_BUCKET = "bronze-raw"
MINIO_SILVER_BUCKET = "silver"
MINIO_GOLD_BUCKET =  "gold"
SILVER_PREFIX =  "silver"


def s3a_path(bucket: str, *cac_phan_path: str) -> str:
    """Build s3a://bucket/part1/part2 — skip empty parts."""
    phan = "/".join(p.strip("/") for p in cac_phan_path if p)
    return f"s3a://{bucket}/{phan}"


def dedupe_latest_by_keys(df, cac_cot_khoa: list[str]):
    """
    Nhiều lần append có thể duplicate key: order by time, keep row_number = 1.
    """
    dieu_kien = []
    for ten in ("updated_at", "timestamp"):
        if ten in df.columns:
            dieu_kien.append(F.col(ten).desc_nulls_last())
    for ten in ("year", "month", "day"):
        if ten in df.columns:
            dieu_kien.append(F.col(ten).desc())
    if not dieu_kien:
        return df
    w = Window.partitionBy(*cac_cot_khoa).orderBy(*dieu_kien)
    return df.withColumn("_xep_hang", row_number().over(w)).filter(col("_xep_hang") == 1).drop("_xep_hang")


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
    """
    Inner join các dim: chỉ giữ rows có FK hợp lệ (drop fact orphan).
    """
    o = orders.alias("o")
    d = order_details.alias("d")
    st = dim_store.alias("st")
    cu = dim_customers.alias("cu")
    pm = dim_payment.alias("pm")
    pr = dim_products.alias("pr")

    # Step 1: order_details inner join orders (header)
    co_ban = d.join(o, col("d.order_id") == col("o.id"), "inner")

    # Step 2: inner join từng dimension
    co_dim = (
        co_ban.join(st, col("o.store_id") == col("st.id"), "inner")
        .join(cu, col("o.customer_id") == col("cu.id"), "inner")
        .join(pm, col("o.payment_method_id") == col("pm.id"), "inner")
        .join(pr, col("d.product_id") == col("pr.id"), "inner")
    )

    return co_dim.select(
        F.to_date(col("o.timestamp")).alias("order_date"),
        col("o.id").cast("string").alias("order_id"),
        col("o.customer_id").cast("int").alias("customer_id"),
        col("o.store_id").cast("int").alias("store_key"),
        col("o.payment_method_id").cast("int").alias("payment_method_key"),
        col("pr.id").cast("string").alias("product_key"),
        col("d.quantity").cast("int").alias("quantity"),
        col("d.subtotal").cast("int").alias("subtotal"),
    )


def main() -> None:
    access_key = os.getenv("MINIO_ROOT_USER") or "minioadmin"
    secret_key = os.getenv("MINIO_ROOT_PASSWORD") or "minioadmin"
    endpoint = os.getenv("MINIO_ENDPOINT") or "http://minio:9000"

    spark = (
        SparkSession.builder.appName("gold-star-schema")
        .config("spark.hadoop.fs.s3a.endpoint", endpoint)
        .config("spark.hadoop.fs.s3a.access.key", access_key)
        .config("spark.hadoop.fs.s3a.secret.key", secret_key)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse"))
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir"))
        .getOrCreate()
    )

    try:
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

        fact = build_fact_orders(orders, order_details, dim_store, dim_customers, dim_payment, dim_products)

        dim_store.write.mode("overwrite").format("parquet").save(s3a_path(MINIO_GOLD_BUCKET, "dim_store"))
        dim_customers.write.mode("overwrite").format("parquet").save(
            s3a_path(MINIO_GOLD_BUCKET, "dim_customers")
        )
        dim_payment.write.mode("overwrite").format("parquet").save(
            s3a_path(MINIO_GOLD_BUCKET,"dim_payment")
        )
        dim_products.write.mode("overwrite").format("parquet").save(
            s3a_path(MINIO_GOLD_BUCKET,  "dim_products")
        )
        fact.write.mode("overwrite").format("parquet").save(s3a_path(MINIO_GOLD_BUCKET,  "fact_orders"))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
