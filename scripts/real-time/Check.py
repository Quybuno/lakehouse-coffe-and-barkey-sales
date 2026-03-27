import sys
from pathlib import Path
import logging

import redis
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',filename=BASE_DIR / 'logs' / 'test.log')
logger = logging.getLogger(__name__)

redis_dynamic = redis.Redis(host='localhost', port=6379, db=1,decode_responses=True)
def check_and_trigger(order_id,producer):
    
    status_key = f'order_status:{order_id}'
    info_key = f'order_info:{order_id}'
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

            num_product = int(order_info.get('num_product',0))

            current_product = pipe.lrange(products_id,0,-1)
            
            if (len(current_product) == num_product) and (num_product > 0):
                pipe.multi()
                pipe.set(status_key,'checking')
                pipe.delete(info_key)
                pipe.delete(products_id)

                pipe.execute()

                producer.send('order_ready_for_checking',{
                    'order_id':order_id,
                    'status':'checking',
                    'store_id':order_info.get('store_id'),
                    'customer_id':order_info['customer_id'],
                    'payment_method_id':order_info['payment_method_id'],
                    'num_product':num_product,
                    'product_ids': current_product,
                })
                logging.info(f'Đã gom đủ {num_product} sản phẩm cho đơn {order_id} ')
            else:
                logger.debug(f'Đơn {order_id} chưa đủ {num_product} sản phẩm')
        except redis.exceptions.WatchError:
            logging.debug(f'Đơn {order_id} đã bị thay đổi trong khi xử lý')
        except Exception as e:
            logging.error(f'Lỗi khi xử lý đơn {order_id}: {e}')
        finally:
            pipe.reset()



