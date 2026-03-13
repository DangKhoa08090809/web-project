from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from google import genai
import os

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

latest_data = {}

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ─── PAGE ROUTES ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/live")
def live():
    return render_template("liveData.html")

@app.route("/errors")
def errors():
    return render_template("errorCodes.html")

@app.route("/vehicle")
def vehicle():
    return render_template("vehicleInfo.html")

@app.route("/maintenance")
def maintenance():
    return render_template("maintenance.html")


# ─── OBD2 ANALYZE ─────────────────────────────────────────────────────────────

@app.route("/analyze", methods=["POST"])
def analyze_post():
    global latest_data
    data = request.get_json()

    battery_voltage    = data.get("battery_voltage")
    alternator_voltage = data.get("alternator_voltage")
    temperature        = data.get("temperature")
    fuel_instant       = data.get("fuel_instant")
    fuel_avg           = data.get("fuel_avg")
    odometer           = data.get("odometer", 0)

    alerts      = []
    maintenance = []

    if battery_voltage < 12.0:
        alerts.append("⚠️ Điện áp ắc quy thấp!")
    else:
        alerts.append("✅ Điện áp ắc quy bình thường.")

    if alternator_voltage < 13.5:
        alerts.append("⚠️ Máy phát điện có vấn đề!")
    else:
        alerts.append("✅ Hệ thống sạc hoạt động tốt.")

    if temperature > 105:
        alerts.append(f"🔥 Cảnh báo: Nhiệt độ động cơ quá cao ({temperature}°C)!")
    elif temperature < 70:
        alerts.append(f"❄️ Động cơ chưa đủ nhiệt ({temperature}°C).")
    else:
        alerts.append(f"🌡 Nhiệt độ động cơ ổn định ({temperature}°C).")

    if fuel_instant and fuel_avg:
        if fuel_instant > fuel_avg * 1.3:
            alerts.append(f"⛽ Tiêu hao nhiên liệu bất thường: {fuel_instant} L/100km (trung bình {fuel_avg})")
        else:
            alerts.append(f"⛽ Mức tiêu hao nhiên liệu ổn định ({fuel_instant} L/100km)")

    if odometer > 5000:
        maintenance.append("🛠️ Đến hạn thay dầu nhớt.")
    if odometer > 10000:
        maintenance.append("🛠️ Kiểm tra & vệ sinh lọc gió.")
    if odometer > 15000:
        maintenance.append("🛠️ Thay bugi.")
    if odometer > 20000:
        maintenance.append("🛠️ Kiểm tra nước làm mát.")

    latest_data = {
        "battery_voltage":    battery_voltage,
        "alternator_voltage": alternator_voltage,
        "temperature":        temperature,
        "fuel_instant":       fuel_instant,
        "fuel_avg":           fuel_avg,
        "odometer":           odometer,
        "alerts":             alerts,
        "maintenance":        maintenance,
    }

    return jsonify({"alerts": alerts, "maintenance": maintenance})


@app.route("/analyze", methods=["GET"])
def analyze_get():
    if not latest_data:
        return jsonify({"error": "Chưa có dữ liệu nào từ simulator"}), 400
    return jsonify(latest_data)


# ─── AI CHAT ──────────────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
def chat():
    data     = request.get_json()
    messages = data.get("messages", [])
    system   = data.get("system", "")

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    try:
        # Build full prompt: system + conversation history + latest message
        full_prompt = system + "\n\n"
        for msg in messages:
            role  = "User" if msg["role"] == "user" else "Assistant"
            full_prompt += f"{role}: {msg['content']}\n"
        full_prompt += "Assistant:"

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=full_prompt
        )

        return jsonify({"reply": response.text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)