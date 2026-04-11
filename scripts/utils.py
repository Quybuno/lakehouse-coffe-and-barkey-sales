import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv_path = BASE_DIR / ".env"
load_dotenv(load_dotenv_path)
def get_mysql_config():
    return {
        "user":os.getenv("MYSQL_USER"),
        "password":os.getenv("MYSQL_PASSWORD"),
        "host":os.getenv("MYSQL_HOST"),
        # "port": os.getenv("MYSQL_PORT"),
        "database":os.getenv("MYSQL_DATABASE")
    }


def check_minio_has_data(spark, bucket: str, key_prefix: str) -> bool:
    """True nếu s3a://bucket/<prefix> tồn tại và có ít nhất một entry (dùng bronze incremental)."""
    prefix = (key_prefix or "").strip().strip("/")
    base = f"s3a://{bucket}/{prefix}" if prefix else f"s3a://{bucket}"
    jvm = spark._jvm
    uri = jvm.java.net.URI(base)
    conf = spark.sparkContext._jsc.hadoopConfiguration()
    fs = jvm.org.apache.hadoop.fs.FileSystem.get(uri, conf)
    p = jvm.org.apache.hadoop.fs.Path(base)
    if not fs.exists(p):
        return False
    st = fs.getFileStatus(p)
    if st.isFile():
        return True
    return len(fs.listStatus(p)) > 0