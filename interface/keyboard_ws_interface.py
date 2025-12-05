import keyboard
import threading
import time
import os
import asyncio
import json
import websockets
import msvcrt  # for clearing buffered keypresses on Windows

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"

# Global state
listen_mode = False
motion_running = False
debug_mode = False
axis_values = {'x': 0, 'y': 0, 'z': 0}  # Track all axis values in one dict
play_fan_combo_active = False  # Tracks F+U combo state

def toggle_mode():
    global listen_mode
    listen_mode = not listen_mode
    print(f"Listen mode {'ON' if listen_mode else 'OFF'}")

async def send_gcode(script):
    """Send G-code command via Moonraker WebSocket"""
    try:
        async with websockets.connect(MOONRAKER_WS) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "printer.gcode.script",
                "params": {"script": script},
                "id": 1
            }))
            resp = await ws.recv()
            if debug_mode:
                print(f"G-code response: {json.loads(resp)}")
    except Exception as e:
        print(f"Error sending G-code: {e}")

def handle_axis_input(axis_key):
    """Generic handler for any axis (x, y, or z) input"""
    global motion_running, axis_values
    
    time.sleep(0.05)  # Brief debounce
    
    # Check for number keys 0-5
    for num in range(0, 6):
        if keyboard.is_pressed(str(num)):
            axis_values[axis_key] = num
            
            # Build motion command with all current axis values
            cmd_type = "CHANGE_MOTION" if motion_running else "START_MOTION"
            gcode = f"{cmd_type} X={axis_values['x']} Y={axis_values['y']} Z={axis_values['z']}"
            
            print(f"Pressed '{axis_key}{num}' - sending {gcode}")
            asyncio.run(send_gcode(gcode))
            
            if not motion_running:
                motion_running = True
            
            # Wait for axis key release
            while keyboard.is_pressed(axis_key):
                time.sleep(0.01)
            time.sleep(0.1)  # Debounce after release
            return True
    
    return False

def handle_fan_input(fan_key):
    """Generic handler for fan control input"""
    time.sleep(0.05)  # Brief debounce
    
    # Check for number keys 0-5
    for num in range(0, 6):
        if keyboard.is_pressed(str(num)):
            gcode = f"TUNE_FAN S={num}"
            print(f"Pressed '{fan_key}{num}' - sending {gcode}")
            asyncio.run(send_gcode(gcode))
            
            # Wait for fan key release
            while keyboard.is_pressed(fan_key):
                time.sleep(0.01)
            time.sleep(0.1)  # Debounce after release
            return True
    
    return False

def handle_stop():
    """Handle stop command"""
    global motion_running, axis_values
    
    if motion_running:
        print("Pressed 's' - sending STOP_MOTION")
        asyncio.run(send_gcode("STOP_MOTION"))
        motion_running = False
        axis_values = {'x': 0, 'y': 0, 'z': 0}
        
        while keyboard.is_pressed('s'):
            time.sleep(0.01)
        time.sleep(0.1)

def handle_play_fan_input():
    """Prompt for PLAY_FAN on_time_ms (S=5, D=500) after F+U release."""
    # Wait until F and U are released to avoid their characters in input()
    while keyboard.is_pressed('f') or keyboard.is_pressed('u'):
        time.sleep(0.01)

    # Clear any buffered key presses (e.g., the 'fu' characters)
    while msvcrt.kbhit():
        msvcrt.getch()

    try:
        user_input = input("Enter PLAY_FAN on_time_ms (S=5, D=500): ").strip()
        on_time_ms = int(user_input)
    except (ValueError, EOFError):
        print("Invalid input for on_time_ms. Expected integer.")
        return True

    gcode = f"PLAY_FAN S=5 U={on_time_ms} D=500"
    print(f"Sending {gcode}")
    asyncio.run(send_gcode(gcode))
    return True

def key_listener():
    """Listen for key presses and send G-code commands"""
    axes = ['x', 'y', 'z']
    global play_fan_combo_active
    
    while True:
        if listen_mode:
            # Detect F+U combo for PLAY_FAN: trigger after release
            combo_pressed = keyboard.is_pressed('f') and keyboard.is_pressed('u')
            if combo_pressed and not play_fan_combo_active:
                play_fan_combo_active = True
                continue
            if not combo_pressed and play_fan_combo_active:
                play_fan_combo_active = False
                handle_play_fan_input()
                continue

            # Check for stop command
            if keyboard.is_pressed('s'):
                handle_stop()
                continue

            # Check fan control (F + number)
            if keyboard.is_pressed('f'):
                handle_fan_input('f')
                continue

            # Check each axis key
            for axis in axes:
                if keyboard.is_pressed(axis):
                    handle_axis_input(axis)
                    break
        
        time.sleep(0.01)

def main():
    print("Press CTRL+SHIFT+L to toggle listen mode.")
    print("Press x0-x5, y0-y5, or z0-z5 while listen mode is ON to START/CHANGE motion.")
    print("Press f0-f5 while listen mode is ON to set fan speed (0=OFF, 5=100%).")
    print("Hold F+U to enter PLAY_FAN on_time_ms (S=5, D=500).")
    print("Press 's' to stop motion.")
    print("Press ESC to stop the script completely.")

    keyboard.add_hotkey('ctrl+shift+l', toggle_mode)
    keyboard.add_hotkey('esc', lambda: os._exit(0))

    t = threading.Thread(target=key_listener, daemon=True)
    t.start()

    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
