import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from flask import Flask, jsonify, redirect, request

# Optional ML imports. App must run even when these are unavailable.
try:
    import numpy as np
except Exception as exc:
    np = None
    print("NumPy not available (ML mode disabled):", exc)

try:
    from tensorflow.keras.models import load_model
except Exception as exc:
    load_model = None
    print("TensorFlow not available (ML mode disabled):", exc)

app = Flask(__name__)

# Public service URLs should be set in Render env vars.
WEB1_URL = os.environ.get("WEB1_URL", "").strip()
WEB2_URL = os.environ.get("WEB2_URL", "").strip()

APP_DIR = Path(__file__).resolve().parent
GENERATOR_PATH = APP_DIR / "generator.h5"
DISCRIMINATOR_PATH = APP_DIR / "discriminator.h5"


def safe_load_models():
    generator_model = None
    discriminator_model = None
    load_messages = []

    if load_model is None:
        load_messages.append("TensorFlow not installed")
        return generator_model, discriminator_model, load_messages

    # generator.h5 is optional. Missing file should not crash app.
    if GENERATOR_PATH.exists():
        try:
            generator_model = load_model(str(GENERATOR_PATH))
            load_messages.append("generator.h5 loaded")
        except Exception as exc:
            load_messages.append(f"generator.h5 load failed: {exc}")
    else:
        load_messages.append("generator.h5 not found")

    if DISCRIMINATOR_PATH.exists():
        try:
            discriminator_model = load_model(str(DISCRIMINATOR_PATH))
            load_messages.append("discriminator.h5 loaded")
        except Exception as exc:
            load_messages.append(f"discriminator.h5 load failed: {exc}")
    else:
        load_messages.append("discriminator.h5 not found")

    return generator_model, discriminator_model, load_messages


generator, discriminator, model_load_messages = safe_load_models()


def is_service_up(url):
    if not url:
        return False

    try:
        with urlopen(url, timeout=1.5) as response:
            return response.status < 500
    except URLError:
        return False
    except Exception:
        return False


def rule_based_fraud_detection(payload):
    amount = float(payload.get("amount", 0) or 0)

    repeated_transactions = payload.get("repeated_transactions")
    if repeated_transactions is None:
        repeated_transactions = payload.get("is_repeated")

    if repeated_transactions is None:
        repeated_count = int(payload.get("repeat_count", 0) or 0)
        repeated_transactions = repeated_count > 1
    else:
        if isinstance(repeated_transactions, str):
            repeated_transactions = repeated_transactions.lower() in {"1", "true", "yes", "y"}
        else:
            repeated_transactions = bool(repeated_transactions)

    reasons = []
    if amount > 50000:
        reasons.append("High transaction amount (>50000)")
    if repeated_transactions:
        reasons.append("Repeated transactions")

    if reasons:
        return {
            "mode": "rule_based",
            "result": "FRAUD",
            "reasons": reasons,
        }

    return {
        "mode": "rule_based",
        "result": "GENUINE",
        "reasons": ["No fraud rule triggered"],
    }


def ml_fraud_detection(payload):
    if discriminator is None or np is None:
        return None

    vector = payload.get("features")
    if vector is None:
        return None

    try:
        input_data = np.array(vector, dtype="float32").reshape(1, -1)
        prediction = discriminator.predict(input_data, verbose=0)
        score = float(prediction[0][0])
        result = "FRAUD" if score < 0.5 else "GENUINE"

        return {
            "mode": "ml",
            "result": result,
            "prediction_score": score,
            "threshold": 0.5,
        }
    except Exception as exc:
        return {
            "mode": "ml",
            "error": str(exc),
        }


@app.route("/")
def home():
    web1_up = is_service_up(WEB1_URL)
    web2_up = is_service_up(WEB2_URL)

    return f"""
    <html>
    <head>
        <title>Fraud + GAN Connector</title>
        <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 30px; }}
            .ok {{ color: #0b7a0b; }}
            .down {{ color: #b00020; }}
            a {{ display: inline-block; margin-right: 12px; }}
        </style>
    </head>
    <body>
        <h2>Fraud + GAN Multi App Connector</h2>

        <h3>Service Status</h3>
        <p>Web1: <strong class=\"{'ok' if web1_up else 'down'}\">{'UP' if web1_up else 'DOWN'}</strong></p>
        <p>Web2: <strong class=\"{'ok' if web2_up else 'down'}\">{'UP' if web2_up else 'DOWN'}</strong></p>

        <h3>Model Mode</h3>
        <p>Discriminator: <strong>{'AVAILABLE' if discriminator is not None else 'MISSING'}</strong></p>
        <p>Generator: <strong>{'AVAILABLE' if generator is not None else 'MISSING'}</strong></p>

        <h3>Navigation</h3>
        <p>
            <a href=\"/web1\">Open Web1</a>
            <a href=\"/web2\">Open Web2</a>
            <a href=\"/status\">JSON Status</a>
        </p>

        <p>POST /check with JSON for fraud detection.</p>
        <pre>
Rule fallback input example:
{{\"amount\": 62000, \"repeated_transactions\": true}}

ML input example (when model loaded):
{{\"features\": [0.12, 0.44, 0.88]}}
        </pre>
    </body>
    </html>
    """


@app.route("/status")
def status():
    data = {
        "web1": {"url": WEB1_URL, "up": is_service_up(WEB1_URL)},
        "web2": {"url": WEB2_URL, "up": is_service_up(WEB2_URL)},
        "models": {
            "generator_loaded": generator is not None,
            "discriminator_loaded": discriminator is not None,
            "messages": model_load_messages,
        },
    }
    return jsonify(data)


@app.route("/web1")
@app.route("/web1/<path:subpath>")
def open_web1(subpath=""):
    if not WEB1_URL:
        return jsonify({"error": "WEB1_URL is not configured"}), 400
    target = WEB1_URL if not subpath else f"{WEB1_URL}/{subpath}"
    return redirect(target)


@app.route("/web2")
@app.route("/web2/<path:subpath>")
def open_web2(subpath=""):
    if not WEB2_URL:
        return jsonify({"error": "WEB2_URL is not configured"}), 400
    target = WEB2_URL if not subpath else f"{WEB2_URL}/{subpath}"
    return redirect(target)


@app.route("/generate", methods=["GET"])
def generate():
    if generator is None:
        return jsonify({"error": "generator.h5 not loaded"}), 503
    if np is None:
        return jsonify({"error": "NumPy not installed"}), 503

    try:
        noise = np.random.normal(0, 1, (1, 100))
        generated_data = generator.predict(noise, verbose=0)
        return jsonify({"generated_sample": generated_data.tolist()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/check", methods=["POST"])
def check():
    payload = request.get_json(silent=True) or {}

    # Try ML prediction first if model + feature vector are available.
    ml_result = ml_fraud_detection(payload)
    if ml_result is not None and "error" not in ml_result:
        return jsonify(ml_result)

    # Always fallback to rules when ML is unavailable or fails.
    rule_result = rule_based_fraud_detection(payload)
    if ml_result is not None and "error" in ml_result:
        rule_result["ml_error"] = ml_result["error"]

    return jsonify(rule_result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
