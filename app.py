from flask import Flask, jsonify
from flask_cors import CORS
from pysolarmanv5 import PySolarmanV5
import time
import logging
from functools import wraps
import threading
import atexit

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# -- Your Inverter Details --
INVERTER_IP = os.getenv("INVERTER_IP")   # Use the find-inverter-ip.py script to get it
LOGGER_SERIAL = int(os.getenv("LOGGER_SERIAL"))  # Get it from your inverter page

# --- Create the Flask App ---
app = Flask(__name__)
CORS(app)

# Connection management
modbus_connection = None
connection_lock = threading.Lock()
last_connection_attempt = 0
last_activity_time = 0
connection_cooldown = 5  # seconds between connection attempts
inactivity_timeout = 300  # 5 minutes = 300 seconds
cleanup_timer = None

def schedule_cleanup():
    """
    Schedule automatic cleanup after inactivity timeout
    """
    global cleanup_timer
    
    # Cancel existing timer if any
    if cleanup_timer:
        cleanup_timer.cancel()
    
    # Schedule new cleanup
    cleanup_timer = threading.Timer(inactivity_timeout, auto_disconnect)
    cleanup_timer.daemon = True
    cleanup_timer.start()
    logger.debug(f"Scheduled auto-disconnect in {inactivity_timeout} seconds")

def auto_disconnect():
    """
    Automatically disconnect after inactivity timeout
    """
    global modbus_connection, last_activity_time
    
    current_time = time.time()
    time_since_last_activity = current_time - last_activity_time
    
    if time_since_last_activity >= inactivity_timeout:
        logger.info(f"Auto-disconnecting due to {time_since_last_activity:.1f}s inactivity")
        safe_disconnect()
    else:
        # Still some recent activity, reschedule
        remaining_time = inactivity_timeout - time_since_last_activity
        cleanup_timer = threading.Timer(remaining_time, auto_disconnect)
        cleanup_timer.daemon = True
        cleanup_timer.start()
        logger.debug(f"Rescheduled auto-disconnect in {remaining_time:.1f} seconds")

def update_activity():
    """
    Update the last activity timestamp and schedule cleanup
    """
    global last_activity_time
    last_activity_time = time.time()
    schedule_cleanup()

def get_modbus_connection():
    """
    Get or create a modbus connection with retry logic and connection management
    """
    global modbus_connection, last_connection_attempt
    
    current_time = time.time()
    
    with connection_lock:
        # Update activity timestamp
        update_activity()
        
        # Check if we need to wait before attempting connection
        if current_time - last_connection_attempt < connection_cooldown:
            logger.info(f"Connection cooldown active, waiting...")
            return None
        
        try:
            # Close existing connection if it exists
            if modbus_connection:
                try:
                    modbus_connection.disconnect()
                except:
                    pass
            
            # Create new connection
            modbus_connection = PySolarmanV5(
                INVERTER_IP, 
                LOGGER_SERIAL, 
                port=8899, 
                mb_slave_id=1, 
                verbose=False,
                socket_timeout=8
            )
            
            last_connection_attempt = current_time
            logger.info("Modbus connection established successfully")
            return modbus_connection
            
        except Exception as e:
            last_connection_attempt = current_time
            logger.error(f"Failed to create modbus connection: {e}")
            modbus_connection = None
            return None

def safe_disconnect():
    """
    Safely disconnect the modbus connection
    """
    global modbus_connection
    
    if modbus_connection:
        try:
            modbus_connection.disconnect()
            logger.info("Modbus connection closed")
        except Exception as e:
            logger.warning(f"Error closing modbus connection: {e}")
        finally:
            modbus_connection = None

def read_with_retry(modbus, register, count, max_retries=3):
    """
    Read registers with retry logic and better error handling
    """
    for attempt in range(max_retries):
        try:
            result = modbus.read_holding_registers(register, count)
            if result is not None:
                logger.debug(f"Successfully read register {register}: {result}")
                return result
            else:
                logger.warning(f"No data returned from register {register}")
                
        except Exception as e:
            logger.warning(f"Read attempt {attempt + 1}/{max_retries} failed for register {register}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)  # Short delay between retries
            else:
                raise e
    
    return None

def handle_connection_errors(f):
    """
    Decorator to handle connection errors and provide consistent error responses
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            safe_disconnect()
            logger.error(f"Error in {f.__name__}: {e}")
            error_data = {
                'status': 'error',
                'message': str(e),
                'timestamp': time.time()
            }
            return jsonify(error_data), 500
    return decorated_function

# --- API 1: Current Consumption and Battery Percentage ---
@app.route('/api/critical-data', methods=['GET'])
@handle_connection_errors
def get_critical_data():
    """
    Get the most critical data: current consumption and battery percentage
    This is the most important data that should be fetched first
    """
    logger.info("Fetching critical data (consumption & battery)...")
    
    modbus = get_modbus_connection()
    if not modbus:
        raise Exception("Failed to establish modbus connection")
    
    try:
        # Battery SoC (State of Charge) from register 184
        logger.info("Reading battery SoC...")
        soc_raw = read_with_retry(modbus, 184, 1)
        soc = soc_raw[0] if soc_raw else 0
        
        # Small delay between reads
        time.sleep(0.1)
        
        # Total Load Power from register 178
        logger.info("Reading load power...")
        load_power_raw = read_with_retry(modbus, 178, 1)
        load_power = load_power_raw[0] if load_power_raw else 0
        
        # Also get battery power for better context (register 190)
        time.sleep(0.1)
        battery_power_raw = read_with_retry(modbus, 190, 1)
        battery_power = battery_power_raw[0] if battery_power_raw else 0
        
        # Handle signed values for battery power
        if battery_power > 32767:
            battery_power -= 65536
        
        data = {
            'battery_percentage': soc,
            'load_power': load_power,
            'battery_power': battery_power,  # Negative for charge, positive for discharge
            'status': 'success',
            'timestamp': time.time()
        }
        
        logger.info(f"Critical data: SoC={soc}%, Load={load_power}W, Battery={battery_power}W")
        return jsonify(data)
        
    finally:
        safe_disconnect()

# --- API 2: Solar Panel Current Data ---
@app.route('/api/solar-current', methods=['GET'])
@handle_connection_errors
def get_solar_current():
    """
    Get PV1 and PV2 current data
    """
    logger.info("Fetching solar current data...")
    
    modbus = get_modbus_connection()
    if not modbus:
        raise Exception("Failed to establish modbus connection")
    
    try:
        # PV1 Current from register 110
        logger.info("Reading PV1 current...")
        pv1_current_raw = read_with_retry(modbus, 110, 1)
        pv1_current = (pv1_current_raw[0] * 0.1) if pv1_current_raw else 0
        
        time.sleep(0.1)
        
        # PV2 Current from register 112
        logger.info("Reading PV2 current...")
        pv2_current_raw = read_with_retry(modbus, 112, 1)
        pv2_current = (pv2_current_raw[0] * 0.1) if pv2_current_raw else 0
        
        # Also get PV power for context
        time.sleep(0.1)
        pv1_power_raw = read_with_retry(modbus, 186, 1)
        pv1_power = pv1_power_raw[0] if pv1_power_raw else 0
        
        time.sleep(0.1)
        pv2_power_raw = read_with_retry(modbus, 187, 1)
        pv2_power = pv2_power_raw[0] if pv2_power_raw else 0
        
        # Also get voltages for complete picture
        time.sleep(0.1)
        pv1_voltage_raw = read_with_retry(modbus, 109, 1)
        pv1_voltage = (pv1_voltage_raw[0] * 0.1) if pv1_voltage_raw else 0
        
        time.sleep(0.1)
        pv2_voltage_raw = read_with_retry(modbus, 111, 1)
        pv2_voltage = (pv2_voltage_raw[0] * 0.1) if pv2_voltage_raw else 0
        
        data = {
            'pv1_current': round(pv1_current, 2),
            'pv2_current': round(pv2_current, 2),
            'pv1_power': pv1_power,
            'pv2_power': pv2_power,
            'pv1_voltage': round(pv1_voltage, 1),
            'pv2_voltage': round(pv2_voltage, 1),
            'total_solar_power': pv1_power + pv2_power,
            'status': 'success',
            'timestamp': time.time()
        }
        
        logger.info(f"Solar data: PV1={pv1_current}A/{pv1_power}W, PV2={pv2_current}A/{pv2_power}W")
        return jsonify(data)
        
    finally:
        safe_disconnect()

# --- API 3: Complete System Data ---
@app.route('/api/complete-data', methods=['GET'])
@handle_connection_errors
def get_complete_data():
    """
    Get all other system data including grid, inverter status, and additional metrics
    """
    logger.info("Fetching complete system data...")
    
    modbus = get_modbus_connection()
    if not modbus:
        raise Exception("Failed to establish modbus connection")
    
    try:
        data = {
            'grid': {},
            'battery': {},
            'inverter': {},
            'daily_stats': {},
            'status': 'success',
            'timestamp': time.time()
        }
        
        # Grid Data
        logger.info("Reading grid data...")
        grid_power_raw = read_with_retry(modbus, 169, 1)
        grid_power = grid_power_raw[0] if grid_power_raw else 0
        if grid_power > 32767:
            grid_power -= 65536
        
        time.sleep(0.1)
        grid_voltage_raw = read_with_retry(modbus, 150, 1)
        grid_voltage = (grid_voltage_raw[0] * 0.1) if grid_voltage_raw else 0
        
        time.sleep(0.1)
        grid_current_raw = read_with_retry(modbus, 160, 1)
        grid_current = (grid_current_raw[0] * 0.01) if grid_current_raw else 0
        
        data['grid'] = {
            'power': grid_power,
            'voltage': round(grid_voltage, 1),
            'current': round(grid_current, 2),
            'feeding_in': grid_power < 0
        }
        
        # Battery Extended Data
        logger.info("Reading battery extended data...")
        time.sleep(0.1)
        battery_voltage_raw = read_with_retry(modbus, 183, 1)
        battery_voltage = (battery_voltage_raw[0] * 0.01) if battery_voltage_raw else 0
        
        time.sleep(0.1)
        battery_current_raw = read_with_retry(modbus, 191, 1)
        battery_current = battery_current_raw[0] if battery_current_raw else 0
        if battery_current > 32767:
            battery_current -= 65536
        battery_current = battery_current * 0.01
        
        time.sleep(0.1)
        battery_status_raw = read_with_retry(modbus, 189, 1)
        battery_status_code = battery_status_raw[0] if battery_status_raw else 0
        battery_status_lookup = {0: "Charge", 1: "Stand-by", 2: "Discharge"}
        battery_status = battery_status_lookup.get(battery_status_code, f"Unknown ({battery_status_code})")
        
        data['battery'] = {
            'voltage': round(battery_voltage, 2),
            'current': round(battery_current, 2),
            'status': battery_status,
            'status_code': battery_status_code
        }
        
        # Inverter Data
        logger.info("Reading inverter data...")
        time.sleep(0.1)
        inverter_status_raw = read_with_retry(modbus, 59, 1)
        inverter_status_code = inverter_status_raw[0] if inverter_status_raw else 0
        inverter_status_lookup = {0: "Stand-by", 1: "Self-checking", 2: "Normal", 3: "FAULT"}
        inverter_status = inverter_status_lookup.get(inverter_status_code, f"Unknown ({inverter_status_code})")
        
        time.sleep(0.1)
        total_ac_power_raw = read_with_retry(modbus, 175, 1)
        total_ac_power = total_ac_power_raw[0] if total_ac_power_raw else 0
        if total_ac_power > 32767:
            total_ac_power -= 65536
        
        time.sleep(0.1)
        dc_temp_raw = read_with_retry(modbus, 90, 1)
        dc_temp = dc_temp_raw[0] if dc_temp_raw else 0
        if dc_temp > 32767:
            dc_temp -= 65536
        dc_temp = (dc_temp * 0.1) - 100
        
        time.sleep(0.1)
        ac_temp_raw = read_with_retry(modbus, 91, 1)
        ac_temp = ac_temp_raw[0] if ac_temp_raw else 0
        if ac_temp > 32767:
            ac_temp -= 65536
        ac_temp = (ac_temp * 0.1) - 100
        
        time.sleep(0.1)
        grid_connected_raw = read_with_retry(modbus, 194, 1)
        grid_connected_code = grid_connected_raw[0] if grid_connected_raw else 0
        grid_connected = "On-Grid" if grid_connected_code == 1 else "Off-Grid"
        
        data['inverter'] = {
            'status': inverter_status,
            'status_code': inverter_status_code,
            'total_ac_power': total_ac_power,
            'dc_temperature': round(dc_temp, 1),
            'ac_temperature': round(ac_temp, 1),
            'grid_connected': grid_connected,
            'grid_connected_code': grid_connected_code
        }
        
        # Daily Statistics
        logger.info("Reading daily statistics...")
        time.sleep(0.1)
        daily_production_raw = read_with_retry(modbus, 108, 1)
        daily_production = (daily_production_raw[0] * 0.1) if daily_production_raw else 0
        
        time.sleep(0.1)
        daily_consumption_raw = read_with_retry(modbus, 84, 1)
        daily_consumption = (daily_consumption_raw[0] * 0.1) if daily_consumption_raw else 0
        
        time.sleep(0.1)
        daily_battery_charge_raw = read_with_retry(modbus, 70, 1)
        daily_battery_charge = (daily_battery_charge_raw[0] * 0.1) if daily_battery_charge_raw else 0
        
        data['daily_stats'] = {
            'production': round(daily_production, 2),
            'consumption': round(daily_consumption, 2),
            'battery_charge': round(daily_battery_charge, 2)
        }
        
        logger.info("Complete system data retrieved successfully")
        return jsonify(data)
        
    finally:
        safe_disconnect()

# --- Legacy API for backwards compatibility ---
@app.route('/api/data', methods=['GET'])
@handle_connection_errors
def get_legacy_data():
    """
    Legacy endpoint for backwards compatibility
    """
    logger.info("Legacy API called - redirecting to critical data")
    
    modbus = get_modbus_connection()
    if not modbus:
        raise Exception("Failed to establish modbus connection")
    
    try:
        # Battery SoC (State of Charge) from register 184
        soc_raw = read_with_retry(modbus, 184, 1)
        soc = soc_raw[0] if soc_raw else 0
        
        time.sleep(0.1)
        
        # Load Power from register 175 (keeping original logic)
        load_power_raw = read_with_retry(modbus, 175, 1)
        load_power = abs(load_power_raw[0]) if load_power_raw else 0
        
        data = {
            'battery_percentage': soc,
            'load_power': load_power,
            'status': 'success'
        }
        
        return jsonify(data)
        
    finally:
        safe_disconnect()

# --- Health check endpoint ---
@app.route('/api/health', methods=['GET'])
def health_check():
    """
    Enhanced health check endpoint with connection test
    """
    health_data = {
        'status': 'healthy',
        'inverter_ip': INVERTER_IP,
        'logger_serial': LOGGER_SERIAL,
        'timestamp': time.time(),
        'connection_test': 'not_tested'
    }
    
    # Optional connection test
    try:
        modbus = get_modbus_connection()
        if modbus:
            # Try to read a simple register to test connection
            test_result = read_with_retry(modbus, 184, 1)
            if test_result:
                health_data['connection_test'] = 'success'
            else:
                health_data['connection_test'] = 'failed'
            safe_disconnect()
        else:
            health_data['connection_test'] = 'failed'
    except Exception as e:
        health_data['connection_test'] = f'error: {str(e)}'
        safe_disconnect()
    
    return jsonify(health_data)

# --- System info endpoint ---
@app.route('/api/system-info', methods=['GET'])
def get_system_info():
    """
    Get system information and available endpoints
    """
    return jsonify({
        'system': 'Solar Inverter API',
        'version': '2.0',
        'endpoints': {
            '/api/critical-data': 'Battery percentage and current consumption',
            '/api/solar-current': 'PV1 and PV2 current data',
            '/api/complete-data': 'Complete system data',
            '/api/health': 'Health check with connection test',
            '/api/data': 'Legacy endpoint for backwards compatibility'
        },
        'inverter_config': {
            'ip': INVERTER_IP,
            'serial': LOGGER_SERIAL
        }
    })

# --- Error handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'status': 'error',
        'message': 'Endpoint not found',
        'available_endpoints': [
            '/api/critical-data',
            '/api/solar-current',
            '/api/complete-data',
            '/api/health',
            '/api/system-info'
        ]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    safe_disconnect()
    return jsonify({
        'status': 'error',
        'message': 'Internal server error',
        'timestamp': time.time()
    }), 500

# --- Cleanup on exit ---
import atexit

def cleanup():
    """Cleanup function to run on exit"""
    safe_disconnect()
    logger.info("Application cleanup completed")

atexit.register(cleanup)

# --- Run the Server ---
if __name__ == '__main__':
    logger.info("Starting Solar Inverter API Server...")
    logger.info(f"Inverter IP: {INVERTER_IP}")
    logger.info(f"Logger Serial: {LOGGER_SERIAL}")
    
    # Host on 0.0.0.0 to make it accessible from other devices on your network
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)