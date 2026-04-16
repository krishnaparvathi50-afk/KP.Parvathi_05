import os
from datetime import datetime, timezone

import requests
from flask import Flask, render_template_string

app = Flask(__name__)

TIMEOUT_SECONDS = 5
WEB1_URL = os.environ.get("WEB1_URL", "").strip()
WEB2_URL = os.environ.get("WEB2_URL", "").strip()

PAGE_TEMPLATE = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Fraud Connector</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; background: #f6f8fb; color: #1f2937; }
    .card { background: #fff; border: 1px solid #dde3ea; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
    .up { color: #0b7a39; font-weight: 700; }
    .down { color: #b42318; font-weight: 700; }
    .unknown { color: #9a6700; font-weight: 700; }
    a { color: #1d4ed8; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <h1>Fraud Detection Connector App</h1>
  <p>Checks health of Web1 and Web2 services.</p>

  {% for service in services %}
  <div class=\"card\">
    <h2>{{ service.name }}</h2>
    <p>Status:
      {% if service.status == 'UP' %}<span class=\"up\">UP</span>
      {% elif service.status == 'DOWN' %}<span class=\"down\">DOWN</span>
      {% else %}<span class=\"unknown\">NOT CONFIGURED</span>
      {% endif %}
    </p>
    {% if service.url %}
      <p>URL: <a href=\"{{ service.url }}\" target=\"_blank\" rel=\"noopener noreferrer\">{{ service.url }}</a></p>
    {% else %}
      <p>Set <code>{{ service.env_key }}</code> env var in Render.</p>
    {% endif %}
    {% if service.error %}<p>Error: {{ service.error }}</p>{% endif %}
  </div>
  {% endfor %}

  <p>Checked at {{ checked_at }} UTC</p>
</body>
</html>
"""


def check_service(url: str):
    if not url:
        return "NOT CONFIGURED", ""

    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        if 200 <= response.status_code < 400:
            return "UP", ""
        return "DOWN", f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        return "DOWN", str(exc)


@app.route("/")
def home():
    web1_status, web1_error = check_service(WEB1_URL)
    web2_status, web2_error = check_service(WEB2_URL)

    services = [
        {"name": "Web1", "env_key": "WEB1_URL", "url": WEB1_URL, "status": web1_status, "error": web1_error},
        {"name": "Web2", "env_key": "WEB2_URL", "url": WEB2_URL, "status": web2_status, "error": web2_error},
    ]
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(PAGE_TEMPLATE, services=services, checked_at=checked_at)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
