"""
Silver layer — 2 nhiệm vụ:

1) Load các bảng từ bronze lên silver:
     - Dim: store, product_category, products, payment_method, customers.
     - Fact: orders, order_details.

2) Xử lý message topic Kafka `accept_rule` (chỉ các message có
   `unlocked_by_accepted_suggestion = true`) → tách thành 2 bảng silver:
     - orders: tạo 1 dòng MỚI, clone theo order_id, set status = "10",
       num_product += size(accepted_product_ids). Sau đó dedupe theo id để loại
       các dòng status != 10 (chỉ giữ dòng đã unlock khi nó tồn tại).
     - order_details: explode accepted_product_ids, mỗi product_id thành 1 row
       với quantity = 1, subtotal = UNIT_PRICE_MAP[product_id], is_suggestion = true.
       Union với bronze order_details.
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.functions import (
    col,
    current_timestamp,
    dayofmonth,
    max,
    month,
    year,
)
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)
from pyspark.sql.window import Window

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from scripts.utils import check_minio_has_data  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename=BASE_DIR / "logs" / "batch.log",
)
logger = logging.getLogger(__name__)

BRONZE_BUCKET = "bronze-raw"
SILVER_BUCKET = "silver"

# Folder kafka trên MinIO (bronze đã ghi); ghép với BRONZE_BUCKET thành s3a://...
KAFKA_ACCEPT_RULE_BRONZE = "accept_rule"

# Order status khi đã unlock đủ để tính tiền (gold / báo cáo)
ORDER_STATUS_UNLOCKED = "10"

# Map product_id → đơn giá (khi create dòng từ Kafka, chưa join bảng products)
UNIT_PRICE_MAP = {
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


def unit_price_for_product_column(cot_product_id):
    """
    Trên worker không dùng dict Python; build Spark map bằng create_map(lit, lit, ...).
    Product không có trong map → 0.
    """
    cap_k_va_v = []
    for ma, gia in UNIT_PRICE_MAP.items():
        cap_k_va_v.append(F.lit(ma))
        cap_k_va_v.append(F.lit(int(gia)))
    bang_anh_xa = F.create_map(*cap_k_va_v)
    return F.coalesce(bang_anh_xa[cot_product_id], F.lit(0))


def create_spark_session():
    return SparkSession.builder \
        .appName("Bronze Raw") \
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000") \
        .config("spark.hadoop.fs.s3a.access.key", os.getenv("MINIO_ROOT_USER")) \
        .config("spark.hadoop.fs.s3a.secret.key", os.getenv("MINIO_ROOT_PASSWORD")) \
        .config("spark.hadoop.fs.s3a.path.style.access", "true") \
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false") \
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse")) \
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir")) \
        .getOrCreate()




def read_bronze_parquet(spark: SparkSession, ten_bang: str):
    return spark.read.parquet(f"s3a://{BRONZE_BUCKET}/{ten_bang}")


CAC_TEN_COT_THOI_GIAN_UU_TIEN = (
    "ts",
    "timestamp",
    "event_time",
    "created_at",
    "updated_at",
    "kafka_timestamp",
)


def kafka_latest_per_order(df_kafka):
    """
    Nhiều Kafka message cùng order_id → chọn 1 row mới nhất.

    Schema Kafka thực tế (xem bronze_raw.ORDERS_ACCEPT_RULE_SCHEMA) KHÔNG có cột thời gian cụ thể.
    Nên dò động: cột nào trong CAC_TEN_COT_THOI_GIAN_UU_TIEN mà tồn tại thì dùng; không có thì
    fallback year/month/day (partition do bronze ghi). Không có gì → literal (row_number vẫn
    chọn 1 row per order_id, thứ tự không xác định nhưng deterministic trong 1 job).
    """
    df = df_kafka

    if "suggestion_lines" not in df.columns:
        df = df.withColumn(
            "suggestion_lines",
            F.expr("cast(null as array<struct<product_id:string,is_suggestion:boolean,source:string>>)"),
        )
    if "unlocked_by_accepted_suggestion" not in df.columns:
        df = df.withColumn("unlocked_by_accepted_suggestion", F.lit(None).cast("boolean"))
    if "quantity" not in df.columns:
        df = df.withColumn("quantity", F.lit(None).cast("long"))
    if "accepted_product_ids" not in df.columns:
        df = df.withColumn("accepted_product_ids", F.array().cast(ArrayType(StringType())))

    dieu_kien_sap_xep = []
    cot_ts = next((c for c in CAC_TEN_COT_THOI_GIAN_UU_TIEN if c in df.columns), None)
    if cot_ts is not None:
        ts_sap_xep = F.coalesce(
            F.to_timestamp(F.col(cot_ts)),
            F.to_timestamp(F.col(cot_ts), "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"),
            F.to_timestamp(F.col(cot_ts), "yyyy-MM-dd'T'HH:mm:ss"),
        )
        dieu_kien_sap_xep.append(ts_sap_xep.desc_nulls_last())
    else:
        logger.warning(
            "[Silver] Kafka DataFrame không có cột thời gian (%s); fallback year/month/day.",
            "|".join(CAC_TEN_COT_THOI_GIAN_UU_TIEN),
        )

    for c in ("year", "month", "day"):
        if c in df.columns:
            dieu_kien_sap_xep.append(F.col(c).desc())

    if not dieu_kien_sap_xep:
        dieu_kien_sap_xep = [F.lit(1)]

    cua_so = Window.partitionBy("order_id").orderBy(*dieu_kien_sap_xep)
    return (
        df.withColumn("thu_tu", F.row_number().over(cua_so))
        .filter(F.col("thu_tu") == 1)
        .drop("thu_tu")
    )



def read_kafka_bronze_or_empty(spark: SparkSession):
    """
    Chưa có path kafka trên MinIO → return empty DataFrame đúng schema (left join an toàn).

    Schema khớp với bronze_raw.ORDERS_ACCEPT_RULE_SCHEMA + partition year/month/day;
    không khai báo `ts`/`suggestion_lines`/`quantity` vì bronze không ghi các cột đó
    (kafka_latest_per_order sẽ tự bổ sung nếu thiếu).
    """
    path_day_du = f"s3a://{BRONZE_BUCKET}/{KAFKA_ACCEPT_RULE_BRONZE}"
    if not check_minio_has_data(spark, BRONZE_BUCKET, KAFKA_ACCEPT_RULE_BRONZE):
        schema_rong = StructType(
            [
                StructField("order_id", StringType(), True),
                StructField("store_id", StringType(), True),
                StructField("customer_id", StringType(), True),
                StructField("payment_method_id", StringType(), True),
                StructField("accepted_product_ids", ArrayType(StringType()), True),
                StructField("is_suggestion", BooleanType(), True),
                StructField("unlocked_by_accepted_suggestion", BooleanType(), True),
                StructField("year", IntegerType(), True),
                StructField("month", IntegerType(), True),
                StructField("day", IntegerType(), True),
            ]
        )
        return spark.createDataFrame([], schema_rong)
    return spark.read.parquet(path_day_du)


def write_dim_to_silver(spark, tien_to_silver: str, ten_bang: str, cot_thoi_gian: str = "updated_at"):
    """
    Sync dim bronze → silver.
      - Có cot_thoi_gian + silver đã tồn tại: incremental append theo max(cot_thoi_gian).
      - Không có cot_thoi_gian (bảng dim tĩnh): overwrite toàn bộ.
      - Silver chưa có: append lần đầu.
    """
    try:
        logger.info("[Silver] dim %s: bắt đầu", ten_bang)
        if not check_minio_has_data(spark, BRONZE_BUCKET, ten_bang):
            logger.warning("[Silver] dim %s: bronze chưa có dữ liệu, bỏ qua", ten_bang)
            return

        nguon = read_bronze_parquet(spark, ten_bang)
        key = f"{tien_to_silver.strip('/')}/{ten_bang}"
        duong_dan_silver = f"s3a://{SILVER_BUCKET}/{key}"
        silver_da_co = check_minio_has_data(spark, SILVER_BUCKET, key)
        co_cot_thoi_gian = cot_thoi_gian in nguon.columns

        if not co_cot_thoi_gian:
            logger.warning(
                "[Silver] dim %s: không có cột %s → overwrite full", ten_bang, cot_thoi_gian
            )
            che_do = "overwrite"
            moi = nguon
        elif silver_da_co:
            lan_cuoi = (
                spark.read.parquet(duong_dan_silver).select(max(cot_thoi_gian)).collect()[0][0]
            )
            if lan_cuoi is None:
                moi = nguon
            else:
                moi = nguon.filter(col(cot_thoi_gian) > lan_cuoi)
            che_do = "append"
            logger.info("[Silver] dim %s: incremental, mốc %s", ten_bang, lan_cuoi)
        else:
            moi = nguon
            che_do = "append"
            logger.info("[Silver] dim %s: lần đầu ghi silver", ten_bang)

        if moi.limit(1).count() == 0:
            logger.info("[Silver] dim %s: không có dòng mới", ten_bang)
            return

        writer = moi.write.mode(che_do).format("parquet")
        cac_cot_partition = [c for c in ("year", "month", "day") if c in moi.columns]
        if cac_cot_partition:
            writer = writer.partitionBy(*cac_cot_partition)
        writer.save(duong_dan_silver)
        logger.info("[Silver] dim %s: xong (%s)", ten_bang, che_do)
    except Exception as e:
        logger.error("[Silver] dim %s lỗi: %s", ten_bang, e)
        raise


def read_bronze_order_details(spark: SparkSession):
    if not check_minio_has_data(spark, BRONZE_BUCKET, "order_details"):
        return None
    return read_bronze_parquet(spark, "order_details")


def dedupe_orders_prefer_unlocked(df):
    """
    Nhiều snapshot cùng order id trong bronze.
    Prefer: status=10, num_product cao hơn, timestamp mới hơn.
    """
    if "timestamp" in df.columns:
        sap_xep_theo_thoi_gian = F.col("timestamp").desc_nulls_last()
    else:
        sap_xep_theo_thoi_gian = F.lit(1).desc()

    la_trang_thai_10 = (F.trim(F.col("status").cast("string")) == F.lit(ORDER_STATUS_UNLOCKED)).cast(
        "int"
    )

    cua_so = Window.partitionBy("id").orderBy(
        la_trang_thai_10.desc(),
        F.col("num_product").cast("long").desc(),
        sap_xep_theo_thoi_gian,
    )
    return (
        df.withColumn("thu_tu", F.row_number().over(cua_so))
        .filter(F.col("thu_tu") == 1)
        .drop("thu_tu")
    )


def build_orders_rows_from_kafka(kafka_unlocked, orders_bronze):
    """
    Với mỗi kafka message có unlocked_by_accepted_suggestion=true:
      clone row orders từ bronze (join theo id = order_id) → tạo row MỚI với
      status = ORDER_STATUS_UNLOCKED và num_product += size(accepted_product_ids).
    Return DataFrame cùng schema với orders_bronze (sẵn sàng union).
    Nếu không có kafka unlocked → return DF rỗng cùng schema (no-op).
    """
    so_san_pham_them = F.coalesce(
        F.size(F.coalesce(F.col("k.accepted_product_ids"), F.expr("array()"))),
        F.lit(0),
    )

    ghep = orders_bronze.alias("o").join(
        kafka_unlocked.alias("k"),
        F.col("o.id") == F.col("k.order_id"),
        how="inner",
    )

    cot_moi = []
    for ten_cot in orders_bronze.columns:
        if ten_cot == "status":
            cot_moi.append(F.lit(ORDER_STATUS_UNLOCKED).alias("status"))
        elif ten_cot == "num_product":
            num_cu = F.coalesce(F.col("o.num_product").cast("long"), F.lit(0))
            cot_moi.append((num_cu + so_san_pham_them).cast("int").alias("num_product"))
        else:
            cot_moi.append(F.col(f"o.{ten_cot}").alias(ten_cot))
    return ghep.select(*cot_moi)


def build_order_details_rows_from_kafka(kafka_unlocked, ts_ghi):
    """
    Với mỗi message kafka unlocked, explode accepted_product_ids → 1 row / product_id:
      - quantity = 1
      - discount_percent = 0
      - subtotal = UNIT_PRICE_MAP[product_id] (0 nếu không có trong map)
      - is_suggestion = true
      - updated_at = ts_ghi
    """
    so_pt_accept = F.size(F.coalesce(F.col("accepted_product_ids"), F.expr("array()")))
    return (
        kafka_unlocked.filter(so_pt_accept > 0)
        .withColumn("product_id", F.explode(F.col("accepted_product_ids")))
        .select(
            F.col("order_id"),
            F.col("product_id"),
            F.lit(1).cast("long").alias("quantity"),
            F.lit(0).cast("long").alias("discount_percent"),
            unit_price_for_product_column(F.col("product_id")).alias("subtotal"),
            F.lit(True).alias("is_suggestion"),
            ts_ghi.alias("updated_at"),
        )
    )


def write_silver_orders_and_details(spark, tien_to_silver: str, ten_bang_orders: str):
    """
    Nhiệm vụ:
      - Load bronze.orders + bronze.order_details.
      - Với các message kafka accept_rule có unlocked_by_accepted_suggestion=true:
          + Orders: tạo row MỚI (status=10, num_product += ...) rồi union bronze,
            dedupe để loại row status != 10 khi cùng id đã có row status=10.
          + Order_details: thêm 1 row / product trong accepted_product_ids với
            quantity=1, subtotal=UNIT_PRICE_MAP[product_id], is_suggestion=true.
      - Ghi silver partition year/month/day.
    """
    logger.info("[Silver] %s + order_details: bắt đầu", ten_bang_orders)
    orders_bronze = read_bronze_parquet(spark, ten_bang_orders)

    kafka_df = read_kafka_bronze_or_empty(spark)
    kafka_moi_nhat = kafka_latest_per_order(kafka_df)
    kafka_unlocked = kafka_moi_nhat.filter(F.col("unlocked_by_accepted_suggestion") == True)

    ts_ghi = current_timestamp()

    dong_orders_moi = build_orders_rows_from_kafka(kafka_unlocked, orders_bronze)
    orders_gop = orders_bronze.unionByName(dong_orders_moi, allowMissingColumns=True)
    orders_sau_dedupe = dedupe_orders_prefer_unlocked(orders_gop)

    key_orders = f"{tien_to_silver.strip('/')}/{ten_bang_orders}"
    orders_out = (
        orders_sau_dedupe.withColumn("year", year(ts_ghi))
        .withColumn("month", month(ts_ghi))
        .withColumn("day", dayofmonth(ts_ghi))
    )
    orders_out.write.partitionBy("year", "month", "day").mode("overwrite").format("parquet").save(
        f"s3a://{SILVER_BUCKET}/{key_orders}"
    )
    logger.info("[Silver] orders write overwrite done")

    dong_od_moi = build_order_details_rows_from_kafka(kafka_unlocked, ts_ghi)
    chi_tiet_bronze = read_bronze_order_details(spark)
    kafka_khong_rong = not dong_od_moi.rdd.isEmpty()

    if chi_tiet_bronze is not None and kafka_khong_rong:
        chi_tiet_full = chi_tiet_bronze.unionByName(dong_od_moi, allowMissingColumns=True)
    elif chi_tiet_bronze is not None:
        chi_tiet_full = chi_tiet_bronze
    elif kafka_khong_rong:
        chi_tiet_full = dong_od_moi
    else:
        chi_tiet_full = None

    if chi_tiet_full is None:
        logger.info("[Silver] skip write order_details (empty)")
        return

    key_od = f"{tien_to_silver.strip('/')}/order_details"
    chi_tiet_out = (
        chi_tiet_full.withColumn("year", year(ts_ghi))
        .withColumn("month", month(ts_ghi))
        .withColumn("day", dayofmonth(ts_ghi))
    )
    chi_tiet_out.write.partitionBy("year", "month", "day").mode("overwrite").format("parquet").save(
        f"s3a://{SILVER_BUCKET}/{key_od}"
    )
    logger.info("[Silver] order_details write overwrite done")


CAC_BANG_DIM = ("store", "product_category", "products", "payment_method", "customers")


def main():
    logger.info("[Silver] === START ===")
    spark = create_spark_session()
    try:
        tien_to = os.getenv("SILVER_WRITE_PREFIX", "silver")
        for ten_bang in CAC_BANG_DIM:
            write_dim_to_silver(spark, tien_to, ten_bang)
        write_silver_orders_and_details(spark, tien_to, "orders")
    finally:
        spark.stop()
    logger.info("[Silver] === DONE ===")


if __name__ == "__main__":
    main()
