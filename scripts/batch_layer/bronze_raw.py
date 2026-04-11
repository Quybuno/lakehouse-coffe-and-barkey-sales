import os
import sys
from pathlib import Path
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from utils import check_minio_has_data
from dotenv import load_dotenv
import logging

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env")
LOG_FILE = BASE_DIR / "logs" / "batch.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename=LOG_FILE,
)
logger = logging.getLogger(__name__)

BUCKET = "bronze-raw"

def create_spark_session():
    return SparkSession.builder \
        .appName("Bronze Raw") \
        .config("spark.hadoop.fs.s3a.endpoint", os.getenv("MINIO_ENDPOINT", "minio:9000")) \
        .config("spark.hadoop.fs.s3a.access.key", os.getenv("MINIO_ROOT_USER")) \
        .config("spark.hadoop.fs.s3a.secret.key", os.getenv("MINIO_ROOT_PASSWORD")) \
        .config("spark.hadoop.fs.s3a.path.style.access", "true") \
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
        .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse")) \
        .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir")) \
        .getOrCreate()

def read_mysql_tables(spark: SparkSession, table: str):
    return spark.read.format("jdbc").option("url", os.getenv("MYSQL_URL")) \
        .option("driver", "com.mysql.cj.jdbc.Driver") \
        .option("user", os.getenv("MYSQL_USER")) \
        .option("password", os.getenv("MYSQL_PASSWORD")) \
        .option("dbtable", table) \
        .load()

def incremental_load_dim_tables(spark: SparkSession, table_name: str,time_col = "updated_at") ->None:
    bucket_path = f"s3a://{BUCKET}/{table_name}"
    try:
        logger.info(f"[Bronze Raw]{table_name} : start_processing")
        source_data = read_mysql_tables(spark, table_name)

        # nếu có rồi thi lấy time gần nhất new_data sẽ lấy tiếp dữ liệu từ time đó 
        if check_minio_has_data(spark, BUCKET, f"{table_name}"):
            logger.info(f"[Bronze Raw]{table_name} : data already exists")
            existing_data = spark.read.parquet(f"s3a://{BUCKET}/{table_name}")
            last_time  = existing_data.select(max(time_col)).collect()[0][0]
            new_data = source_data.filter(col(time_col) > last_time)

        else:
            logger.info(f"[Bronze Raw]{table_name} : data does not exist -> load full data")
            new_data = source_data

        if new_data.rdd.isEmpty():
            logger.info(f"[Bronze Raw]{table_name} : no new data -> skip")

        else:
            output_df = new_data.withColumn("year", year(current_timestamp())) \
                .withColumn("month", month(current_timestamp())) \
                .withColumn("day", day(current_timestamp())) \

            logger.info(f"[Bronze Raw]{table_name} : new data count: {new_data.count()}")

            output_df.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(f"s3a://{BUCKET}/{table_name}")
            logger.info(f"[Bronze Raw]{table_name} : data saved")

    except Exception as e:
        logger.error(f"[Bronze Raw]{table_name} : error: {e}")
        raise e

def incremental_load_order_details_tables(spark: SparkSession):
    try:
        logger.info(f"[Bronze Raw]orders and order_details : start_processing")
        orders = read_mysql_tables(spark, "orders")
        order_details = read_mysql_tables(spark, "order_details")    

        if check_minio_has_data(spark, BUCKET, f"orders"):
            logger.info(f"[Bronze Raw]orders : data already exists")
            existing_orders = spark.read.parquet(f"s3a://{BUCKET}/orders")
            last_time = existing_orders.select(max("updated_at")).collect()[0][0]
            new_orders = orders.filter(col("updated_at") > last_time)

        else:
            logger.info(f"[Bronze Raw]orders : data does not exist -> load full data")
            new_orders = orders
        if new_orders.rdd.isEmpty():
            logger.info(f"[Bronze Raw]orders : no new data -> skip")
            return
        
        logger.info(f"[BRONZE][orders] Found new records. Preparing to write...")
        enriched_orders = new_orders.withColumn("year", year(current_timestamp())) \
            .withColumn("month", month(current_timestamp())) \
            .withColumn("day", day(current_timestamp())) \
            .withColumn("hour", hour(current_timestamp())) \
        join_orders_and_order_details = enriched_orders.join(order_details, enriched_orders["id"] == order_details["order_id"],"inner") \
        .select(
            "order_id",
            "product_id",
            "quantity",
            "discount_percent",
            "subtotal",
            "is_suggestion",
            "updated_at",
            "year",
            "month",
            "day"
        ) 
  
        logger.info(f"writing orders and order_details to minio")
        enriched_orders.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(f"s3a://{BUCKET}/orders")
        join_orders_and_order_details.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(f"s3a://{BUCKET}/order_details")
        logger.info(f"[Bronze Raw]orders and order_details : data saved")
                
    except Exception as e:
        logger.error(f"[Bronze Raw]orders and order_details : error: {e}")
        raise e


def incremental_load_kafka_topic(spark: SparkSession):

    try:
        logger.info(f"[Bronze Raw]kafka_topic : start_processing")
        bootstrap_servers = ["kafka-1:9092", "kafka-2:9092", "kafka-3:9092"]
        bootstrap_servers_str = ",".join(bootstrap_servers)
        
        orders_accept_rule = spark.read \
                            .format("kafka") \
                            .option("kafka.bootstrap.servers", bootstrap_servers_str) \
                            .option("subscribe", "orders_accept_rule") \
                            .option("startingOffsets", "earliest") \
                            .load()
    
        schema = StructType([
            StructField("order_id", StringType(), True),
            StructField("store_id", StringType(), True),
            StructField("customer_id", StringType(), True),
            StructField("payment_method_id", StringType(), True),
            StructField("accepted_product_ids", ArrayType(StringType()), True),
            StructField("is_suggestion", BooleanType(), True),
            StructField("unlocked_by_accepted_suggestion", BooleanType(), True),
        ])


        orders_accept_rule = orders_accept_rule.selectExpr("CAST(value AS string) as json")
        orders_accept_rule = orders_accept_rule.select(from_json(col("json"), schema).alias("data"))
        orders_accept_rule = orders_accept_rule.select("data.*")
        
        orders_accept_rule = orders_accept_rule.withColumn("year", year(current_timestamp()))
        orders_accept_rule = orders_accept_rule.withColumn("month", month(current_timestamp()))
        orders_accept_rule = orders_accept_rule.withColumn("day", day(current_timestamp()))
        orders_accept_rule.write.partitionBy("year", "month", "day").mode("append").format("parquet").save(
            f"s3a://{BUCKET}/bronze/raw/kafka/orders_accept_rule"
        )
        logger.info("[Bronze Raw]orders_accept_rule : data saved")
    except Exception as e:
        logger.error(f"[Bronze Raw]orders_accept_rule : error: {e}")
        raise

        
def main():
    logger.info(f"[Bronze Raw] : start_processing")
    
    try:
        spark = create_spark_session()
        dimension_tables = ["store", "product_category", "products", "payment_method", "customers"]
        for table in dimension_tables:
            incremental_load_dim_tables(spark, table)
        incremental_load_orders_and_order_details(spark)
        incremental_load_kafka_topic(spark)
        spark.stop()
        logger.info(f"[Bronze Raw] : end_processing")
    except Exception as e:
        sys.exit(1)

if __name__ == "__main__":
    main()