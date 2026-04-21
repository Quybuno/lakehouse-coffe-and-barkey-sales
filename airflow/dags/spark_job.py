import os
from pathlib import Path

import airflow.utils.dates
from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

BASE_DIR = Path(os.environ.get("AIRFLOW_PROJECT_DIR", "/opt/airflow/project")).resolve()
SCRIPTS = BASE_DIR / "scripts" / "batch_layer"
JAR_MYSQL = str(BASE_DIR / "jars" / "mysql-connector-j-8.0.33.jar")

PKG_S3 = "org.apache.hadoop:hadoop-aws:3.3.1"
PKG_KAFKA = "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.5"
PKG_S3_KAFKA = f"{PKG_S3},{PKG_KAFKA}"

# Iceberg runtime cho Spark 3.5 + AWS bundle (S3FileIO dùng cho MinIO qua s3://).
ICEBERG_VER = "1.9.2"
PKG_ICEBERG = (
    f"org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:{ICEBERG_VER},"
    f"org.apache.iceberg:iceberg-aws-bundle:{ICEBERG_VER}"
)
PKG_S3_ICEBERG = f"{PKG_S3},{PKG_ICEBERG}"

# spark-submit subprocess must see MySQL creds (some setups strip env); copy from scheduler/worker.
_SPARK_ENV_MYSQL = {
    k: v
    for k in (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "MYSQL_ROOT_PASSWORD",
    )
    if (v := os.environ.get(k))
}

# Cùng lý do: MinIO + Iceberg REST cần env trong subprocess spark-submit.
_SPARK_ENV_DATALAKE = {
    k: v
    for k in (
        "MINIO_ENDPOINT",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    )
    if (v := os.environ.get(k))
}
_SPARK_ENV_DATALAKE.setdefault("ICEBERG_REST_URI", "http://iceberg-rest:8181")
_SPARK_ENV_DATALAKE.setdefault("ICEBERG_WAREHOUSE", "s3://warehouse/")
_SPARK_ENV_DATALAKE.setdefault("ICEBERG_CATALOG", "iceberg")
_SPARK_ENV_DATALAKE.setdefault("ICEBERG_NAMESPACE", "gold")

default_args = {
    "owner": "airflow",
    "start_date": airflow.utils.dates.days_ago(1),
}

common_conf = {
    "spark.driver.extraJavaOptions": "-Dlog4j.rootCategory=ERROR,console",
    "spark.executor.extraJavaOptions": "-Dlog4j.rootCategory=ERROR,console",
    "spark.pyspark.python": "/usr/bin/python3.11",
    "spark.executorEnv.PYSPARK_PYTHON": "/usr/bin/python3.11",
    # Pin cứng resource để vừa docker-compose limits (airflow-scheduler 2g / spark-worker 2g).
    # Không pin → Spark lấy default 1g driver nhưng JVM overhead có thể vượt container limit
    # → kernel OOM kill driver, Airflow thấy task "hang" (heartbeat mất).
    "spark.driver.memory": "1g",
    "spark.driver.memoryOverhead": "384m",
    "spark.executor.memory": "1500m",
    "spark.executor.memoryOverhead": "384m",
    "spark.executor.cores": "2",
    # Chỉ có 1 worker → giới hạn tổng core executor = 2 để tránh standalone scheduler
    # chờ tài nguyên không bao giờ có.
    "spark.cores.max": "2",
    # Dataset nhỏ (vài triệu row) → 200 partition default sinh quá nhiều small files
    # + overhead shuffle vô ích trên 1 executor. 8 partition vừa đủ song song.
    "spark.sql.shuffle.partitions": "8",
    "spark.default.parallelism": "8",
    # Giảm network timeout để fail nhanh thay vì treo vô hạn khi executor chết.
    "spark.network.timeout": "300s",
    "spark.executor.heartbeatInterval": "30s",
}

# Conf bổ sung chỉ cho task gold: khai báo Iceberg catalog qua REST (trùng với gold_layer.py).
iceberg_conf = {
    "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    "spark.sql.catalog.iceberg": "org.apache.iceberg.spark.SparkCatalog",
    "spark.sql.catalog.iceberg.catalog-impl": "org.apache.iceberg.rest.RESTCatalog",
    "spark.sql.catalog.iceberg.uri": "http://iceberg-rest:8181",
    "spark.sql.catalog.iceberg.warehouse": "s3://warehouse/",
    "spark.sql.catalog.iceberg.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
    "spark.sql.catalog.iceberg.s3.endpoint": "http://minio:9000",
    "spark.sql.catalog.iceberg.s3.path-style-access": "true",
    # AWS SDK v2 bắt buộc region — MinIO không dùng thật nhưng SDK sẽ throw
    # SdkClientException: Unable to load region nếu thiếu. us-east-1 là default an toàn.
    "spark.sql.catalog.iceberg.client.region": "us-east-1",
    "spark.sql.catalog.iceberg.s3.region": "us-east-1",
    "spark.sql.catalog.iceberg.s3.access-key-id": os.environ.get("MINIO_ROOT_USER", "minioadmin"),
    "spark.sql.catalog.iceberg.s3.secret-access-key": os.environ.get(
        "MINIO_ROOT_PASSWORD", "minioadmin"
    ),
    "spark.sql.defaultCatalog": "iceberg",
}

# SDK v2 đôi khi đọc AWS_REGION từ env (kể cả khi đã set qua catalog properties).
# Truyền vào subprocess spark-submit cho chắc.
_SPARK_ENV_DATALAKE["AWS_REGION"] = "us-east-1"
_SPARK_ENV_DATALAKE["AWS_DEFAULT_REGION"] = "us-east-1"

spark_submit_kw = {
    "conn_id": "spark",
    "deploy_mode": "client",
    "conf": common_conf,
    "jars": JAR_MYSQL,
    "env_vars": _SPARK_ENV_MYSQL,
}

spark_submit_kw_gold = {
    "conn_id": "spark",
    "deploy_mode": "client",
    "conf": {**common_conf, **iceberg_conf},
    "jars": JAR_MYSQL,
    "env_vars": {**_SPARK_ENV_MYSQL, **_SPARK_ENV_DATALAKE},
}

with DAG(
    dag_id="spark-batch-job",
    default_args=default_args,
    schedule_interval="@daily",
    catchup=False,
) as dag:
    bronze_layer_load = SparkSubmitOperator(
        task_id="bronze_layer_load",
        application=str(SCRIPTS / "bronze_raw.py"),
        packages=PKG_S3_KAFKA,
        **spark_submit_kw,
    )

    silver_layer_transform = SparkSubmitOperator(
        task_id="silver_layer_transform",
        application=str(SCRIPTS / "silver_layer.py"),
        packages=PKG_S3_KAFKA,
        **spark_submit_kw,
    )

    gold_layer_star_schema = SparkSubmitOperator(
        task_id="gold_layer_star_schema",
        application=str(SCRIPTS / "gold_layer.py"),
        packages=PKG_S3_ICEBERG,
        **spark_submit_kw_gold,
    )

    bronze_layer_load >> silver_layer_transform >> gold_layer_star_schema
