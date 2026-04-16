import os

from flask import Flask, render_template_string

app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Web2</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; background: #fff7ed; color: #1f2937; }
    .panel { background: #fff; border: 1px solid #fed7aa; border-radius: 12px; padding: 1rem 1.25rem; max-width: 720px; }
  </style>
</head>
<body>
  <div class=\"panel\">
    <h1>Web2 Service</h1>
    <p>Fraud Detection sub-application #2 is running.</p>
  </div>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
