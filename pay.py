import urllib.parse
import hashlib
import requests
import logging
import os
import json
import time
ORDER_TIMEOUT = 300
def load_all_orders():
    file_path = 'orders.json'
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_all_orders(data):
    file_path = 'orders.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def add_order(order_id, user_id, chat_id, created_time):
    orders = load_all_orders()
    orders[order_id] = {
        'user_id': user_id,
        'chat_id': chat_id,
        'created_time': created_time,
        'status': 'pending'
    }
    save_all_orders(orders)
    logging.info(f"订单添加: {order_id}, 用户: {user_id}")

def remove_order(order_id):
    orders = load_all_orders()
    if order_id in orders:
        del orders[order_id]
        save_all_orders(orders)
        logging.info(f"订单删除: {order_id}")
        return True
    return False

def get_order(order_id):
    orders = load_all_orders()
    return orders.get(order_id)

def cleanup_expired_orders():
    orders = load_all_orders()
    current_time = time.time()
    expired_orders = []
    
    for order_id, order_data in orders.items():
        if current_time - order_data['created_time'] > ORDER_TIMEOUT:
            expired_orders.append(order_id)
    
    for order_id in expired_orders:
        del orders[order_id]
    
    if expired_orders:
        save_all_orders(orders)
        logging.info(f"清理了 {len(expired_orders)} 个过期订单")
    return expired_orders

# OKPAY支付类
class OkayPay:
    def __init__(self, shop_id, token):
        self.id = int(shop_id)
        self.token = token
        self.base_url = 'https://api.okaypay.me/shop/'
        
    def sign(self, data):
        data['id'] = self.id
        filtered_data = {k: v for k, v in data.items() if v is not None and v != ''}
        sorted_data = sorted(filtered_data.items())
        
        query_str = urllib.parse.urlencode(sorted_data, quote_via=urllib.parse.quote)
        decoded_str = urllib.parse.unquote(query_str)
        sign_str = decoded_str + '&token=' + self.token
        
        signature = hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
        
        signed_dict = dict(sorted_data)
        signed_dict['sign'] = signature
        return signed_dict
        
    def get_pay_link(self, unique_id, amount, coin, name):
        url = self.base_url + 'payLink'
        params = {
            'unique_id': unique_id,
            'name': name,
            'amount': amount,
            'coin': coin,
            'return_url': os.getenv("OKPAY_RETURN_URL", "https://t.me/")
        }
        signed_data = self.sign(params)
        try:
            response = requests.post(
                url, 
                data=signed_data, 
                headers={'User-Agent': 'HTTP CLIENT'},
                timeout=10,
                verify=False 
            )
            result = response.json()
            if result.get('code') == 200 and 'data' in result:
                return result['data'].get('pay_url'), result['data'].get('order_id')
            else:
                logging.error(f"API Error: {result.get('msg')}")
            return None, None
        except Exception as e:
            logging.error(f"Request Failed: {e}")
            return None, None
            
    def check_order(self, order_id):
        url = self.base_url + 'checkTransferByTxid'
        params = {
            'id': self.id,
            'txid': order_id
        }
        signed_data = self.sign(params)
        try:
            response = requests.post(
                url, 
                data=signed_data, 
                timeout=5, 
                verify=False,
                proxies={"http": None, "https": None}
            )
            result = response.json()
            logging.info(f"轮询订单 {order_id} 结果: {result}")
            if result.get('code') == 200 and 'data' in result:
                return str(result['data'].get('status')) == '1'
            return False
        except Exception as e:
            logging.error(f"查询订单异常: {e}")
            return False
def load_all_users():
    file_path = 'users.json'
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_all_users(data):
    file_path = 'users.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
