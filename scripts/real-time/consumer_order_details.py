import multiprocessing
from datetime import datetime
import json
from kafka_get import KafkaHandler
from Check import logger, redis_dynamic, check_and_trigger


def process_detail_message(message, producer):
    detail_payload = message.value.get("payload", {}).get("after")
    if not detail_payload:
        return

    order_id = detail_payload["order_id"]
    # product_id là id của sản phẩm
    product_id = detail_payload["product_id"]
    is_suggestion = bool(detail_payload.get("is_suggestion", False))
    
    subtotal = int(detail_payload.get("subtotal", 0) or 0)
    quantity = int(detail_payload.get("quantity", 0) or 0)

    if redis_dynamic.get(f"order_status:{order_id}") == "checking":
        return
    
    # Lưu từng món vào Redis List (rpush) để hỗ trợ khách mua nhiều ly giống nhau
    with redis_dynamic.pipeline() as pipe:
        pipe.rpush(
            f"products:{order_id}",
            json.dumps(
                {
                    "product_id": product_id,
                    "subtotal": subtotal,
                    "quantity": quantity,
                },
                ensure_ascii=False,
            ),
        )
        pipe.expire(f'products:{order_id}', 120)
        pipe.execute()

    check_and_trigger(order_id, producer)

def detail_worker(worker_id: int):
    kafka_client = KafkaHandler(["localhost:29092", "localhost:29093"])
    producer = kafka_client.get_producer()
    consumer = kafka_client.get_consumer(
        topic="mysql.kd_bakery_coffee.order_details",
        group_id=f"order_details_tracker_{worker_id}" # Group ID phải ĐỘC LẬP với file order
    )

    try:
        while True:
            try:
                message_pack = consumer.poll(timeout_ms=1000)
                for _, messages in message_pack.items():
                    for message in messages:
                        process_detail_message(message, producer)
            except Exception as e:
                logger.error(f"[Worker Detail {worker_id}] Lỗi Polling: {e}")
    finally:
        producer.flush()
        producer.close()
        consumer.close()

if __name__ == "__main__":
    # Thường order_details sẽ nhiều dữ liệu hơn orders, nên bật 3-4 process
    processes = [multiprocessing.Process(target=detail_worker, args=(i,)) for i in range(3)]
    for p in processes: p.start()
    for p in processes: p.join()