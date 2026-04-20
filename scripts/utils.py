import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv_path = BASE_DIR / ".env"
load_dotenv(load_dotenv_path)


def _running_inside_container() -> bool:
    return Path("/.dockerenv").exists()


def get_mysql_config():
    """Kwargs for mysql.connector. ``MYSQL_HOST=mysql`` chỉ resolve trong Docker; từ máy host dùng 127.0.0.1."""
    host = os.getenv("MYSQL_HOST")
    if host is not None:
        host = host.strip()
    skip = os.getenv("MYSQL_SKIP_DOCKER_HOST_REMAP", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if (
        host
        and host.casefold() == "mysql"
        and not skip
        and not _running_inside_container()
    ):
        host = os.getenv("MYSQL_LOCAL_HOST", "127.0.0.1")
    try:
        port = int((os.getenv("MYSQL_PORT") or "3306").strip())
    except ValueError:
        port = 3306
    return {
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "host": host,
        "port": port,
        "database": os.getenv("MYSQL_DATABASE"),
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