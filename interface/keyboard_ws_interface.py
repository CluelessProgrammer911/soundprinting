import keyboard
import threading
import time
import os
import asyncio
import json
import websockets

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"

listen_mode = False
motion_running = False
debug_mode = False  # Set to True to see detailed G-code responses
current_x = 0
current_y = 0
current_z = 0
ws_connection = None

def toggle_mode():
    global listen_mode
    listen_mode = not listen_mode
    print(f"Listen mode {'ON' if listen_mode else 'OFF'}")

async def send_gcode(script):
    """Send G-code command via Moonraker WebSocket"""
    global ws_connection
    try:
        async with websockets.connect(MOONRAKER_WS) as ws:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "method": "printer.gcode.script",
                "params": {"script": script},
                "id": 1
            }))
            resp = await ws.recv()
            result = json.loads(resp)
            if debug_mode:
                print(f"G-code response: {result}")
    except Exception as e:
        print(f"Error sending G-code: {e}")

def key_listener():
    """Listen for key presses and send G-code commands"""
    global motion_running, current_x, current_y, current_z
    
    while True:
        if listen_mode:
            # Check for stop command ('s' key)
            if keyboard.is_pressed('s'):
                if motion_running:
                    print("Pressed 's' - sending STOP_MOTION")
                    asyncio.run(send_gcode("STOP_MOTION"))
                    motion_running = False
                    current_x = 0
                    current_y = 0
                    current_z = 0
                    while keyboard.is_pressed('s'):
                        time.sleep(0.01)
                    time.sleep(0.1)
            
            # Wait for 'x' key press
            if keyboard.is_pressed('x'):
                time.sleep(0.05)  # Brief debounce
                # Now check for number keys 0-5
                for num in range(0, 6):
                    key_name = str(num)
                    if keyboard.is_pressed(key_name):
                        current_x = num
                        if motion_running:
                            # Motion already running - send CHANGE_MOTION
                            print(f"Pressed 'x{num}' - sending CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}"))
                        else:
                            # Motion not running - send START_MOTION
                            print(f"Pressed 'x{num}' - sending START_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"START_MOTION X={current_x} Y={current_y} Z={current_z}"))
                            motion_running = True
                        # Wait for x key release
                        while keyboard.is_pressed('x'):
                            time.sleep(0.01)
                        time.sleep(0.1)  # Debounce after release
                        break
            
            # Wait for 'y' key press
            if keyboard.is_pressed('y'):
                time.sleep(0.05)  # Brief debounce
                # Now check for number keys 0-5
                for num in range(0, 6):
                    key_name = str(num)
                    if keyboard.is_pressed(key_name):
                        current_y = num
                        if motion_running:
                            # Motion already running - send CHANGE_MOTION
                            print(f"Pressed 'y{num}' - sending CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}"))
                        else:
                            # Motion not running - send START_MOTION
                            print(f"Pressed 'y{num}' - sending START_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"START_MOTION X={current_x} Y={current_y} Z={current_z}"))
                            motion_running = True
                        # Wait for y key release
                        while keyboard.is_pressed('y'):
                            time.sleep(0.01)
                        time.sleep(0.1)  # Debounce after release
                        break
            
            # Wait for 'z' key press
            if keyboard.is_pressed('z'):
                time.sleep(0.05)  # Brief debounce
                # Now check for number keys 0-5
                for num in range(0, 6):
                    key_name = str(num)
                    if keyboard.is_pressed(key_name):
                        current_z = num
                        if motion_running:
                            # Motion already running - send CHANGE_MOTION
                            print(f"Pressed 'z{num}' - sending CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"CHANGE_MOTION X={current_x} Y={current_y} Z={current_z}"))
                        else:
                            # Motion not running - send START_MOTION
                            print(f"Pressed 'z{num}' - sending START_MOTION X={current_x} Y={current_y} Z={current_z}")
                            asyncio.run(send_gcode(f"START_MOTION X={current_x} Y={current_y} Z={current_z}"))
                            motion_running = True
                        # Wait for z key release
                        while keyboard.is_pressed('z'):
                            time.sleep(0.01)
                        time.sleep(0.1)  # Debounce after release
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
