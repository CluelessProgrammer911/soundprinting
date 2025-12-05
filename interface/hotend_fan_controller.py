import asyncio
import time
import keyboard
import msvcrt
from gcode_sender import send_gcode

DEFAULT_PLAY_HOTEND_FAN_TIME = 1000


class HotendFanController:
    """Manages hotend fan (TOGGLE_HOTEND_FAN and PLAY_HOTEND_FAN)."""
    def __init__(self):
        self.params = {'u': DEFAULT_PLAY_HOTEND_FAN_TIME, 'd': DEFAULT_PLAY_HOTEND_FAN_TIME}
        self.combo_states = {'u': False, 'd': False}
        self.params_set = {'u': False, 'd': False}  # Track if params have been set
        self.h_key_pressed_time = None
        self.h_combo_detected = False
    
    def handle_h_key(self):
        """Handle H key state transitions."""
        h_pressed = keyboard.is_pressed('h')
        
        if h_pressed:
            if self.h_key_pressed_time is None:
                self.h_key_pressed_time = time.time()
        else:
            # H key released
            if self.h_key_pressed_time is not None and not self.h_combo_detected:
                self.toggle()
            self.h_key_pressed_time = None
            self.h_combo_detected = False
    
    def handle_param_input(self, param_key):
        """Handle H + U/D input for PLAY_HOTEND_FAN parameters."""
        while keyboard.is_pressed('h') or keyboard.is_pressed(param_key):
            time.sleep(0.01)
        
        # Clear buffered key presses
        while msvcrt.kbhit():
            msvcrt.getch()
        
        prompt, default = self._get_prompt_and_default(param_key)
        value = self._get_user_input(prompt, default)
        self.params[param_key] = value
        self._send_play_hotend_fan_command()
    
    def _get_prompt_and_default(self, param_key):
        """Get input prompt and default value based on parameter."""
        if param_key == 'u':
            if self.params_set['u']:
                prompt = f"Enter PLAY_HOTEND_FAN on_time_ms U (last {self.params['u']}ms): "
            else:
                prompt = f"Enter PLAY_HOTEND_FAN on_time_ms U (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms): "
            return prompt, DEFAULT_PLAY_HOTEND_FAN_TIME
        else:  # 'd'
            if self.params_set['d']:
                prompt = f"Enter PLAY_HOTEND_FAN off_time_ms D (last {self.params['d']}ms): "
            else:
                prompt = f"Enter PLAY_HOTEND_FAN off_time_ms D (default {DEFAULT_PLAY_HOTEND_FAN_TIME}ms): "
            return prompt, DEFAULT_PLAY_HOTEND_FAN_TIME
    
    def _get_user_input(self, prompt, default):
        """Get and validate user input."""
        try:
            user_input = input(prompt).strip()
            if user_input:
                # Determine which param we're setting based on prompt
                if 'on_time' in prompt:
                    self.params_set['u'] = True
                else:
                    self.params_set['d'] = True
                return int(user_input)
            return default
        except (ValueError, EOFError):
            print("Invalid input. Using default.")
            return default
    
    def toggle(self):
        """Toggle hotend fan on/off."""
        gcode = "TOGGLE_HOTEND_FAN"
        print(f"Pressed 'h' - sending {gcode}")
        asyncio.run(send_gcode(gcode))
        
        while keyboard.is_pressed('h'):
            time.sleep(0.01)
        time.sleep(0.1)
    
    def _send_play_hotend_fan_command(self):
        """Send PLAY_HOTEND_FAN command with current parameters."""
        gcode = f"PLAY_HOTEND_FAN U={self.params['u']} D={self.params['d']}"
        print(f"Sending {gcode}")
        asyncio.run(send_gcode(gcode))
