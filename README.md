python -m venv venv
venv\Scripts\Activate


<!-- linux -->
sudo apt update
sudo apt install python-is-python3
sudo apt install python3-venv
python3 -m venv venv
source venv/bin/activate



pip install -r requirement.txt

#Thứ tự thực hiện luồng real-time:
python scripts/database/generate_data.py
python scripts/real-time/consumer_orders.py
python scripts/real-time/consumer_order_details.py
python scripts/real-time/order_ready_for_rcm.py
 
test thử chạy file Test.py
