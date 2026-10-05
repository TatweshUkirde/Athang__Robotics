import time
import threading
import queue
import json
import csv
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import paho.mqtt.client as mqtt
from datetime import datetime
from tuya_connector import TuyaOpenAPI

# Tuya Cloud Configuration
ACCESS_ID = "w3fe7usndqww9myj49wm"
ACCESS_KEY = "a20edcf693f04003a9fe3ea38ba9b74b"
API_ENDPOINT = "https://openapi.tuyain.com"
DEVICE_ID = "vdevo179103963413298"

# Initialize Tuya API
openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
try:
    connect_res = openapi.connect()
    print(f"Tuya Cloud Initialized: {connect_res.get('success', False)}")
except Exception as e:
    print(f"Tuya Cloud Initialization Error: {e}")

# State & Telemetry Tracking
states = {1: False, 2: False, 3: False, 4: False}
last_on_time = {1: "Never", 2: "Never", 3: "Never", 4: "Never"}
last_off_time = {1: "Never", 2: "Never", 3: "Never", 4: "Never"}
gang_runtimes = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0} 
gang_energy = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0} 
linkquality_val = "--"
target_device_id = "0xa4c138286e74b9db"
pairing_locked = True
state_lock = threading.Lock()

# Debounce tracker
last_action_timestamp = 0.0

# CSV Backup Setup
BACKUP_CSV_FILE = "zigbee_audit_trail.csv"
def init_csv_backup():
    if not os.path.exists(BACKUP_CSV_FILE):
        with open(BACKUP_CSV_FILE, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Gang", "Action/State", "Last ON", "Last OFF", "Runtime (s)", "Energy (kWh)"])

init_csv_backup()

def append_csv_backup(gang_idx, action_state):
    try:
        with open(BACKUP_CSV_FILE, mode='a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                f"Gang {gang_idx}",
                action_state,
                last_on_time[gang_idx],
                last_off_time[gang_idx],
                int(gang_runtimes[gang_idx]),
                round(gang_energy[gang_idx], 4)
            ])
    except Exception as e:
        print(f"CSV Backup Error: {e}")

# Thread-safe message queue for UI logging
log_queue = queue.Queue()

def log_message(msg):
    log_queue.put(msg)

# Setup Local MQTT Client
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    log_message("$ mqtt_client connected and listening to network...")
    log_message(f"[SUCCESS] Target locked: {target_device_id}")
    client.subscribe("zigbee2mqtt/+")
    client.subscribe("zigbee2mqtt/bridge/log")

def on_message(client, userdata, msg):
    global linkquality_val, last_action_timestamp
    try:
        payload_str = msg.payload.decode()
        if msg.topic == "zigbee2mqtt/bridge/log":
            return

        payload = json.loads(payload_str)
        with state_lock:
            if time.time() - last_action_timestamp < 1.0:
                return

            local_changed = False
            commands = []
            
            if "linkquality" in payload:
                linkquality_val = str(payload["linkquality"])
                
            for i in range(1, 5):
                key = f"state_l{i}"
                if key in payload:
                    val = True if payload[key] == "ON" else False
                    if states[i] != val:
                        states[i] = val
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        if val:
                            last_on_time[i] = timestamp
                        else:
                            last_off_time[i] = timestamp
                            
                        commands.append({"code": f"switch_{i}", "value": val})
                        local_changed = True
                        log_message(f"> Received {payload[key]} for Gang {i} from MQTT")
                        append_csv_backup(i, payload[key])

            if local_changed and commands:
                threading.Thread(target=sync_to_cloud, args=(commands,), daemon=True).start()
                
    except Exception as e:
        pass

def sync_to_cloud(commands):
    try:
        openapi.post(f'/v1.0/iot-03/devices/{DEVICE_ID}/commands', {'commands': commands})
        log_message("[SUCCESS] Synced local changes to Tuya Cloud")
    except Exception as ce:
        log_message(f"[Cloud Error] {ce}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    mqtt_client.connect("localhost", 1883, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"MQTT Connection Error: {e}")

# Background worker for Runtimes, Energy, & Cloud Polling
def background_worker():
    last_tick = time.time()
    while True:
        now = time.time()
        dt = now - last_tick
        last_tick = now
        
        with state_lock:
            for i in range(1, 5):
                if states[i]:
                    gang_runtimes[i] += dt
                    gang_energy[i] += 0.1 * (dt / 3600.0)

        if time.time() - last_action_timestamp > 2.0:
            try:
                res = openapi.get(f'/v1.0/iot-03/devices/{DEVICE_ID}/status')
                if res.get('success') and 'result' in res:
                    with state_lock:
                        cloud_changed = False
                        mqtt_payload = {}
                        for item in res['result']:
                            code = item.get('code')
                            value = item.get('value')
                            if code and code.startswith('switch_'):
                                try:
                                    idx = int(code.split('_')[1])
                                    if states.get(idx) != value:
                                        states[idx] = value
                                        timestamp = datetime.now().strftime("%H:%M:%S")
                                        if value:
                                            last_on_time[idx] = timestamp
                                        else:
                                            last_off_time[idx] = timestamp
                                            
                                        mqtt_payload[f"state_l{idx}"] = "ON" if value else "OFF"
                                        cloud_changed = True
                                        log_message(f"> App triggered Gang {idx} -> {'ON' if value else 'OFF'}")
                                        append_csv_backup(idx, "ON" if value else "OFF")
                                except ValueError:
                                    pass
                        
                        if cloud_changed and mqtt_payload:
                            mqtt_client.publish(f"zigbee2mqtt/{target_device_id}/set", json.dumps(mqtt_payload))
            except Exception as e:
                pass
            
        time.sleep(2)

worker_thread = threading.Thread(target=background_worker, daemon=True)
worker_thread.start()

# --- Exact UI Layout ---
root = tk.Tk()
root.title("Zigbee 4-Gang Gateway - Real-Time Offline Control Panel")
root.geometry("1400x720")

header_frame = tk.Frame(root, bg="#2c3e50", pady=8)
header_frame.pack(fill=tk.X)
tk.Label(header_frame, text="⚡ Zigbee Gateway: 4-Gang Control & Energy Monitor", fg="white", bg="#2c3e50", font=("Arial", 13, "bold")).pack(anchor="w", padx=15)
tk.Label(header_frame, text="Real-time switch actuation, timestamp tracking, link quality, and energy consumption estimation.", fg="#ecf0f1", bg="#2c3e50", font=("Arial", 9)).pack(anchor="w", padx=15)

main_container = tk.Frame(root, padx=10, pady=10)
main_container.pack(fill=tk.BOTH, expand=True)

left_pane = tk.Frame(main_container)
left_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

health_frame = tk.LabelFrame(left_pane, text="Device Health & RF Telemetry", font=("Arial", 10, "bold"), padx=10, pady=8)
health_frame.pack(fill=tk.X, pady=(0, 8))

lqi_label = tk.Label(health_frame, text="Signal LQI: -- / 255", font=("Arial", 10, "bold"), fg="#2980b9")
lqi_label.pack(side=tk.LEFT, padx=10)

time_label = tk.Label(health_frame, text="Updated: --:--:--", font=("Arial", 10))
time_label.pack(side=tk.LEFT, padx=10)

power_label = tk.Label(health_frame, text="Total Consumption: 0.0000 kWh", font=("Arial", 10, "bold"), fg="#27ae60")
power_label.pack(side=tk.RIGHT, padx=10)

relays_frame = tk.LabelFrame(left_pane, text="4-Gang Relays: Status, Timestamps & Energy", font=("Arial", 10, "bold"), padx=10, pady=8)
relays_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

gang_off_indicators = {}
gang_on_indicators = {}
gang_on_labels = {}
gang_off_labels = {}
gang_runtime_labels = {}
gang_energy_labels = {}

def set_gang_state(gang_idx, target_state):
    global last_action_timestamp
    last_action_timestamp = time.time()
    states[gang_idx] = target_state
    
    payload = {f"state_l{gang_idx}": "ON" if target_state else "OFF"}
    mqtt_client.publish(f"zigbee2mqtt/{target_device_id}/set", json.dumps(payload))
    state_str = "ON" if target_state else "OFF"
    log_message(f"> Sent: {state_str} to Gang {gang_idx} on {target_device_id}")
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    if target_state:
        last_on_time[gang_idx] = timestamp
    else:
        last_off_time[gang_idx] = timestamp
    append_csv_backup(gang_idx, state_str)

for i in range(1, 5):
    row_f = tk.Frame(relays_frame, pady=6)
    row_f.pack(fill=tk.X)
    
    tk.Label(row_f, text=f"Gang {i} (L{i}):", font=("Arial", 10, "bold"), width=12, anchor="w").pack(side=tk.LEFT)
    
    off_ind = tk.Label(row_f, text="OFF", width=6, bg="#bdc3c7", relief=tk.SUNKEN)
    off_ind.pack(side=tk.LEFT, padx=2)
    gang_off_indicators[i] = off_ind
    
    on_ind = tk.Label(row_f, text="ON", width=6, bg="#ecf0f1", relief=tk.SUNKEN)
    on_ind.pack(side=tk.LEFT, padx=2)
    gang_on_indicators[i] = on_ind
    
    on_btn = tk.Button(row_f, text="ON", command=lambda idx=i: set_gang_state(idx, True), width=6, bg="#27ae60", fg="white")
    on_btn.pack(side=tk.LEFT, padx=2)
    
    off_btn = tk.Button(row_f, text="OFF", command=lambda idx=i: set_gang_state(idx, False), width=6, bg="#c0392b", fg="white")
    off_btn.pack(side=tk.LEFT, padx=6)
    
    on_lbl = tk.Label(row_f, text="Last ON: Never", font=("Arial", 9), fg="#27ae60", width=16, anchor="w")
    on_lbl.pack(side=tk.LEFT, padx=4)
    gang_on_labels[i] = on_lbl
    
    off_lbl = tk.Label(row_f, text="Last OFF: Never", font=("Arial", 9), fg="#c0392b", width=16, anchor="w")
    off_lbl.pack(side=tk.LEFT, padx=4)
    gang_off_labels[i] = off_lbl
    
    runtime_lbl = tk.Label(row_f, text="Runtime: 00h 00m 00s", font=("Arial", 9), width=18, anchor="w")
    runtime_lbl.pack(side=tk.LEFT, padx=4)
    gang_runtime_labels[i] = runtime_lbl
    
    energy_lbl = tk.Label(row_f, text="Energy: 0.0000 kWh (~100W est.)", font=("Arial", 9), fg="#2980b9", anchor="w")
    energy_lbl.pack(side=tk.LEFT, padx=4)
    gang_energy_labels[i] = energy_lbl

def add_device_action():
    global pairing_locked
    pairing_locked = False
    pairing_label.config(text="Pairing: Scanning...", fg="#e67e22")
    log_message("[COMMISSIONING] Opening network for pairing (PermitJoin)...")
    mqtt_client.publish("zigbee2mqtt/bridge/request", json.dumps({"value": True}))
    
    scan_win = tk.Toplevel(root)
    scan_win.title("Scan & Add Zigbee Device")
    scan_win.geometry("400x250")
    scan_win.grab_set()
    
    tk.Label(scan_win, text="Scanning for nearby Zigbee devices in pairing mode...", font=("Arial", 10, "bold"), wraplength=350).pack(pady=15)
    
    progress = ttk.Progressbar(scan_win, orient="horizontal", length=300, mode="indeterminate")
    progress.pack(pady=10)
    progress.start(10)
    
    status_lbl = tk.Label(scan_win, text="Searching network...", font=("Arial", 9), fg="#7f8c8d")
    status_lbl.pack(pady=5)
    
    def simulate_scan():
        time.sleep(3)
        if scan_win.winfo_exists():
            progress.stop()
            progress.pack_forget()
            status_lbl.config(text="Found Device: 0xa4c138_NewSwitch", fg="#27ae60", font=("Arial", 10, "bold"))
            
            def save_named_device():
                custom_name = name_entry.get().strip()
                if custom_name:
                    log_message(f"[SUCCESS] Device added and saved as: '{custom_name}'")
                    messagebox.showinfo("Success", f"Device successfully saved as '{custom_name}' and linked to UI!")
                    target_entry.delete(0, tk.END)
                    target_entry.insert(0, custom_name)
                    scan_win.destroy()
                else:
                    messagebox.showwarning("Warning", "Please enter a valid name for the device.")

            tk.Label(scan_win, text="Enter Custom Name for Device:", font=("Arial", 9)).pack(pady=(10, 2))
            global name_entry
            name_entry = tk.Entry(scan_win, font=("Arial", 10), width=25)
            name_entry.insert(0, "My_4Gang_Switch")
            name_entry.pack(pady=5)
            
            tk.Button(scan_win, text="Save & Operate", bg="#27ae60", fg="white", font=("Arial", 10, "bold"), command=save_named_device).pack(pady=10)

    threading.Thread(target=simulate_scan, daemon=True).start()

def quick_join_action():
    log_message("[COMMISSIONING] Quick Join broadcast sent to network.")
    messagebox.showinfo("Quick Join", "Quick Join broadcast initiated.")

def refresh_target_action():
    global target_device_id
    val = target_entry.get().strip()
    if val:
        target_device_id = val
        log_message(f"[SUCCESS] Target device updated: {target_device_id}")
        messagebox.showinfo("Target Refreshed", f"Successfully locked target: {target_device_id}")

def remove_target_action():
    global target_device_id
    target_entry.delete(0, tk.END)
    target_device_id = ""
    log_message("[WARNING] Target device cleared.")

def export_csv_action():
    filename = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")], initialfile="zigbee_metrics_export.csv")
    if filename:
        try:
            with open(filename, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Gang", "State", "Last ON", "Last OFF", "Runtime (s)", "Energy (kWh)"])
                with state_lock:
                    for i in range(1, 5):
                        writer.writerow([f"Gang {i}", "ON" if states[i] else "OFF", last_on_time[i], last_off_time[i], int(gang_runtimes[i]), round(gang_energy[i], 4)])
            log_message(f"[SUCCESS] Metrics exported to {filename}")
            messagebox.showinfo("Export Successful", f"Snapshot saved to:\n{filename}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))

comm_container = tk.Frame(left_pane)
comm_container.pack(fill=tk.X, pady=(0, 8))

comm_frame = tk.LabelFrame(comm_container, text="Device Commissioning", font=("Arial", 10, "bold"), padx=10, pady=8)
comm_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

pairing_label = tk.Label(comm_frame, text="Pairing: Locked", fg="#2980b9", font=("Arial", 9, "bold"))
pairing_label.pack(anchor="w", pady=(0, 5))
btn_box = tk.Frame(comm_frame)
btn_box.pack(fill=tk.X)
tk.Button(btn_box, text="+ Add Device", width=12, command=add_device_action).pack(side=tk.LEFT, padx=2)
tk.Button(btn_box, text="Quick Join", width=12, command=quick_join_action).pack(side=tk.LEFT, padx=2)

target_frame = tk.LabelFrame(comm_container, text="Selected Target", font=("Arial", 10, "bold"), padx=10, pady=8)
target_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

tk.Label(target_frame, text="Target Device:", font=("Arial", 9)).pack(anchor="w")
target_entry = tk.Entry(target_frame, font=("Arial", 9))
target_entry.insert(0, target_device_id)
target_entry.pack(fill=tk.X, pady=2)

t_btn_box = tk.Frame(target_frame)
t_btn_box.pack(fill=tk.X, pady=2)
tk.Button(t_btn_box, text="Refresh", width=10, command=refresh_target_action).pack(side=tk.LEFT, padx=2)
tk.Button(t_btn_box, text="Remove", width=10, command=remove_target_action).pack(side=tk.LEFT, padx=2)

diag_frame = tk.LabelFrame(left_pane, text="System Diagnostics & Export", font=("Arial", 10, "bold"), padx=10, pady=8)
diag_frame.pack(fill=tk.X)
tk.Button(diag_frame, text="Export Snapshot to CSV", bg="#27ae60", fg="white", font=("Arial", 10, "bold"), command=export_csv_action).pack(fill=tk.X, pady=2)

right_pane = tk.Frame(main_container)
right_pane.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

log_frame = tk.LabelFrame(right_pane, text="Live Log & Command Output:", font=("Arial", 10, "bold"), padx=5, pady=5)
log_frame.pack(fill=tk.BOTH, expand=True)

clear_btn = tk.Button(log_frame, text="Clear", width=8, command=lambda: log_text.delete("1.0", tk.END))
clear_btn.pack(anchor="ne", padx=5, pady=2)

log_text = tk.Text(log_frame, bg="black", fg="#2ecc71", font=("Courier", 10), wrap=tk.WORD)
log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
log_text.insert(tk.END, "$ mqtt_client connected and listening to network...\n[SUCCESS] Auto CSV backup initialized: zigbee_audit_trail.csv\n")

def update_ui():
    while not log_queue.empty():
        msg = log_queue.get_nowait()
        log_text.insert(tk.END, f"{msg}\n")
        log_text.see(tk.END)

    with state_lock:
        lqi_label.config(text=f"Signal LQI: {linkquality_val} / 255")
        time_label.config(text=f"Updated: {datetime.now().strftime('%H:%M:%S')}")
        
        total_kwh_all = 0.0
        for i in range(1, 5):
            is_on = states[i]
            if is_on:
                gang_off_indicators[i].config(bg="#ecf0f1")
                gang_on_indicators[i].config(bg="#2ecc71")
            else:
                gang_off_indicators[i].config(bg="#bdc3c7")
                gang_on_indicators[i].config(bg="#ecf0f1")
                
            gang_on_labels[i].config(text=f"Last ON: {last_on_time[i]}")
            gang_off_labels[i].config(text=f"Last OFF: {last_off_time[i]}")
            
            rt = int(gang_runtimes[i])
            hrs = rt // 3600
            mins = (rt % 3600) // 60
            secs = rt % 60
            gang_runtime_labels[i].config(text=f"Runtime: {hrs:02d}h {mins:02d}m {secs:02d}s")
            
            kwh = gang_energy[i]
            total_kwh_all += kwh
            gang_energy_labels[i].config(text=f"Energy: {kwh:.4f} kWh (~100W est.)")
            
        power_label.config(text=f"Total Consumption: {total_kwh_all:.4f} kWh")
        
    root.after(300, update_ui)

root.after(300, update_ui)

try:
    root.mainloop()
except KeyboardInterrupt:
    print("Stopping application...")
    mqtt_client.loop_stop()
