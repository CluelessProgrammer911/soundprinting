import keyboard
import threading
import time
import os
import asyncio
import json
import websockets

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"

# Global state
listen_mode = False
motion_running = False
debug_mode = False
axis_values = {'x': 0, 'y': 0, 'z': 0}  # Track all axis values in one dict

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

def key_listener():
    """Listen for key presses and send G-code commands"""
    axes = ['x', 'y', 'z']
    
    while True:
        if listen_mode:
            # Check for stop command
            if keyboard.is_pressed('s'):
                handle_stop()
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
