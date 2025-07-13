from flask import Flask, jsonify
from flask_cors import CORS
from pysolarmanv5 import PySolarmanV5
import time
import logging
import threading
import os
import json
import redis
import random
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# Inverter Details
INVERTER_IP = os.getenv("INVERTER_IP")
if not INVERTER_IP:
    raise ValueError("INVERTER_IP not set in .env file")
LOGGER_SERIAL = os.getenv("LOGGER_SERIAL")
if not LOGGER_SERIAL:
    raise ValueError("LOGGER_SERIAL not set in .env file")
LOGGER_SERIAL = int(LOGGER_SERIAL)


# Redis Configuration
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_CACHE_KEY = 'inverter_data'
REDIS_LOCK_KEY = 'inverter_poll_lock'
REDIS_LOCK_TIMEOUT = 20  # Increased for safety during full data fetch
REDIS_LAST_ACTIVITY_KEY = 'inverter_last_activity'
REDIS_FORCE_POLL_KEY = 'inverter_force_poll'

# Dynamic Polling and Backoff Configuration
IDLE_POLLING_INTERVAL = 300  # 5 minutes
ACTIVE_POLLING_INTERVAL = 3    # 3 seconds
USER_ACTIVITY_TIMEOUT = 120  # 3 minutes
BACKOFF_INITIAL_DELAY = 5
BACKOFF_FACTOR = 2
BACKOFF_MAX_DELAY = 600        # 10 minutes

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Create the Flask App & Redis Client ---
app = Flask(__name__)
CORS(app)
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()
    logger.info("Successfully connected to Redis.")
except redis.exceptions.ConnectionError as e:
    logger.error(f"FATAL: Could not connect to Redis at {REDIS_HOST}:{REDIS_PORT}. Please ensure Redis is running. Error: {e}")
    exit(1)


# --- Inverter Communication Logic ---

def read_with_retry(modbus, register, count, max_retries=3):
    """Read registers with retry logic."""
    for attempt in range(max_retries):
        try:
            result = modbus.read_holding_registers(register, count)
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"Read attempt {attempt + 1}/{max_retries} failed for register {register}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                raise e
    return None

def fetch_all_inverter_data():
    """Connects to the inverter and fetches ALL required data in a single session."""
    modbus = None
    try:
        logger.info("Connecting to inverter for polling...")
        modbus = PySolarmanV5(
            INVERTER_IP, LOGGER_SERIAL, port=8899, mb_slave_id=1,
            verbose=False, socket_timeout=10
        )

        # --- Process raw data (including signed values) ---
        def to_signed(val):
            return val - 65536 if val > 32767 else val

        # --- Read all registers ---
        soc_raw = read_with_retry(modbus, 184, 1)
        load_power_raw = read_with_retry(modbus, 178, 1)
        battery_power_raw = read_with_retry(modbus, 190, 1)
        pv1_current_raw = read_with_retry(modbus, 110, 1)
        pv2_current_raw = read_with_retry(modbus, 112, 1)
        pv1_power_raw = read_with_retry(modbus, 186, 1)
        pv2_power_raw = read_with_retry(modbus, 187, 1)
        pv1_voltage_raw = read_with_retry(modbus, 109, 1)
        pv2_voltage_raw = read_with_retry(modbus, 111, 1)
        grid_power_raw = read_with_retry(modbus, 169, 1)
        daily_production_raw = read_with_retry(modbus, 108, 1)
        daily_consumption_raw = read_with_retry(modbus, 84, 1)

        # --- Assemble the final data structure ---
        data = {
            'critical': {
                'battery_percentage': soc_raw[0] if soc_raw else 0,
                'load_power': load_power_raw[0] if load_power_raw else 0,
                'battery_power': to_signed(battery_power_raw[0]) if battery_power_raw else 0
            },
            'solar': {
                'pv1_current': round((pv1_current_raw[0] * 0.1), 2) if pv1_current_raw else 0,
                'pv2_current': round((pv2_current_raw[0] * 0.1), 2) if pv2_current_raw else 0,
                'pv1_power': pv1_power_raw[0] if pv1_power_raw else 0,
                'pv2_power': pv2_power_raw[0] if pv2_power_raw else 0,
                'pv1_voltage': round((pv1_voltage_raw[0] * 0.1), 1) if pv1_voltage_raw else 0,
                'pv2_voltage': round((pv2_voltage_raw[0] * 0.1), 1) if pv2_voltage_raw else 0,
                'total_solar_power': (pv1_power_raw[0] if pv1_power_raw else 0) + (pv2_power_raw[0] if pv2_power_raw else 0)
            },
            'complete': {
                'grid_power': to_signed(grid_power_raw[0]) if grid_power_raw else 0,
                'daily_production_kwh': round((daily_production_raw[0] * 0.1), 2) if daily_production_raw else 0,
                'daily_consumption_kwh': round((daily_consumption_raw[0] * 0.1), 2) if daily_consumption_raw else 0,
            },
            'status': 'success',
            'timestamp': time.time()
        }

        logger.info(f"Successfully polled data. SoC: {data['critical']['battery_percentage']}%")
        return data

    except Exception as e:
        logger.error(f"Could not fetch inverter data: {e}")
        return None
    finally:
        if modbus:
            try:
                modbus.disconnect()
            except:
                pass

# --- Enhanced Background Polling Thread ---
def poll_inverter_data():
    consecutive_failures = 0
    while True:
        force_poll_flag = redis_client.get(REDIS_FORCE_POLL_KEY)

        last_activity_ts = float(redis_client.get(REDIS_LAST_ACTIVITY_KEY) or 0)
        is_currently_idle = (time.time() - last_activity_ts) >= USER_ACTIVITY_TIMEOUT

        should_poll_now = False
        if force_poll_flag:
            logger.info("Force poll signal received. Polling immediately.")
            redis_client.delete(REDIS_FORCE_POLL_KEY)
            should_poll_now = True
        elif not is_currently_idle:
            should_poll_now = True

        if should_poll_now or is_currently_idle:
            lock_acquired = redis_client.set(REDIS_LOCK_KEY, '1', nx=True, ex=REDIS_LOCK_TIMEOUT)
            if lock_acquired:
                logger.info("Lock acquired. Starting poll.")
                try:
                    data = fetch_all_inverter_data()
                    if data:
                        if consecutive_failures > 0: logger.info("Inverter connection restored.")
                        consecutive_failures = 0
                        redis_client.set(REDIS_CACHE_KEY, json.dumps(data))
                    else:
                        consecutive_failures += 1
                        logger.warning(f"Polling failed. Consecutive failures: {consecutive_failures}")
                finally:
                    redis_client.delete(REDIS_LOCK_KEY)
                    logger.debug("Lock released.")
            else:
                logger.debug("Could not acquire lock, another process is already polling.")

        if consecutive_failures > 0:
            backoff_delay = min(BACKOFF_INITIAL_DELAY * (BACKOFF_FACTOR ** (consecutive_failures - 1)), BACKOFF_MAX_DELAY)
            sleep_duration = backoff_delay + random.uniform(0, 1)
            logger.warning(f"Inverter unreachable. Backing off for {sleep_duration:.2f} seconds.")
        elif is_currently_idle:
            sleep_duration = IDLE_POLLING_INTERVAL
        else:
            sleep_duration = ACTIVE_POLLING_INTERVAL

        logger.debug(f"Poller sleeping for {sleep_duration:.2f} seconds.")
        time.sleep(sleep_duration)

# --- Intelligent API Helper ---
def get_data_with_dynamic_refresh(wait_timeout=15):
    redis_client.set(REDIS_LAST_ACTIVITY_KEY, str(time.time()), ex=USER_ACTIVITY_TIMEOUT + 60)

    cached_data_str = redis_client.get(REDIS_CACHE_KEY)
    cached_data = json.loads(cached_data_str) if cached_data_str else None

    is_stale = True
    if cached_data:
        time_since_update = time.time() - cached_data.get('timestamp', 0)
        if time_since_update < (ACTIVE_POLLING_INTERVAL + 2):
            is_stale = False

    if is_stale and (not cached_data or (time.time() - cached_data.get('timestamp', 0)) > IDLE_POLLING_INTERVAL / 2):
        logger.info("Data is stale. Triggering an immediate refresh and waiting.")
        old_timestamp = cached_data.get('timestamp', 0) if cached_data else 0

        redis_client.set(REDIS_FORCE_POLL_KEY, '1', ex=10)

        start_wait = time.time()
        while time.time() - start_wait < wait_timeout:
            new_data_str = redis_client.get(REDIS_CACHE_KEY)
            new_data = json.loads(new_data_str) if new_data_str else None
            if new_data and new_data.get('timestamp', 0) > old_timestamp:
                logger.info(f"Cache refreshed in {time.time() - start_wait:.2f} seconds.")
                return new_data
            time.sleep(0.2)

        logger.warning(f"Timed out waiting for cache refresh. Returning existing data.")
        return cached_data
    else:
        return cached_data

# --- API Endpoints ---
def generate_error_response():
    return jsonify({'status': 'error', 'message': 'Data not available yet. Please try again in a few seconds.'}), 503

@app.route('/api/critical-data', methods=['GET'])
def get_critical_data():
    all_data = get_data_with_dynamic_refresh()
    if all_data and 'critical' in all_data:
        response = all_data['critical']
        response.update({'status': 'success', 'timestamp': all_data.get('timestamp')})
        return jsonify(response)
    return generate_error_response()

@app.route('/api/solar-current', methods=['GET'])
def get_solar_current():
    all_data = get_data_with_dynamic_refresh()
    if all_data and 'solar' in all_data:
        response = all_data['solar']
        response.update({'status': 'success', 'timestamp': all_data.get('timestamp')})
        return jsonify(response)
    return generate_error_response()

@app.route('/api/complete-data', methods=['GET'])
def get_complete_data():
    all_data = get_data_with_dynamic_refresh()
    if all_data:
        # Combine all data categories for the complete view
        response = {**all_data.get('critical', {}), **all_data.get('solar', {}), **all_data.get('complete', {})}
        response.update({'status': 'success', 'timestamp': all_data.get('timestamp')})
        return jsonify(response)
    return generate_error_response()

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check reports on cache status without triggering a refresh."""
    cached_json = redis_client.get(REDIS_CACHE_KEY)
    all_data = json.loads(cached_json) if cached_json else None

    health_status = {
        'status': 'healthy',
        'inverter_ip': INVERTER_IP,
        'timestamp': time.time(),
        'cache_status': 'empty'
    }
    if all_data:
        time_since_update = time.time() - all_data.get('timestamp', 0)
        health_status['last_update_seconds_ago'] = round(time_since_update, 1)
        if time_since_update < (IDLE_POLLING_INTERVAL + 10):
            health_status['cache_status'] = 'fresh'
        else:
            health_status['cache_status'] = 'stale'
    return jsonify(health_status)

# --- Main Execution ---
if __name__ == '__main__':
    logger.info("Starting Solar Inverter API Server with Dynamic Refresh...")
    poller_thread = threading.Thread(target=poll_inverter_data, daemon=True)
    poller_thread.start()
    logger.info(f"Background polling thread started.")
    app.run(host='0.0.0.0', port=5000, debug=False)
else:
    # When running with Gunicorn, this block will be executed by each worker.
    # The Redis lock will ensure only one poller thread is active at a time.
    logger.info("Starting background poller in Gunicorn worker...")
    poller_thread = threading.Thread(target=poll_inverter_data, daemon=True)
    poller_thread.start()