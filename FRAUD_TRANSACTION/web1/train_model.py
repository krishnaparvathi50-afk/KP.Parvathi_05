import pandas as pd
import matplotlib.pyplot as plt
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib
import os

# ---------------- CREATE FOLDER ----------------
if not os.path.exists("models"):
    os.makedirs("models")

# ---------------- LOAD DATA ----------------
df = pd.read_csv("transactions.csv")

# Ensure correct type
df['isFraud'] = df['isFraud'].astype(int)

# ---------------- FEATURES ----------------
X = df[['amount']]
y = df['isFraud']

# ---------------- SPLIT ----------------
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

# ---------------- MODEL ----------------
model = XGBClassifier(
    n_estimators=20,   # 🔥 20 epochs
    learning_rate=0.1,
    max_depth=5,
    eval_metric="logloss",
    use_label_encoder=False
)

# ---------------- TRAIN ----------------
eval_set = [(X_train, y_train), (X_test, y_test)]

model.fit(
    X_train, y_train,
    eval_set=eval_set,
    verbose=True
)

# ---------------- PREDICT ----------------
y_pred = model.predict(X_test)

# ---------------- ACCURACY ----------------
accuracy = accuracy_score(y_test, y_pred)
print(f"\n✅ Accuracy: {accuracy * 100:.2f}%")

# ---------------- SAVE MODEL ----------------
model_path = os.path.join("models", "xgb_model.pkl")
joblib.dump(model, model_path)

print(f"✅ Model saved at: {model_path}")

# ---------------- LOSS GRAPH ----------------
results = model.evals_result()

train_loss = results['validation_0']['logloss']
test_loss = results['validation_1']['logloss']

plt.figure()
plt.plot(train_loss, label="Train Loss")
plt.plot(test_loss, label="Test Loss")
plt.title("Loss Graph")
plt.xlabel("Epochs")
plt.ylabel("Loss")
plt.legend()
plt.show()

# ---------------- ACCURACY GRAPH ----------------
train_acc = [1 - loss for loss in train_loss]
test_acc = [1 - loss for loss in test_loss]

plt.figure()
plt.plot(train_acc, label="Train Accuracy")
plt.plot(test_acc, label="Test Accuracy")
plt.title("Accuracy Graph")
plt.xlabel("Epochs")
plt.ylabel("Accuracy")
plt.legend()
plt.show()