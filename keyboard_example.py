import keyboard
import threading
import time
import os

listen_mode = False

def toggle_mode():
    global listen_mode
    listen_mode = not listen_mode
    print(f"Listen mode {'ON' if listen_mode else 'OFF'}")

def key_listener():
    while True:
        if listen_mode and keyboard.is_pressed('k'):
            print("pressed k")
            keyboard.wait('k')
        time.sleep(0.01)

def main():
    print("Press CTRL+SHIFT+L to toggle listen mode.")
    print("Press 'k' while listen mode is ON to print message.")
    print("Press ESC to stop the script completely.")

    keyboard.add_hotkey('ctrl+shift+l', toggle_mode)

    # 🔴 Hard stop of the entire process
    keyboard.add_hotkey('esc', lambda: os._exit(0))

    t = threading.Thread(target=key_listener, daemon=True)
    t.start()

    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
