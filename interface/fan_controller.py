import asyncio
import time
import keyboard
import msvcrt
from gcode_sender import send_gcode

DEFAULT_PLAY_FAN_TIME = 1000
DEFAULT_PLAY_FAN_SPEED = 5


class FanController:
    """Manages part cooling fan (TUNE_FAN and PLAY_FAN)."""
    def __init__(self):
        self.params = {'u': DEFAULT_PLAY_FAN_TIME, 'd': DEFAULT_PLAY_FAN_TIME, 's': DEFAULT_PLAY_FAN_SPEED}
        self.last_speed = None
        self.combo_states = {'u': False, 'd': False, 's': False}
    
    def handle_tune_input(self):
        """Handle F + number input for TUNE_FAN."""
        time.sleep(0.05)
        for num in range(0, 6):
            if keyboard.is_pressed(str(num)):
                gcode = f"TUNE_FAN S={num}"
                print(f"Pressed 'f{num}' - sending {gcode}")
                asyncio.run(send_gcode(gcode))
                self.last_speed = num
                self.params['s'] = num
                
                while keyboard.is_pressed('f'):
                    time.sleep(0.01)
                time.sleep(0.1)
                break
    
    def handle_param_input(self, param_key):
        """Handle F + U/D/S input for PLAY_FAN parameters."""
        while keyboard.is_pressed('f') or keyboard.is_pressed(param_key):
            time.sleep(0.01)
        
        # Clear buffered key presses
        while msvcrt.kbhit():
            msvcrt.getch()
        
        prompt, default = self._get_prompt_and_default(param_key)
        value = self._get_user_input(prompt, default, param_key)
        self.params[param_key] = value
        self._send_play_fan_command()
    
    def _get_prompt_and_default(self, param_key):
        """Get input prompt and default value based on parameter."""
        if param_key == 'u':
            return f"Enter PLAY_FAN on_time_ms U (default {DEFAULT_PLAY_FAN_TIME}ms): ", DEFAULT_PLAY_FAN_TIME
        elif param_key == 'd':
            return f"Enter PLAY_FAN off_time_ms D (default {DEFAULT_PLAY_FAN_TIME}ms): ", DEFAULT_PLAY_FAN_TIME
        else:  # 's'
            default = self.last_speed if self.last_speed is not None else DEFAULT_PLAY_FAN_SPEED
            return f"Enter PLAY_FAN speed level S (default {default}): ", default
    
    def _get_user_input(self, prompt, default, param_key):
        """Get and validate user input."""
        try:
            user_input = input(prompt).strip()
            if user_input:
                value = int(user_input)
                if param_key == 's' and (value < 0 or value > 5):
                    print("Invalid speed level. Must be 0-5. Using default.")
                    return default
                return value
            return default
        except (ValueError, EOFError):
            print(f"Invalid input. Using default.")
            return default
    
    def _send_play_fan_command(self):
        """Send PLAY_FAN command with current parameters."""
        gcode = f"PLAY_FAN S={self.params['s']} U={self.params['u']} D={self.params['d']}"
        print(f"Sending {gcode}")
        asyncio.run(send_gcode(gcode))
