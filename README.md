# KD Bakery & Coffee — Sales Platform

Data platform end-to-end cho chuỗi Bakery & Coffee:

```
MySQL ──► Debezium CDC ──► Kafka ──► Consumers + Redis (real-time gợi ý)
   │
   └──► Spark bronze ──► Spark silver ──► Spark gold (Iceberg) ──► MinIO
                                                    │
                                             iceberg-rest (catalog)
                                                    │
                                              Trino (iceberg)
                                                    │
                                               Streamlit
```

Ba luồng chính (làm theo thứ tự dưới đây):

1. **Setup môi trường** — Python venv + Docker stack.
2. **Real-time** — sinh đơn giả, Debezium bắt CDC, consumer đẩy gợi ý về Kafka.
3. **Batch + Dashboard** — Airflow chạy DAG Spark (bronze → silver → gold Iceberg), Trino query, Streamlit render.

---

## 0. Chuẩn bị môi trường

### 0.1 Python venv

Windows (PowerShell / cmd):

```powershell
python -m venv venv
venv\Scripts\Activate
```

Linux / WSL:

```bash
sudo apt update
sudo apt install python-is-python3 python3-venv
python3 -m venv venv
source venv/bin/activate
```

Cài dependency cho cả real-time scripts + dashboard:

```bash
pip install -r requirement.txt
```

### 0.2 File `.env`

Copy/tạo `.env` ở root với các biến tối thiểu:

```env
# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=Ngocquy2501@
MYSQL_DATABASE=kd_bakery_coffee
MYSQL_ROOT_PASSWORD=Ngocquy2501@

# MinIO
MINIO_ENDPOINT=http://minio:9000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

# Iceberg / Trino
ICEBERG_REST_URI=http://iceberg-rest:8181
ICEBERG_WAREHOUSE=s3://warehouse/
ICEBERG_CATALOG=iceberg
ICEBERG_NAMESPACE=gold
AWS_REGION=us-east-1
```

> Nếu đổi `MYSQL_PASSWORD`, nhớ sửa luôn `scripts/real-time/mysql_debezium_connector.json` (field `database.password`).

### 0.3 Khởi động stack Docker

Bật toàn bộ (cách nhanh nhất cho demo):

```bash
docker compose up -d
```

Hoặc bật theo nhóm khi chỉ cần 1 luồng:

```bash
# Real-time stack
docker compose up -d mysql kafka-1 kafka-2 kafka-3 init-kafka kafka-ui connect redis

# Batch + analytics stack
docker compose up -d postgres airflow-init airflow-scheduler airflow-webserver \
                    spark-master spark-worker \
                    minio minio-init iceberg-rest trino streamlit
```

Chờ healthcheck xanh:

```bash
docker compose ps
```

### 0.4 Cổng truy cập

| Service           | URL                       | Credentials              |
| ----------------- | ------------------------- | ------------------------ |
| Adminer (MySQL)   | http://localhost:8080     | Server `mysql` / root    |
| Kafka UI          | http://localhost:8000     | —                        |
| Kafka Connect API | http://localhost:8083     | —                        |
| Redis Insight     | http://localhost:8001     | —                        |
| MinIO Console     | http://localhost:9001     | `minioadmin/minioadmin`  |
| Iceberg REST      | http://localhost:8181     | — (`/v1/config`)         |
| Trino UI          | http://localhost:8090     | user `dashboard`         |
| Spark Master UI   | http://localhost:9090     | —                        |
| Airflow           | http://localhost:8088     | `airflow/airflow`        |
| Streamlit         | http://localhost:8501     | —                        |

---

## 1. Luồng real-time (CDC → Kafka → Redis → gợi ý)

### 1.1 Tạo schema + nạp master data vào MySQL

```bash
python scripts/database/create_table.py        # tạo bảng store/products/customers/…
python scripts/database/load_data_static.py    # LOAD DATA từ data/*.csv vào MySQL
python scripts/database/load_redis_static.py   # đẩy tier + cặp đồng mua vào Redis
```

Kiểm tra nhanh ở Adminer (http://localhost:8080, database `kd_bakery_coffee`)
thấy các bảng `store`, `customers`, `payment_method`, `product_category`,
`products` đã có data.

### 1.2 Đăng ký Debezium MySQL connector

`kafka-connect` đã được bật từ `docker compose up -d`. Register CDC connector:

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @scripts/real-time/mysql_debezium_connector.json
```

Verify:

```bash
curl http://localhost:8083/connectors/mysql_debezium_connector/status
```

Status `RUNNING` → Debezium sẽ phát CDC events lên topic
`mysql.kd_bakery_coffee.orders` và `mysql.kd_bakery_coffee.order_details`.

### 1.3 Bật consumer + engine gợi ý

Mở 3 terminal (hoặc dùng `tmux` / `screen`), activate venv ở từng cái:

```bash
# Terminal 1 — theo dõi event orders
python scripts/real-time/consumer_orders.py

# Terminal 2 — theo dõi event order_details
python scripts/real-time/consumer_order_details.py

# Terminal 3 — rule engine discount + gợi ý (R1/R2/R3)
python scripts/real-time/order_ready_for_rcm.py
```

Ba process này hoạt động như sau:

- `consumer_orders.py` và `consumer_order_details.py` lắng nghe 2 topic CDC,
  gom `order_info + products` vào Redis (`order_info:{id}`, `products:{id}`),
  và khi đủ điều kiện (số dòng detail khớp `num_product`) sẽ `check_and_trigger`
  publish event sang topic `order_ready_for_rcm`.
- `order_ready_for_rcm.py` tiêu thụ event đó, áp rule discount (A1/A2) + gợi ý
  sản phẩm (R1/R2/R3) dựa vào Redis (tier, cặp đồng mua `copurchase:{id}`).

### 1.4 Sinh đơn hàng giả

```bash
python scripts/database/generate_data.py
```

Script này insert `orders` + `order_details` liên tục vào MySQL. Debezium bắt
CDC → Kafka → consumer → Redis → gợi ý. Theo dõi luồng real-time:

- Kafka UI (http://localhost:8000) xem message đi qua các topic.
- Redis Insight (http://localhost:8001) xem key `order_info:*`, `products:*`.
- Log stdout của 3 terminal consumer để thấy rule nào được kích.

### 1.5 Dừng real-time

`Ctrl+C` ở từng terminal consumer. Không cần dừng `generate_data.py` nếu muốn
giữ dữ liệu chảy liên tục.

---

## 2. Luồng batch (Spark → Iceberg)

Batch layer đọc dữ liệu MySQL + Kafka đã tích lũy, xây bronze → silver → gold
(Iceberg) rồi lưu lên MinIO. Chạy qua Airflow DAG `spark-batch-job`.

### 2.1 Kích DAG qua Airflow UI

1. Mở http://localhost:8088 (login `airflow/airflow`).
2. Tìm DAG **`spark-batch-job`**.
3. Un-pause (toggle bên trái) → Trigger DAG (nút ▶ phía phải).

DAG có 3 task chạy tuần tự:

```
bronze_layer_load  ──►  silver_layer_transform  ──►  gold_layer_star_schema
```

| Task                        | Script                                   | Output                                        |
| --------------------------- | ---------------------------------------- | --------------------------------------------- |
| `bronze_layer_load`         | `scripts/batch_layer/bronze_raw.py`      | `s3a://bronze-raw/{store,customers,orders,…}` (parquet) |
| `silver_layer_transform`    | `scripts/batch_layer/silver_layer.py`    | `s3a://silver/silver/{orders,order_details}` (parquet) |
| `gold_layer_star_schema`    | `scripts/batch_layer/gold_layer.py`      | `iceberg.gold.{dim_*,fact_orders}` (Iceberg)   |

### 2.2 Theo dõi tiến độ

- Airflow UI → click task → **Logs** để xem output của `spark-submit`.
- Spark Master UI (http://localhost:9090) xem executor + stage runtime.
- MinIO Console (http://localhost:9001) kiểm tra bucket `bronze-raw`,
  `silver`, `warehouse` có dữ liệu.

### 2.3 (Tuỳ chọn) Chạy script batch thủ công

Để debug 1 task mà không qua Airflow:

```bash
docker exec -it spark-master /opt/bitnami/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.9.2,\
org.apache.iceberg:iceberg-aws-bundle:1.9.2,\
org.apache.hadoop:hadoop-aws:3.3.1 \
  /opt/airflow/project/scripts/batch_layer/gold_layer.py
```

(Đường dẫn trong container đã mount sẵn project.)

### 2.4 Verify kết quả gold layer

```bash
docker exec -it trino trino --execute "SHOW TABLES IN iceberg.gold"
docker exec -it trino trino --execute "SELECT COUNT(*) FROM iceberg.gold.fact_orders"
docker exec -it trino trino --execute \
  "SELECT order_date, SUM(subtotal) AS doanh_thu
   FROM iceberg.gold.fact_orders GROUP BY 1 ORDER BY 1 DESC LIMIT 10"
```

Nếu thấy đủ 5 bảng (`dim_store`, `dim_customers`, `dim_payment`, `dim_products`,
`fact_orders`) và `COUNT(*) > 0` → sẵn sàng cho dashboard.

---

## 3. Dashboard (Trino + Streamlit)

### 3.1 Mở dashboard

Streamlit đã chạy sẵn ở http://localhost:8501.

Sidebar gồm:

- **Banner trạng thái** — gọi `SELECT 1` qua Trino; đỏ nếu mất kết nối.
- **Khoảng ngày** — tự detect min/max từ `fact_orders`.
- **Cửa hàng** — multiselect từ `dim_store`.
- **🔄 Refresh cache** — xoá cache 5 phút của `@st.cache_data`.

6 tab phân tích:

| Tab              | Nội dung chính                                                            |
| ---------------- | ------------------------------------------------------------------------- |
| 📊 Tổng quan     | KPI PoP + xu hướng doanh thu (MA7) + donut cửa hàng + insight tự sinh    |
| 🛍️ Sản phẩm      | Pareto 80/20 + Top/Bottom + matrix Giá × Sản lượng                         |
| 🏪 Cửa hàng      | Leaderboard + bar DT + scatter Traffic × AOV                              |
| 👥 Khách hàng    | Tier mix + Top 10 VIP + histogram tần suất mua                             |
| ⏰ Hành vi mua   | DT theo thứ + theo giờ + heatmap Thứ×Giờ + phương thức thanh toán          |
| ✨ Gợi ý         | KPI tác động gợi ý + % DT theo ngày/giờ + top SP gợi ý + breakdown store |

Chi tiết query & flow: xem **[`doc/TRINO_GOLD_DASHBOARD_FLOW.md`](doc/TRINO_GOLD_DASHBOARD_FLOW.md)**.

### 3.2 Dev dashboard ngoài Docker

```bash
source venv/bin/activate

export TRINO_HOST=localhost
export TRINO_PORT=8090       # port host mapping ra ngoài
export TRINO_CATALOG=iceberg
export TRINO_SCHEMA=gold

streamlit run scripts/dashboard/app.py
```

### 3.3 Refresh dashboard sau mỗi lần chạy batch

`@st.cache_data` TTL = 5 phút. Sau khi DAG chạy xong, bấm nút **🔄 Refresh
cache** ở sidebar để query lại ngay, không cần chờ hết TTL.

---

## 4. Tóm tắt thứ tự lệnh (quick cheat-sheet)

```bash
# === Setup 1 lần ===
python3 -m venv venv && source venv/bin/activate
pip install -r requirement.txt
docker compose up -d

# === Real-time ===
python scripts/database/create_table.py
python scripts/database/load_data_static.py
python scripts/database/load_redis_static.py

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @scripts/real-time/mysql_debezium_connector.json

# 3 terminal:
python scripts/real-time/consumer_orders.py
python scripts/real-time/consumer_order_details.py
python scripts/real-time/order_ready_for_rcm.py

# Terminal 4 — sinh đơn:
python scripts/database/generate_data.py

# === Batch ===
# Airflow UI → DAG spark-batch-job → Trigger
# Hoặc: docker exec airflow-scheduler airflow dags trigger spark-batch-job

# === Dashboard ===
# Mở http://localhost:8501
```

---

## 5. Tài liệu chi tiết thêm

- `doc/TRINO_GOLD_DASHBOARD_FLOW.md` — luồng Trino → gold layer + mọi query
  tạo nên Streamlit dashboard.
- `doc/TRINO_STREAMLIT_DASHBOARD.md` — hướng dẫn run + troubleshoot dashboard.
- `doc/REALTIME_PROMO_RECOMMEND_DESIGN.md` — thiết kế rule discount + gợi ý.
- `doc/KAFKA_DOCKER_PRODUCTION.md` — note tinh chỉnh Kafka cluster.
- `doc/kafka-docker-sua-loi.md` — các lỗi Kafka hay gặp + cách fix.
- `doc/ui.md` — ghi chú về UI dashboard.

---

## 6. Troubleshoot nhanh

| Triệu chứng                                                     | Xử lý                                                                                                         |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Connector Debezium báo `AccessDenied` khi đọc binlog            | MySQL chưa bật binlog ROW — check `docker exec mysql mysql -e "SHOW VARIABLES LIKE 'log_bin'"`                |
| Consumer không nhận message                                     | Kafka topic chưa có data — vào Kafka UI xem topic `mysql.kd_bakery_coffee.*`, hoặc chạy lại `generate_data.py` |
| DAG Spark fail ở task gold với `SdkClientException: region`     | `.env` thiếu `AWS_REGION=us-east-1` → export lại và restart scheduler                                         |
| Streamlit báo "Connection error"                                | `docker compose restart trino` rồi chờ healthcheck xanh; bấm Refresh cache                                    |
| Trino báo `TABLE_NOT_FOUND iceberg.gold.fact_orders`            | DAG gold chưa chạy xong → trigger lại DAG                                                                     |
| MinIO bucket `warehouse` / `bronze-raw` không tồn tại           | `docker compose up -d minio-init` (service này tạo bucket lần đầu)                                            |

Lỗi chi tiết hơn: xem `doc/TRINO_STREAMLIT_DASHBOARD.md` mục Troubleshoot.
