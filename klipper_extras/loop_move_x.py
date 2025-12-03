import logging

# Centralized motion presets: (step_distance, speed)
MOTION_SETTINGS_XY = {
    1: (0.45, 10.0),
    2: (0.80, 20.0),
    3: (1.05, 30.0),
    4: (1.20, 40.0),
    5: (1.25, 50.0)
}

class LoopMoveX:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
        self.is_running = False
        self.origin_pos = None
        self.current_pos = None
        
        # X-axis parameters
        self.direction_x = 1
        self.step_distance_x = 1.25
        self.speed_x = 50.0
        self.min_x = -10.0
        self.max_x = 234.0
        
        # Y-axis parameters
        self.direction_y = 1
        self.step_distance_y = 1.25
        self.speed_y = 50.0
        self.min_y = -7.0
        self.max_y = 234.0

        # Register G-code commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('START_MOTION', self.cmd_START_MOTION,
                               desc="Start continuous X-Y axis drip motion between bounds")
        gcode.register_command('STOP_MOTION', self.cmd_STOP_MOTION,
                               desc="Stop continuous X-Y axis drip motion")
        gcode.register_command('CHANGE_MOTION', self.cmd_CHANGE_MOTION,
                               desc="Change motion parameters during loop")

        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')

    def _get_preset(self, val):
        """Get motion preset for given value, or None if invalid."""
        return MOTION_SETTINGS_XY.get(val)

    def _apply_preset_x(self, x_val):
        """Apply X-axis motion preset and validate. Returns True if successful."""
        preset = self._get_preset(x_val)
        if preset is None:
            return False
        step, speed = preset
        if step <= 0 or speed <= 0:
            logging.warning("Invalid X preset values: step=%s speed=%s", step, speed)
            return False
        self.step_distance_x, self.speed_x = step, speed
        return True

    def _apply_preset_y(self, y_val):
        """Apply Y-axis motion preset and validate. Returns True if successful."""
        preset = self._get_preset(y_val)
        if preset is None:
            return False
        step, speed = preset
        if step <= 0 or speed <= 0:
            logging.warning("Invalid Y preset values: step=%s speed=%s", step, speed)
            return False
        self.step_distance_y, self.speed_y = step, speed
        return True

    def cmd_START_MOTION(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet.")
            return
        if self.is_running:
            gcmd.respond_info("Loop motion already running.")
            return

        x_val = gcmd.get_int('X', None)
        y_val = gcmd.get_int('Y', None)
        
        if not self._apply_preset_x(x_val):
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return
        
        if not self._apply_preset_y(y_val):
            gcmd.respond_info("Invalid Y value. Use Y=1..5.")
            return

        self.origin_pos = self.toolhead.get_position()
        self.current_pos = list(self.origin_pos)

        # Determine closest bound for X to start direction
        dist_to_min_x = abs(self.current_pos[0] - self.min_x)
        dist_to_max_x = abs(self.current_pos[0] - self.max_x)
        self.direction_x = -1 if dist_to_min_x < dist_to_max_x else 1

        # Determine closest bound for Y to start direction
        dist_to_min_y = abs(self.current_pos[1] - self.min_y)
        dist_to_max_y = abs(self.current_pos[1] - self.max_y)
        self.direction_y = -1 if dist_to_min_y < dist_to_max_y else 1

        self.is_running = True
        gcmd.respond_info(f"Starting drip-feed motion: X[step={self.step_distance_x}mm, speed={self.speed_x}mm/s, bounds=({self.min_x},{self.max_x})], Y[step={self.step_distance_y}mm, speed={self.speed_y}mm/s, bounds=({self.min_y},{self.max_y})]")
        self._schedule_next_move()

    def cmd_STOP_MOTION(self, gcmd):
        if not self.is_running:
            gcmd.respond_info("Loop motion not active.")
            return
        self.is_running = False
        gcmd.respond_info("Loop motion stopped immediately.")

    def cmd_CHANGE_MOTION(self, gcmd):
        if not self.is_running:
            gcmd.respond_info("Cannot change motion: loop is not running.")
            return

        x_val = gcmd.get_int('X', None)
        y_val = gcmd.get_int('Y', None)
        
        if x_val is not None and not self._apply_preset_x(x_val):
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return
        
        if y_val is not None and not self._apply_preset_y(y_val):
            gcmd.respond_info("Invalid Y value. Use Y=1..5.")
            return

        # Continue from current actual position
        self.current_pos = list(self.toolhead.get_position())
        gcmd.respond_info(f"Motion changed: X[step={self.step_distance_x}mm, speed={self.speed_x}mm/s], Y[step={self.step_distance_y}mm, speed={self.speed_y}mm/s] (continuing from current position)")

    def _schedule_next_move(self, eventtime=None):
        """Schedule next drip move. Accepts eventtime for reactor callback compatibility."""
        if not self.is_running or self.toolhead is None:
            return None

        # Compute next X position
        next_x = self.current_pos[0] + (self.step_distance_x * self.direction_x)

        # Clamp to bounds and reverse direction if needed for X
        if next_x > self.max_x:
            next_x = self.max_x
            self.direction_x = -1
        elif next_x < self.min_x:
            next_x = self.min_x
            self.direction_x = 1

        # Compute next Y position
        next_y = self.current_pos[1] + (self.step_distance_y * self.direction_y)

        # Clamp to bounds and reverse direction if needed for Y
        if next_y > self.max_y:
            next_y = self.max_y
            self.direction_y = -1
        elif next_y < self.min_y:
            next_y = self.min_y
            self.direction_y = 1

        # Build new position: copy current and update X and Y
        new_pos = list(self.current_pos)
        new_pos[0] = next_x
        new_pos[1] = next_y

        # Use the maximum of the two speeds to ensure coordinated motion
        move_speed = max(self.speed_x, self.speed_y)

        drip_completion = self.reactor.completion()

        try:
            self.toolhead.drip_move(new_pos, move_speed, drip_completion)
        except Exception:
            logging.exception("Error during drip motion")
            self.is_running = False
            return None

        # Update current position
        self.current_pos = new_pos

        logging.debug("Drip move to X=%.3f (dir=%d), Y=%.3f (dir=%d)", next_x, self.direction_x, next_y, self.direction_y)

        # Chain next move: register method directly (accepts eventtime)
        self.reactor.register_callback(self._schedule_next_move, self.reactor.NOW)
        return None

def load_config(config):
    return LoopMoveX(config)
