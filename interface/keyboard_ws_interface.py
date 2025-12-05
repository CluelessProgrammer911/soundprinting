import keyboard
import threading
import time
import os
import asyncio
import json
import websockets
import msvcrt

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"
DEFAULT_PLAY_FAN_TIME = 1000
DEFAULT_PLAY_FAN_SPEED = 5
DEFAULT_PLAY_HOTEND_FAN_TIME = 1000

# Global state
listen_mode = False
motion_running = False
debug_mode = False
axis_values = {'x': 0, 'y': 0, 'z': 0}
play_fan_params = {'u': DEFAULT_PLAY_FAN_TIME, 'd': DEFAULT_PLAY_FAN_TIME, 's': DEFAULT_PLAY_FAN_SPEED}
play_hotend_fan_params = {'u': DEFAULT_PLAY_HOTEND_FAN_TIME, 'd': DEFAULT_PLAY_HOTEND_FAN_TIME}
last_fan_speed = None

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
        print("Pressed 'q' - sending STOP_MOTION")
        asyncio.run(send_gcode("STOP_MOTION"))
        motion_running = False
        axis_values = {'x': 0, 'y': 0, 'z': 0}
        
        while keyboard.is_pressed('q'):
            time.sleep(0.01)
        time.sleep(0.1)

def handle_play_fan_param_input(param_key):
    """Generic handler for PLAY_FAN parameter input (U, D, or S)."""
    global play_fan_params, last_fan_speed
    
    # Wait until F and param key are released
    while keyboard.is_pressed('f') or keyboard.is_pressed(param_key):
        time.sleep(0.01)

    # Clear buffered key presses
    while msvcrt.kbhit():
        msvcrt.getch()

    # Determine prompt and default based on parameter
    param_upper = param_key.upper()
    if param_upper == 'U':
        prompt = f"Enter PLAY_FAN on_time_ms U (default {DEFAULT_PLAY_FAN_TIME}ms): "
        default = DEFAULT_PLAY_FAN_TIME
    elif param_upper == 'D':
        prompt = f"Enter PLAY_FAN off_time_ms D (default {DEFAULT_PLAY_FAN_TIME}ms): "
        default = DEFAULT_PLAY_FAN_TIME
    else:  # S
        default = last_fan_speed if last_fan_speed is not None else DEFAULT_PLAY_FAN_SPEED
        prompt = f"Enter PLAY_FAN speed level S (default {default}): "

    # Get user input
    try:
        user_input = input(prompt).strip()
        if user_input:
            value = int(user_input)
            if param_upper == 'S' and (value < 0 or value > 5):
                print("Invalid speed level. Must be 0-5. Using default.")
                value = default
        else:
            value = default
    except (ValueError, EOFError):
        print(f"Invalid input for {param_upper}. Using default.")
        value = default

    # Update parameter
    play_fan_params[param_key] = value
    
    # Send updated PLAY_FAN command
    send_play_fan_command()

def handle_play_hotend_fan_param_input(param_key):
    """Generic handler for PLAY_HOTEND_FAN parameter input (U or D)."""
    global play_hotend_fan_params
    
    # Wait until H and param key are released
    while keyboard.is_pressed('h') or keyboard.is_pressed(param_key):
        time.sleep(0.01)

    # Clear buffered key presses
    while msvcrt.kbhit():
        msvcrt.getch()

    # Determine prompt and default based on parameter
    param_upper = param_key.upper()
    if param_upper == 'U':
        prompt = f"Enter PLAY_HOTEND_FAN on_time_ms U (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms): "
        default = DEFAULT_PLAY_HOTEND_FAN_TIME
    else:  # D
        prompt = f"Enter PLAY_HOTEND_FAN off_time_ms D (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms): "
        default = DEFAULT_PLAY_HOTEND_FAN_TIME

    # Get user input
    try:
        user_input = input(prompt).strip()
        if user_input:
            value = int(user_input)
        else:
            value = default
    except (ValueError, EOFError):
        print(f"Invalid input for {param_upper}. Using default.")
        value = default

    # Update parameter
    play_hotend_fan_params[param_key] = value
    
    # Send updated PLAY_HOTEND_FAN command
    send_play_hotend_fan_command()

def send_play_fan_command():
    """Send PLAY_FAN command using current parameters."""
    gcode = f"PLAY_FAN S={play_fan_params['s']} U={play_fan_params['u']} D={play_fan_params['d']}"
    print(f"Sending {gcode}")
    asyncio.run(send_gcode(gcode))

def send_play_hotend_fan_command():
    """Send PLAY_HOTEND_FAN command using current parameters."""
    gcode = f"PLAY_HOTEND_FAN U={play_hotend_fan_params['u']} D={play_hotend_fan_params['d']}"
    print(f"Sending {gcode}")
    asyncio.run(send_gcode(gcode))

def handle_toggle_hotend_fan():
    """Handle hotend fan toggle command."""
    gcode = "TOGGLE_HOTEND_FAN"
    print(f"Pressed 'h' - sending {gcode}")
    asyncio.run(send_gcode(gcode))
    
    # Wait for H key release
    while keyboard.is_pressed('h'):
        time.sleep(0.01)
    time.sleep(0.1)

def key_listener():
    """Listen for key presses and send G-code commands"""
    axes = ['x', 'y', 'z']
    play_fan_params_keys = ['u', 'd', 's']
    play_hotend_fan_params_keys = ['u', 'd']
    global motion_running, axis_values, last_fan_speed, play_fan_params, play_hotend_fan_params
    
    # Track combo states
    play_fan_combo_states = {key: False for key in play_fan_params_keys}
    play_hotend_fan_combo_states = {key: False for key in play_hotend_fan_params_keys}
    h_key_pressed_time = None
    h_combo_detected = False
    
    while True:
        if listen_mode:
            # Check for stop command
            if keyboard.is_pressed('q'):
                handle_stop()
                continue

            # Detect F+{U,D,S} combos (check BEFORE single F+number detection)
            combo_detected = False
            for param_key in play_fan_params_keys:
                combo_pressed = keyboard.is_pressed('f') and keyboard.is_pressed(param_key)
                if combo_pressed and not play_fan_combo_states[param_key]:
                    play_fan_combo_states[param_key] = True
                    combo_detected = True
                    continue
                if not combo_pressed and play_fan_combo_states[param_key]:
                    play_fan_combo_states[param_key] = False
                    handle_play_fan_param_input(param_key)
                    combo_detected = True
                    continue
            
            # Detect H+{U,D} combos for hotend fan
            h_pressed = keyboard.is_pressed('h')
            if h_pressed:
                if h_key_pressed_time is None:
                    h_key_pressed_time = time.time()
                
                # Check for H+U or H+D combo
                for param_key in play_hotend_fan_params_keys:
                    combo_pressed = keyboard.is_pressed('h') and keyboard.is_pressed(param_key)
                    if combo_pressed and not play_hotend_fan_combo_states[param_key]:
                        play_hotend_fan_combo_states[param_key] = True
                        h_combo_detected = True
                        combo_detected = True
                        continue
                    if not combo_pressed and play_hotend_fan_combo_states[param_key]:
                        play_hotend_fan_combo_states[param_key] = False
                        handle_play_hotend_fan_param_input(param_key)
                        combo_detected = True
                        continue
            else:
                # H key released
                if h_key_pressed_time is not None and not h_combo_detected:
                    # Was H press without combo - toggle hotend fan
                    handle_toggle_hotend_fan()
                h_key_pressed_time = None
                h_combo_detected = False
            
            if combo_detected:
                continue

            # Check fan control (F + number) - only if no combo detected
            if keyboard.is_pressed('f'):
                time.sleep(0.05)
                for num in range(0, 6):
                    if keyboard.is_pressed(str(num)):
                        gcode = f"TUNE_FAN S={num}"
                        print(f"Pressed 'f{num}' - sending {gcode}")
                        asyncio.run(send_gcode(gcode))
                        last_fan_speed = num
                        play_fan_params['s'] = num
                        while keyboard.is_pressed('f'):
                            time.sleep(0.01)
                        time.sleep(0.1)
                        break
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
    print(f"Hold F+U to enter PLAY_FAN on_time_ms U (default {DEFAULT_PLAY_FAN_TIME}ms).")
    print(f"Hold F+D to enter PLAY_FAN off_time_ms D (default {DEFAULT_PLAY_FAN_TIME}ms).")
    print(f"Hold F+S to enter PLAY_FAN speed level S (default {DEFAULT_PLAY_FAN_SPEED}).")
    print("Press 'h' to toggle hotend fan on/off.")
    print(f"Hold H+U to enter PLAY_HOTEND_FAN on_time_ms U (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms).")
    print(f"Hold H+D to enter PLAY_HOTEND_FAN off_time_ms D (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms).")
    print("Press 'q' to stop motion.")
    print("Press ESC to stop the script completely.")

    keyboard.add_hotkey('ctrl+shift+l', toggle_mode)
    keyboard.add_hotkey('esc', lambda: os._exit(0))

    t = threading.Thread(target=key_listener, daemon=True)
    t.start()

    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
