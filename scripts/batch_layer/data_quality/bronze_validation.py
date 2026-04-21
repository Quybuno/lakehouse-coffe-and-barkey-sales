import json
import sys
from pathlib import Path
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
import os
import dotenv

dotenv.load_dotenv()


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.append(str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)    

s3_access_key = os.getenv("MINIO_ROOT_USER")
s3_secret_key = os.getenv("MINIO_ROOT_PASSWORD")


spark = SparkSession.builder \
    .appName("Bronze Layer Data Quality Check") \
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000") \
    .config("spark.hadoop.fs.s3a.access.key", s3_access_key) \
    .config("spark.hadoop.fs.s3a.secret.key", s3_secret_key) \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem") \
    .config("spark.sql.warehouse.dir", str(BASE_DIR / "tmp" / "sql_warehouse")) \
    .config("spark.local.dir", str(BASE_DIR / "tmp" / "local_dir")) \
    .getOrCreate()

# Schema file location
SCHEMA_FILE = Path(__file__).resolve().parent / "bronze_schema.json"


def read_schema_file(): 
    with open(SCHEMA_FILE, "r") as f:
        schema = json.load(f)
    return schema


def validate_schema(df,table_name:str,schema_store:dict): ->bool:
    current_schema = json.loads(df.schema.json())
    expected_schema = schema_store[table_name]

    if current_schema != expected_schema:
        logger.warning(f"[{table_name}] Schema mismatch detected.")
        logger.debug(f"[{table_name}] Expected schema: {json.dumps(expected_schema, indent=2)}")
        logger.debug(f"[{table_name}] Current schema : {json.dumps(current_schema, indent=2)}")
        return False

    logger.info(f"[{table_name}] Schema check passed.")
    return True

def check_data_quality(df,table_name:str,null_cols[],unique_cols[]): ->bool:
    check = False
    for col in null_cols:
        if df.filter(col(col).isNull()).count() > 0:
            logger.warning(f"[{table_name}] Null value detected in column {col}.")
            check = True
    for col in unique_cols:
        if df.filter(col(col).distinct().count() != df.count()):
            logger.warning(f"[{table_name}] Duplicate value detected in column {col}.")
            check = True
    return check
