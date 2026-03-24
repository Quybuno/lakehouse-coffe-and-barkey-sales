from kafka import KafkaConsumer
import json

# Khởi tạo consumer
consumer = KafkaConsumer(
    'mysql.kd_bakery_coffee.order_details',          # Topic muốn đọc
    bootstrap_servers=[
        "localhost:29092",
        "localhost:29093",
        "localhost:29094",
    ],
    group_id='1',                    # Consumer group (tự đặt)
    auto_offset_reset='earliest',             # Đọc từ đầu nếu chưa có offset
    enable_auto_commit=True,                  # Tự động commit offset
    value_deserializer=lambda m: json.loads(m.decode('utf-8'))  # Parse JSON
)

print("Đang lắng nghe topic orders...")
for message in consumer:
    # message.value đã là dict JSON từ Debezium
    data = message.value
    print(f"Received: {data}")
    # Ở đây bạn có thể xử lý logic: lấy order mới, lookup thông tin, gửi đề xuất...