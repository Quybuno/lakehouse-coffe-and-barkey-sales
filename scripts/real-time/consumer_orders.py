import multiprocessing
from kafka_get import KafkaHandler
from Check import check_and_trigger,logger,redis_dynamic

def process_order(message,producer):
    order_payload = message.value.get('payload')['after']

    order_id = order_payload['id']

    if redis_dynamic.get(f'order_status:{order_id}') == 'checking':
        return
    
    # tao cai ro voi id la order_id       
    with redis_dynamic.pipeline() as pipe:
        try:
            pipe.hset(f'order_info:{order_id}',mapping={
                "customer_id":order_payload['customer_id'],
                "payment_method_id":order_payload['payment_method_id'],
                "num_product":order_payload['num_product'],
                "store_id":order_payload['store_id'],
            })
            # expire là đặt thời gian tồn tại cho order_info
            pipe.expire(f"order_info:{order_id}", 120)
            pipe.execute()
            
            check_and_trigger(order_id,producer)  
        except Exception as e:
            logger.error(f"[Worker Order Lỗi Process Order: {e}")
        finally:
            pipe.reset()

def order_worker(worker_id: int):
    kafka_client = KafkaHandler(["localhost:29092", "localhost:29093","localhost:29094"])
    producer = kafka_client.get_producer()
    consumer = kafka_client.get_consumer(
        topic="mysql.kd_bakery_coffee.orders",
        group_id=f"order_info_tracker_{worker_id}"
    )

    try:
        while True:
            try:
                message_pack = consumer.poll(timeout_ms=1000)
                for _, messages in message_pack.items():
                    for message in messages:
                        process_order(message, producer)
            except Exception as e:
                logger.error(f"[Worker Order {worker_id}] Lỗi Polling: {e}")
    finally:
        producer.flush()
        producer.close()
        consumer.close()
        logger.info(f"[Worker Order {worker_id}] Đã đóng consumer và producer") 

if __name__ == "__main__":
    # Bật 3 process để chạy song song cho topic orders
    processes = [multiprocessing.Process(target=order_worker, args=(i,)) for i in range(3)]
    for p in processes: p.start()
    for p in processes: p.join()            
            