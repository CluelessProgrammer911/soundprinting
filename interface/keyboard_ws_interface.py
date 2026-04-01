import keyboard
import threading
import time
import os

from axis_controller import AxisController
from fan_controller import FanController
from hotend_fan_controller import HotendFanController
from key_listener import KeyListener


def main():
    print("Press CTRL+SHIFT+L to toggle listen mode.")
    print("Press x0-x5, y0-y5, or z0-z5 while listen mode is ON to START/CHANGE motion.")
    print("Press f0-f5 while listen mode is ON to set fan speed (0=OFF, 5=100%).")
    print("Hold F+U to enter PLAY_FAN on_time_ms U (default 1000ms).")
    print("Hold F+D to enter PLAY_FAN off_time_ms D (default 1000ms).")
    print("Hold F+S to enter PLAY_FAN speed level S (default 5).")
    print("Press 'h' to toggle hotend fan on/off.")
    print("Hold H+U to enter PLAY_HOTEND_FAN on_time_ms U (default 1000ms).")
    print("Hold H+D to enter PLAY_HOTEND_FAN off_time_ms D (default 1000ms).")
    print("Press 'q' to stop motion.")
    print("Press ESC to stop the script completely.")

    # Create controller instances
    axis_ctrl = AxisController()
    fan_ctrl = FanController()
    hotend_fan_ctrl = HotendFanController()
    key_listener = KeyListener(axis_ctrl, fan_ctrl, hotend_fan_ctrl)

    # Register hotkey for toggling listen mode
    keyboard.add_hotkey('ctrl+shift+l', key_listener.toggle_listen_mode)
    keyboard.add_hotkey('esc', lambda: os._exit(0))

    # Start keyboard listener thread
    t = threading.Thread(target=key_listener.run, daemon=True)
    t.start()

    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
