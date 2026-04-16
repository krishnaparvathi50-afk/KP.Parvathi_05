import pandas as pd
import random
from datetime import datetime, timedelta

data = []

# Sample users
users = [f"user{i}" for i in range(1, 51)]

# Generate random IP
def random_ip():
    return ".".join(str(random.randint(1, 255)) for _ in range(4))

# Generate random mobile number
def random_mobile():
    return str(random.randint(6000000000, 9999999999))

# Generate random timestamp
def random_time():
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 22)
    return start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))

for i in range(3000):
    sender = random.choice(users)
    receiver = random_mobile()
    amount = random.randint(10, 50000)
    ip = random_ip()
    time = random_time()

    # 🔥 Fraud Logic (important)
    if amount > 15000 and random.random() > 0.5:
        status = "fraud"
        isFraud = 1
    else:
        status = "success"
        isFraud = 0

    data.append([
        sender,
        receiver,
        amount,
        ip,
        time.strftime("%Y-%m-%d %H:%M:%S"),
        status,
        isFraud
    ])

# Create DataFrame
df = pd.DataFrame(data, columns=[
    "sender", "receiver", "amount", "ip", "timestamp", "status", "isFraud"
])

# Save CSV
df.to_csv("transactions.csv", index=False)

print("✅ 3000 transaction dataset generated: transactions.csv")