import keyboard
import time


class KeyListener:
    """Handles keyboard input and dispatches to appropriate controllers."""
    
    def __init__(self, axis_ctrl, fan_ctrl, hotend_fan_ctrl):
        self.axis_ctrl = axis_ctrl
        self.fan_ctrl = fan_ctrl
        self.hotend_fan_ctrl = hotend_fan_ctrl
        self.listen_mode = False
        
        self.axes = ['x', 'y', 'z']
        self.fan_param_keys = ['u', 'd', 's']
        self.hotend_fan_param_keys = ['u', 'd']
    
    def run(self):
        """Main keyboard listening loop."""
        while True:
            if self.listen_mode:
                self._handle_stop()
                self._handle_fan_params()
                self._handle_hotend_fan_params()
                self._handle_hotend_fan_toggle()
                self._handle_fan_tune()
                self._handle_axis_input()
            
            time.sleep(0.01)
    
    def _handle_stop(self):
        """Handle stop command (Q key)."""
        if keyboard.is_pressed('q'):
            self.axis_ctrl.stop()
    
    def _handle_fan_params(self):
        """Handle F+U, F+D, F+S combos for PLAY_FAN."""
        for param_key in self.fan_param_keys:
            combo_pressed = keyboard.is_pressed('f') and keyboard.is_pressed(param_key)
            
            if combo_pressed and not self.fan_ctrl.combo_states[param_key]:
                self.fan_ctrl.combo_states[param_key] = True
                continue
            
            if not combo_pressed and self.fan_ctrl.combo_states[param_key]:
                self.fan_ctrl.combo_states[param_key] = False
                self.fan_ctrl.handle_param_input(param_key)
    
    def _handle_hotend_fan_params(self):
        """Handle H+U, H+D combos for PLAY_HOTEND_FAN."""
        for param_key in self.hotend_fan_param_keys:
            combo_pressed = keyboard.is_pressed('h') and keyboard.is_pressed(param_key)
            
            if combo_pressed and not self.hotend_fan_ctrl.combo_states[param_key]:
                self.hotend_fan_ctrl.combo_states[param_key] = True
                self.hotend_fan_ctrl.h_combo_detected = True
                continue
            
            if not combo_pressed and self.hotend_fan_ctrl.combo_states[param_key]:
                self.hotend_fan_ctrl.combo_states[param_key] = False
                self.hotend_fan_ctrl.handle_param_input(param_key)
    
    def _handle_hotend_fan_toggle(self):
        """Handle H key press for toggling hotend fan."""
        self.hotend_fan_ctrl.handle_h_key()
    
    def _handle_fan_tune(self):
        """Handle F+number input for TUNE_FAN."""
        if keyboard.is_pressed('f'):
            self.fan_ctrl.handle_tune_input()
    
    def _handle_axis_input(self):
        """Handle X/Y/Z+number input for motion control."""
        for axis in self.axes:
            if keyboard.is_pressed(axis):
                self.axis_ctrl.handle_input(axis)
                break
    
    def toggle_listen_mode(self):
        """Toggle listen mode on/off."""
        self.listen_mode = not self.listen_mode
        print(f"Listen mode {'ON' if self.listen_mode else 'OFF'}")
