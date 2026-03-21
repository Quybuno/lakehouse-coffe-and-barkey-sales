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
        "port": os.getenv("MYSQL_PORT"),
        "database":os.getenv("MYSQL_DATABASE")
    }

