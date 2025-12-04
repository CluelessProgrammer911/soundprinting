import keyboard
import threading
import time
import os
import asyncio
import json
import websockets

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"

listen_mode = False
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
            print(f"G-code response: {result}")
    except Exception as e:
        print(f"Error sending G-code: {e}")

def key_listener():
    """Listen for key presses and send G-code commands"""
    while True:
        if listen_mode:
            # Wait for 'x' key press
            if keyboard.is_pressed('x'):
                time.sleep(0.05)  # Brief debounce
                # Now check for number keys 1-5
                for num in range(1, 6):
                    key_name = str(num)
                    if keyboard.is_pressed(key_name):
                        print(f"Pressed 'x{num}' - sending START_MOTION X={num} Y=0 Z=0")
                        asyncio.run(send_gcode(f"START_MOTION X={num} Y=0 Z=0"))
                        # Wait for x key release
                        while keyboard.is_pressed('x'):
                            time.sleep(0.01)
                        time.sleep(0.1)  # Debounce after release
                        break
        
        time.sleep(0.01)

def main():
    print("Press CTRL+SHIFT+L to toggle listen mode.")
    print("Press x1-x5 while listen mode is ON to send START_MOTION with different X presets.")
    print("Press ESC to stop the script completely.")

    keyboard.add_hotkey('ctrl+shift+l', toggle_mode)
    keyboard.add_hotkey('esc', lambda: os._exit(0))

    t = threading.Thread(target=key_listener, daemon=True)
    t.start()

    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
