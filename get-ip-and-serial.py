import requests
import re
import socket
import ipaddress
import time

# --- Scan the network for Deye inverters by checking for open port 8899 ---
def find_deye_inverters(port=8899, timeout=1.0, max_scan=50):
    def get_local_ip():
        """Get the local IP address of this machine (within the LAN)"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        except:
            return '127.0.0.1'
        finally:
            s.close()

    def is_port_open(ip):
        """Check if the given IP has the specified port open (8899 for Deye)"""
        try:
            with socket.create_connection((str(ip), port), timeout=timeout):
                return str(ip)
        except:
            return None

    local_ip = get_local_ip()
    network = ipaddress.IPv4Network(local_ip + '/24', strict=False)
    local_parts = local_ip.split(".")
    base_prefix = ".".join(local_parts[:3])

    found = []
    scanned = 0

    for i in range(1, 255):
        if scanned >= max_scan:
            break
        ip = f"{base_prefix}.{i}"
        if ip == local_ip:
            continue
        result = is_port_open(ip)
        if result:
            found.append(result)
        scanned += 1

    return found

# --- Fetch the serial number (cover_mid) from the inverter’s web interface ---
def get_cover_mid(ip_address, username="admin", password="admin", retries=3):
    url = f"http://{ip_address}/status.html"
    for attempt in range(retries):
        try:
            response = requests.get(url, auth=(username, password), timeout=5)
            response.raise_for_status()

            # Match the JavaScript variable from HTML: var cover_mid = "XXXXXXXXXX";
            match = re.search(r'var\s+cover_mid\s*=\s*"(\d+)"', response.text)
            if match:
                return match.group(1)
            else:
                print("cover_mid not found in HTML.")
                return None

        except requests.HTTPError as e:
            print(f"HTTP error: {e} - Status code: {response.status_code}")
        except requests.RequestException as e:
            print(f"Connection error (attempt {attempt+1}): {e}")
        time.sleep(1)
    return None

# --- Main function: find inverter and get serial number ---
def get_info():
    ip_list = find_deye_inverters(timeout=1.0, max_scan=50)
    if not ip_list:
        print("[!] No inverters found, retrying...")
        time.sleep(1)
        ip_list = find_deye_inverters(timeout=1.0, max_scan=50)

    for ip in ip_list:
        time.sleep(1)
        cover_mid = get_cover_mid(ip)
        if cover_mid:
            return ip, cover_mid

    return (ip_list[0] if ip_list else None), None

# --- Run the script ---
if __name__ == "__main__":
    ip, cover_mid = get_info()

    if not ip:
        print("[-] Unable to find inverter IP. Make sure it's powered on and connected to the network.")
    elif not cover_mid:
        print("[!] Inverter found at:", ip)
        print("[-] But serial number not found. Check Solarman app or sticker on logger.")
    else:
        print("[+] Inverter found:")
        print("    IP:     ", ip)
        print("    Serial: ", cover_mid)
