import sys
from pathlib import Path
import logging
import json

import redis
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_dynamic = redis.Redis(host='localhost', port=6379, db=1,decode_responses=True)
def check_and_trigger(order_id,producer):
    # là trang thai checking
    status_key = f'order_status:{order_id}'
    # infor_key là key để lưu thông tin của đơn hàng
    info_key = f'order_info:{order_id}'
    # products_id là key để lưu danh sách sản phẩm trong 1 order
    products_id = f'products:{order_id}'

    with redis_dynamic.pipeline() as pipe:
        try:
            pipe.watch(status_key,info_key,products_id)
            if(pipe.get(status_key) == 'checking'):
                return
            
            order_info = pipe.hgetall(info_key)
            if not order_info:
                logging.debug(f'Order {order_id} not found')
                return

            # lấy tổng số sản phẩm trong 1 order
            num_product = int(order_info.get('num_product',0))
            # lấy số lượng sản phẩm đang có trong 1 order
            current_product = pipe.lrange(products_id,0,-1)

            if (len(current_product) == num_product) and (num_product > 0):

                # tinh tong gia tri don hang
                # tao ra list product_ids tu o
                product_ids = [json.loads(raw).get("product_id") for raw in current_product if raw]
                total_price = sum(int(json.loads(raw).get("subtotal", 0) or 0) for raw in current_product if raw)
                quantity = sum(int(json.loads(raw).get("quantity", 0) or 0) for raw in current_product if raw)
                # total_price = 0
                # product_ids: list[str] = []
                # for raw in current_product:
                #     if not raw:
                #         continue
                #     try:
                #         item = json.loads(raw)
                #     except Exception:
                #         # nếu list chỉ chứa product_id string
                #         product_ids.append(str(raw))
                #         continue

                #     pid = item.get("product_id")
                #     if pid is not None:
                #         product_ids.append(str(pid))

                #     try:
                #         total_price += int(item.get("subtotal", 0) or 0)
                #     except Exception:
                #         pass

                pipe.multi()
                pipe.set(status_key,'checking')
                pipe.delete(info_key)
                pipe.delete(products_id)
                pipe.execute()

                producer.send(
                    'rule_topic',
                    {
                        'order_id': order_id,
                        'status': 'checking',
                        'store_id': order_info.get('store_id'),
                        'customer_id': order_info['customer_id'],
                        'payment_method_id': order_info.get('payment_method_id'),
                        'num_product': num_product,
                        'total_price': total_price,
                        'product_ids': product_ids,
                        'quantity': quantity,
                    },
                )
                
                # producer.send(
                #     'rule_recommendation',
                #     {
                #         'order_id': order_id,
                #         'product_ids': product_ids,
                #         'quantity': quantity,
                #     },
                # )

                
                logging.info(f'Đã gom đủ {num_product} sản phẩm cho đơn {order_id} ')
            else:
                logger.debug(f'Đơn {order_id} chưa đủ {num_product} sản phẩm')
        except redis.exceptions.WatchError:
            logging.debug(f'Đơn {order_id} đã bị thay đổi trong khi xử lý')
        except Exception as e:
            logging.error(f'Lỗi khi xử lý đơn {order_id}: {e}')
        finally:
            pipe.reset()



