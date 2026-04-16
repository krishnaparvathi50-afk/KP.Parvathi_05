from pathlib import Path

_MODEL = None
_MODEL_ERROR = None


def _load_model():
    global _MODEL, _MODEL_ERROR
    if _MODEL is not None or _MODEL_ERROR is not None:
        return

    try:
        import joblib

        base_dir = Path(__file__).resolve().parent
        model_path = base_dir / "models" / "xgb_model.pkl"
        if not model_path.exists():
            fallback = base_dir / "xgb_model.pkl"
            model_path = fallback if fallback.exists() else model_path

        _MODEL = joblib.load(str(model_path))
    except Exception as e:
        _MODEL_ERROR = e


def predict_fraud(amount):
    _load_model()
    if _MODEL is None:
        raise RuntimeError(f"Fraud model unavailable: {_MODEL_ERROR}")

    import numpy as np

    data = np.array([[float(amount)]])

    pred = _MODEL.predict(data)[0]
    prob = _MODEL.predict_proba(data)[0][1]

    return pred, round(float(prob) * 100, 2)
