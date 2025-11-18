import keyboard  # pip install keyboard
import threading
import time

listen_mode = False  # Toggleable mode

def toggle_mode():
    global listen_mode
    listen_mode = not listen_mode
    print(f"Listen mode {'ON' if listen_mode else 'OFF'}")

def key_listener():
    while True:
        if listen_mode and keyboard.is_pressed('k'):
            print("pressed k")
            # Prevent continuous spam while holding k
            keyboard.wait('k')  
        time.sleep(0.01)  # Small delay to reduce CPU load

def main():
    print("Press CTRL+SHIFT+L to toggle listen mode.")
    print("Press 'k' while listen mode is ON to print message.")
    print("Press ESC to exit.")

    # Hotkey to toggle listening mode
    keyboard.add_hotkey('ctrl+shift+l', toggle_mode)

    # Hotkey to exit debug loop
    keyboard.add_hotkey('esc', lambda: exit(0))

    # Start listener thread
    t = threading.Thread(target=key_listener, daemon=True)
    t.start()

    # Keep script alive
    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
