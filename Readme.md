# Inverex Solar Monitor

> A real-time local running application to monitor the status of an Inverex Nitrox Solar Inverter.

This project provides a complete, self-hosted solution for monitoring your Inverex solar inverter. It features a Python/Flask backend that communicates directly with the inverter on your local network and a modern React frontend to visualize the data in real-time.

---

## 🎯 Compatibility & Important Disclaimer

**This project has been developed and tested exclusively on an Inverex Nitrox 6kW Hybrid (Single-Phase) inverter.**

While the underlying communication protocol is common for many Deye-rebranded inverters (like Sunsynk, Sol-Ark, etc.), functionality on other models is **not guaranteed**. The Modbus register addresses and data scaling may differ, especially for:
*   Three-phase models
*   Different power ratings (e.g., 3kW, 5kW, 8kW)
*   Non-hybrid or off-grid models

You are welcome to try it with your inverter! If you do, please **[open an issue](https://github.com/awasay905/inverex-solar-monitor/issues)** to report your success or any problems you encounter. Your feedback is invaluable for expanding the project's compatibility.

---

## 🌟 Features

*   **Ready-to-Use Dashboard**: A clean and modern React interface to visualize your solar system's performance—no setup required.
*   **Stable API Bridge**: Exposes a reliable set of REST API endpoints, solving Modbus connection complexities so you don't have to.
*   **Real-Time Data Monitoring**: Get instant updates on battery SoC, solar generation, grid consumption, and load power.
*   **Auto-Discovery**: Automatically finds your inverter's IP address and serial number on your local network for a pain-free setup.
*   **Local First & Private**: All data is fetched and processed on your local network. No cloud, no latency, no privacy concerns.
*   **Highly Extensible**: The perfect data source for Home Assistant, custom dashboards (like Grafana), or your own scripts.
*   **Robust Backend**: A stable Flask API with built-in retry logic and connection management, optimized for the tested hardware.

---

## 🤔 Project Goals & Philosophy

You might wonder, "Why use this if a Home Assistant integration already exists?" This project was built with several goals in mind:

1.  **To Be a Complete, Standalone Application.**
    Not everyone runs Home Assistant. This is a self-contained tool with its **own polished frontend**. You can set it up on a Raspberry Pi and have a beautiful, dedicated monitoring panel accessible from any browser on your network, with zero UI configuration needed.

2.  **To Provide a Stable, Decoupled API Gateway.**
    Direct Modbus connections can sometimes be unstable or cause the inverter's Wi-Fi logger to lock up. This application acts as a **stabilizing middle-layer**. It manages the connection gracefully and provides a simple, reliable HTTP endpoint that any other service (including Home Assistant) can consume.

3.  **To Be Lightweight and Focused.**
    This application does one thing and does it well: talk to your solar inverter. If you don't need the overhead of a full home automation platform, this is a much lighter and more efficient solution.

4.  **To Empower Developers and Tinkerers.**
    If you want to build custom automations, log data to a database like InfluxDB, or create custom alerts, having a clean REST API to work with is far simpler than interacting with Modbus libraries directly.

---

## 🛠️ Setup and Installation

### Prerequisites

*   Python 3.7+
*   Node.js and npm (for the React frontend)
*   An Inverex Nitrox 6kW Hybrid (Single-Phase) or a similar Deye-based inverter connected to your Wi-Fi network.

### 1. Get the Code

Clone the repository to your local machine:
```bash
git clone https://github.com/awasay905/inverex-solar-monitor.git
cd inverex-solar-monitor
```

### 2. Backend Setup (Flask API)

The backend server runs from the root of the project directory.

1.  **Create a virtual environment and install dependencies:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```

2.  **Find your Inverter's IP and Serial Number:**
    Run the auto-discovery script to get your inverter's credentials.
    ```bash
    python3 get_ip_and_serial.py
    ```

3.  **Configure Environment Variables:**
    Create a file named `.env` in the root of the project directory. Add the details found by the script.
    ```
    INVERTER_IP=192.168.X.X
    LOGGER_SERIAL=XXXXXXXXXX
    ```

4.  **Run the Flask Server:**
    ```bash
    flask run --host=0.0.0.0
    ```
    The API will now be accessible on your network at `http://<your-server-ip>:5000`.

### 3. Frontend Setup (React App)

The React frontend code is located in the `frontend/solar-dashboard` directory.

1.  **Navigate to the frontend directory:**
    ```bash
    cd frontend/solar-dashboard
    ```

2.  **Install dependencies:**
    ```bash
    npm install
    ```

3.  **Start the development server:**
    ```bash
    npm start
    ```
    You can now access the monitoring dashboard in your browser, typically at `http://localhost:5173`. It will automatically connect to the Flask API running on your network.

---
## 🔌 API Endpoints

The Flask server provides the following endpoints to query data from the inverter.

| Endpoint              | Method | Description                                                                 |
| --------------------- | ------ | --------------------------------------------------------------------------- |
| `/api/critical-data`  | `GET`  | Fetches the most essential data: battery percentage and current power load. |
| `/api/solar-current`  | `GET`  | Provides detailed data for PV1 and PV2, including voltage, current, and power. |
| `/api/complete-data`  | `GET`  | Returns a comprehensive dataset including grid, battery, and inverter stats. |
| `/api/health`         | `GET`  | A health check endpoint to verify API and inverter connectivity.            |
| `/api/system-info`    | `GET`  | Provides information about the API version and available endpoints.         |