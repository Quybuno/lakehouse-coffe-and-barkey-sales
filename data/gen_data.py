import pandas as pd
from faker import Faker
import random
from datetime import datetime
fake = Faker('vi_VN')

# Cấu trúc dữ liệu phân cấp để đảm bảo Data Integrity

def store():
    vietnam_locations = {
        'North': {
            'Hà Nội': ['Phố Huế', 'Lê Duẩn', 'Quang Trung', 'Hàng Bông', 'Cầu Giấy'],
            'Hải Phòng': ['Lạch Tray', 'Lê Hồng Phong', 'Cầu Đất', 'Điện Biên Phủ'],
            'Quảng Ninh': ['Hạ Long', 'Trần Hưng Đạo', 'Kênh Liêm']
        },
        'Central': {
            'Đà Nẵng': ['Võ Nguyên Giáp', 'Nguyễn Văn Linh', 'Hàm Nghi', 'Lê Duẩn'],
            'Huế': ['Hùng Vương', 'Lê Lợi', 'Nguyễn Huệ'],
            'Nha Trang': ['Trần Phú', 'Phạm Văn Đồng', 'Thống Nhất']
        },
        'South': {
            'TP Hồ Chí Minh': ['Nguyễn Huệ', 'Lê Lợi', 'Cách Mạng Tháng 8', 'Đồng Khởi', 'Phan Xích Long'],
            'Cần Thơ': ['30 Tháng 4', 'Ninh Kiều', 'Trần Văn Khéo'],
            'Bình Dương': ['Đại Lộ Bình Dương', 'Yersin', 'Phú Lợi']
        }
    }

    data = []

    for i in range(1, 1001):
        # Chọn Miền theo trọng số (Miền Nam nhiều cửa hàng nhất)
        region = random.choices(['North', 'Central', 'South'], weights=[35, 20, 45])[0]
        
        # Chọn Tỉnh/Thành trong Miền đó
        city = random.choice(list(vietnam_locations[region].keys()))
        
        # Chọn Tuyến đường thuộc đúng Tỉnh/Thành đó
        street = random.choice(vietnam_locations[region][city])
        
        address = f"{random.randint(1, 800)} {street}"
        
        data.append({
            "shop_id": i,
            "shop_name": f"KD Bakery Coffee {city}",
            "address": address,
            "city": city,
            "region": region,
        })

    df = pd.DataFrame(data)
    # Dùng utf-8-sig để Excel trên Windows đọc được tiếng Việt
    df.to_csv("store.csv", index=False, encoding='utf-8-sig')

    print("Đã tạo 1000 cửa hàng!")

def customer():
    data = []
    start_date = datetime(2025,8,1)
    end_date = datetime(2026,6,1)

    tier = ['regular','gold','silver']

    for i in range(1,201):
        x = fake.date_time_between(start_date = start_date, end_date = end_date) 
        format_time = x.strftime('%Y-%m-%d %H:%M:%S')
        record = {
            "id" : i + 1000000,
            "name" : fake.first_name(),
            "phone_number" : fake.msisdn()[:9],
            "tier" : random.choice(tier),
            "updated_at" : format_time
        }
        data.append(record)
    
    df = pd.DataFrame(data)
    df_old = pd.read_csv("customers.csv")
    df_final = pd.concat([df_old,df],ignore_index=False)
    df_final.to_csv("customers.csv",index = False,encoding='utf-8-sig')
    
    print("da tao thanh cong")

if __name__ == "__main__":
    print("Bat dau sinh du lieu")
    store()
    customer()
