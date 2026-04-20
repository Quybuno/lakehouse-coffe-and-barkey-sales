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

default_args = {
    "owner": "airflow",
    "start_date": airflow.utils.dates.days_ago(1),
}

common_conf = {
    "spark.driver.extraJavaOptions": "-Dlog4j.rootCategory=ERROR,console",
    "spark.executor.extraJavaOptions": "-Dlog4j.rootCategory=ERROR,console",
    "spark.pyspark.python": "/usr/bin/python3.11",
    "spark.executorEnv.PYSPARK_PYTHON": "/usr/bin/python3.11",
}

spark_submit_kw = {
    "conn_id": "spark",
    "deploy_mode": "client",
    "conf": common_conf,
    "jars": JAR_MYSQL,
    "env_vars": _SPARK_ENV_MYSQL,
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

    # bronze_data_quality_check = SparkSubmitOperator(
    #     task_id="bronze_data_quality_check",
    #     application=str(SCRIPTS / "data_quality" / "bronze_validation.py"),
    #     packages=PKG_S3,
    #     **spark_submit_kw,
    # )

    silver_layer_transform = SparkSubmitOperator(
        task_id="silver_layer_transform",
        application=str(SCRIPTS / "silver_layer.py"),
        packages=PKG_S3_KAFKA,
        **spark_submit_kw,
    )

    gold_layer_star_schema = SparkSubmitOperator(
        task_id="gold_layer_star_schema",
        application=str(SCRIPTS / "gold_layer.py"),
        packages=PKG_S3,
        **spark_submit_kw,
    )

    (
        bronze_layer_load >> silver_layer_transform >> gold_layer_star_schema

       
    )
    