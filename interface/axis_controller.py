import asyncio
import time
import keyboard
from gcode_sender import send_gcode


class AxisController:
    """Manages X, Y, Z axis motion control."""
    def __init__(self):
        self.values = {'x': 0, 'y': 0, 'z': 0}
        self.motion_running = False
    
    def handle_input(self, axis_key):
        """Handle axis key input (x, y, or z)."""
        time.sleep(0.05)  # Brief debounce
        
        for num in range(0, 6):
            if keyboard.is_pressed(str(num)):
                self.values[axis_key] = num
                self._send_motion_command()
                
                # Wait for axis key release
                while keyboard.is_pressed(axis_key):
                    time.sleep(0.01)
                time.sleep(0.1)
                return True
        return False
    
    def _send_motion_command(self):
        """Send START_MOTION or CHANGE_MOTION command."""
        cmd_type = "CHANGE_MOTION" if self.motion_running else "START_MOTION"
        gcode = f"{cmd_type} X={self.values['x']} Y={self.values['y']} Z={self.values['z']}"
        print(f"Sending {gcode}")
        asyncio.run(send_gcode(gcode))
        if not self.motion_running:
            self.motion_running = True
    
    def stop(self):
        """Send STOP_MOTION command."""
        if self.motion_running:
            print("Pressed 'q' - sending STOP_MOTION")
            asyncio.run(send_gcode("STOP_MOTION"))
            self.motion_running = False
            self.values = {'x': 0, 'y': 0, 'z': 0}
            
            while keyboard.is_pressed('q'):
                time.sleep(0.01)
            time.sleep(0.1)
