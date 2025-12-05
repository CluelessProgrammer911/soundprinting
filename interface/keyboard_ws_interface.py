import keyboard
import threading
import time
import os
import asyncio
import json
import websockets
import msvcrt  # for clearing buffered keypresses on Windows

MOONRAKER_WS = "ws://192.168.101.8:7125/websocket"
DEFAULT_PLAY_FAN_TIME = 1000  # Default on/off time in ms
DEFAULT_PLAY_FAN_SPEED = 5     # Default speed level

# Global state
listen_mode = False
motion_running = False
debug_mode = False
axis_values = {'x': 0, 'y': 0, 'z': 0}  # Track all axis values in one dict
play_fan_combo_active = False  # Tracks F+U combo state
play_fan_u_time = DEFAULT_PLAY_FAN_TIME  # on_time_ms
play_fan_d_time = DEFAULT_PLAY_FAN_TIME  # off_time_ms
play_fan_speed = DEFAULT_PLAY_FAN_SPEED
last_fan_speed = None  # Track last F{num} used

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

def handle_play_fan_u_input():
    """Prompt for PLAY_FAN on_time_ms (U) after F+U release."""
    global play_fan_u_time
    
    # Wait until F and U are released
    while keyboard.is_pressed('f') or keyboard.is_pressed('u'):
        time.sleep(0.01)

    # Clear buffered key presses
    while msvcrt.kbhit():
        msvcrt.getch()

    try:
        user_input = input(f"Enter PLAY_FAN on_time_ms U (default {DEFAULT_PLAY_FAN_TIME}ms): ").strip()
        if user_input:
            play_fan_u_time = int(user_input)
        else:
            play_fan_u_time = DEFAULT_PLAY_FAN_TIME
    except (ValueError, EOFError):
        print("Invalid input for on_time_ms. Using default.")
        play_fan_u_time = DEFAULT_PLAY_FAN_TIME

    # Send PLAY_FAN command immediately with current U and (current or default) D and S
    send_play_fan_command()

def handle_play_fan_d_input():
    """Prompt for PLAY_FAN off_time_ms (D) after F+D release."""
    global play_fan_d_time
    
    # Wait until F and D are released
    while keyboard.is_pressed('f') or keyboard.is_pressed('d'):
        time.sleep(0.01)

    # Clear buffered key presses
    while msvcrt.kbhit():
        msvcrt.getch()

    try:
        user_input = input(f"Enter PLAY_FAN off_time_ms D (default {DEFAULT_PLAY_FAN_TIME}ms): ").strip()
        if user_input:
            play_fan_d_time = int(user_input)
        else:
            play_fan_d_time = DEFAULT_PLAY_FAN_TIME
    except (ValueError, EOFError):
        print("Invalid input for off_time_ms. Using default.")
        play_fan_d_time = DEFAULT_PLAY_FAN_TIME

    # Send PLAY_FAN command immediately with current D and (current or default) U and S
    send_play_fan_command()

def handle_play_fan_s_input():
    """Prompt for PLAY_FAN speed level (S) after F+S release."""
    global play_fan_speed
    
    # Wait until F and S are released
    while keyboard.is_pressed('f') or keyboard.is_pressed('s'):
        time.sleep(0.01)

    # Clear buffered key presses
    while msvcrt.kbhit():
        msvcrt.getch()

    # Determine default: use last_fan_speed if available, otherwise DEFAULT_PLAY_FAN_SPEED
    default_speed = last_fan_speed if last_fan_speed is not None else DEFAULT_PLAY_FAN_SPEED

    try:
        user_input = input(f"Enter PLAY_FAN speed level S (default {default_speed}): ").strip()
        if user_input:
            play_fan_speed = int(user_input)
            if play_fan_speed < 0 or play_fan_speed > 5:
                print("Invalid speed level. Must be 0-5. Using default.")
                play_fan_speed = default_speed
        else:
            play_fan_speed = default_speed
    except (ValueError, EOFError):
        print("Invalid input for speed level. Using default.")
        play_fan_speed = default_speed

    # Send PLAY_FAN command immediately with current S and (current or default) U and D
    send_play_fan_command()

def send_play_fan_command():
    """Send PLAY_FAN command using current S, U, and D values."""
    gcode = f"PLAY_FAN S={play_fan_speed} U={play_fan_u_time} D={play_fan_d_time}"
    print(f"Sending {gcode}")
    asyncio.run(send_gcode(gcode))

def key_listener():
    """Listen for key presses and send G-code commands"""
    axes = ['x', 'y', 'z']
    global motion_running, axis_values, last_fan_speed
    
    fu_combo_active = False
    fd_combo_active = False
    fs_combo_active = False
    
    while True:
        if listen_mode:
            # Detect F+U combo
            fu_pressed = keyboard.is_pressed('f') and keyboard.is_pressed('u')
            if fu_pressed and not fu_combo_active:
                fu_combo_active = True
                continue
            if not fu_pressed and fu_combo_active:
                fu_combo_active = False
                handle_play_fan_u_input()
                continue

            # Detect F+D combo
            fd_pressed = keyboard.is_pressed('f') and keyboard.is_pressed('d')
            if fd_pressed and not fd_combo_active:
                fd_combo_active = True
                continue
            if not fd_pressed and fd_combo_active:
                fd_combo_active = False
                handle_play_fan_d_input()
                continue

            # Detect F+S combo
            fs_pressed = keyboard.is_pressed('f') and keyboard.is_pressed('s')
            if fs_pressed and not fs_combo_active:
                fs_combo_active = True
                continue
            if not fs_pressed and fs_combo_active:
                fs_combo_active = False
                handle_play_fan_s_input()
                continue

            # Check for stop command
            if keyboard.is_pressed('s'):
                handle_stop()
                continue

            # Check fan control (F + number)
            if keyboard.is_pressed('f'):
                time.sleep(0.05)  # Brief debounce
                for num in range(0, 6):
                    if keyboard.is_pressed(str(num)):
                        gcode = f"TUNE_FAN S={num}"
                        print(f"Pressed 'f{num}' - sending {gcode}")
                        asyncio.run(send_gcode(gcode))
                        last_fan_speed = num  # Track for FS default
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
