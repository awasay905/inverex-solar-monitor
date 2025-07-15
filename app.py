import time
from pysolarmanv5 import PySolarmanV5
import os
import dotenv

# Load environment variables from .env file
dotenv.load_dotenv()

# -- Your Inverter Details --
INVERTER_IP = os.getenv("INVERTER_IP") # Your inverter's local IP address
LOGGER_SERIAL = int(os.getenv("LOGGER_SERIAL"))     # Your data logger's serial number

# ===================================================================================
# REGISTER DEFINITIONS (from your final, most complete YAML file)
# This structure is now based on the proven, correct register map.
# Hex addresses from YAML are converted to decimal.
# ===================================================================================
PARAMETERS = {
    "Solar": [
        {"name": "PV1 Power", "reg": 186, "scale": 1, "uom": "W", "rule": 1},
        {"name": "PV2 Power", "reg": 187, "scale": 1, "uom": "W", "rule": 1},
        {"name": "PV1 Voltage", "reg": 109, "scale": 0.1, "uom": "V", "rule": 1},
        {"name": "PV2 Voltage", "reg": 111, "scale": 0.1, "uom": "V", "rule": 1},
        {"name": "PV1 Current", "reg": 110, "scale": 0.1, "uom": "A", "rule": 1},
        {"name": "PV2 Current", "reg": 112, "scale": 0.1, "uom": "A", "rule": 1},
        {"name": "Daily Production", "reg": 108, "scale": 0.1, "uom": "kWh", "rule": 1},
    ],
    "Battery": [
        {"name": "Battery Status", "reg": 189, "scale": 1, "uom": "", "rule": "lookup", "lookup": {0: "Charge", 1: "Stand-by", 2: "Discharge"}},
        {"name": "Battery Power", "reg": 190, "scale": 1, "uom": "W", "rule": 2}, # Signed: negative for charge, positive for discharge
        {"name": "Battery Voltage", "reg": 183, "scale": 0.01, "uom": "V", "rule": 1},
        {"name": "Battery SOC", "reg": 184, "scale": 1, "uom": "%", "rule": 1},
        {"name": "Battery Current", "reg": 191, "scale": 0.01, "uom": "A", "rule": 2}, # Signed
        {"name": "Daily Battery Charge", "reg": 70, "scale": 0.1, "uom": "kWh", "rule": 1},
    ],
    "Grid": [
        {"name": "Total Grid Power", "reg": 169, "scale": 1, "uom": "W", "rule": 2}, # Signed: negative for feed-in
        {"name": "Grid Voltage L1", "reg": 150, "scale": 0.1, "uom": "V", "rule": 1},
        {"name": "Grid Current L1", "reg": 160, "scale": 0.01, "uom": "A", "rule": 1},
    ],
    "Load": [
        {"name": "Total Load Power", "reg": 178, "scale": 1, "uom": "W", "rule": 1},
        {"name": "Load Voltage", "reg": 157, "scale": 0.1, "uom": "V", "rule": 1},
        {"name": "Daily Load Consumption", "reg": 84, "scale": 0.1, "uom": "kWh", "rule": 1},
    ],
    "Inverter": [
        {"name": "Running Status", "reg": 59, "scale": 1, "uom": "", "rule": "lookup", "lookup": {0: "Stand-by", 1: "Self-checking", 2: "Normal", 3: "FAULT"}},
        {"name": "Total Power (AC Output)", "reg": 175, "scale": 1, "uom": "W", "rule": 2},
        {"name": "DC Temperature", "reg": 90, "scale": 0.1, "uom": "°C", "rule": 2, "offset": -100},
        {"name": "AC Temperature", "reg": 91, "scale": 0.1, "uom": "°C", "rule": 2, "offset": -100},
        {"name": "Grid-connected Status", "reg": 194, "scale": 1, "uom": "", "rule": "lookup", "lookup": {0: "Off-Grid", 1: "On-Grid"}},
    ]
}

def read_and_interpret(modbus, param):
    """Reads a register and interprets the value based on the defined rules."""
    try:
        raw_val = None
        is_32bit = isinstance(param['reg'], list)

        if is_32bit:
            raw_list = modbus.read_holding_registers(register_addr=param['reg'][0], quantity=2)
            if raw_list: raw_val = (raw_list[0] << 16) + raw_list[1]
        else:
            raw_list = modbus.read_holding_registers(register_addr=param['reg'], quantity=1)
            if raw_list: raw_val = raw_list[0]

        if raw_val is None: return "No response"

        # Rule 2: Signed 16-bit
        if param['rule'] == 2 and raw_val > 32767:
            raw_val -= 65536

        calculated_value = raw_val * param['scale']
        if 'offset' in param:
            calculated_value += param['offset']

        if param['rule'] == 'lookup':
            return param['lookup'].get(raw_val, f"Unknown Code ({raw_val})")
        
        # Format the final output string
        if isinstance(calculated_value, float):
            precision = 2 if abs(calculated_value) > 10 else 3
            return f"{calculated_value:,.{precision}f} {param['uom']}".replace(",", "") # Remove comma for values > 1000
        else:
            return f"{calculated_value:,} {param['uom']}"

    except Exception as e:
        return f"ERROR: {e}"

def main():
    """Main function to connect and read all defined parameters."""
    print("--- Revised Universal Inverter Data Reader ---")
    print(f"Connecting to inverter at {INVERTER_IP}...")

    try:
        modbus = PySolarmanV5(INVERTER_IP, LOGGER_SERIAL, port=8899, mb_slave_id=1, verbose=False)
        print("Connection successful. Reading data...\n")
    except Exception as e:
        print(f"\nFATAL: Could not connect to the inverter: {e}")
        print("Please check your INVERTER_IP, LOGGER_SERIAL, and network connection.")
        return
    
    for group, params in PARAMETERS.items():
        print(f"--- [ {group.upper()} ] ---")
        for param in params:
            value_str = read_and_interpret(modbus, param)
            print(f"  {param['name']:<30}: {value_str}")
        print() 

    print("--- [ SCRIPT FINISHED ] ---")

if __name__ == "__main__":
    main()