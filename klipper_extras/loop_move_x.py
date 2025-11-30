import logging

# Centralized motion presets: (step_distance, speed)
MOTION_SETTINGS = {
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
        self.direction = 1  # 1 = forward, -1 = backward
        self.step_distance = 1.25  # mm per move
        self.speed = 50.0          # mm/s
        self.min_x = -10.0         # Lower bound
        self.max_x = 234.0         # Upper bound

        # Register G-code commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('START_MOTION', self.cmd_START_MOTION,
                               desc="Start continuous X-axis drip motion between bounds")
        gcode.register_command('STOP_MOTION', self.cmd_STOP_MOTION,
                               desc="Stop continuous X-axis drip motion")
        gcode.register_command('CHANGE_MOTION', self.cmd_CHANGE_MOTION,
                               desc="Change motion parameters during loop")

        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')

    def _get_preset(self, x_val):
        """Get motion preset for given X value, or None if invalid."""
        return MOTION_SETTINGS.get(x_val)

    def _apply_preset(self, x_val):
        """Apply motion preset and validate. Returns True if successful."""
        preset = self._get_preset(x_val)
        if preset is None:
            return False
        step, speed = preset
        if step <= 0 or speed <= 0:
            logging.warning("Invalid preset values: step=%s speed=%s", step, speed)
            return False
        self.step_distance, self.speed = step, speed
        return True

    def cmd_START_MOTION(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet.")
            return
        if self.is_running:
            gcmd.respond_info("Loop motion already running.")
            return

        x_val = gcmd.get_int('X', None)
        if not self._apply_preset(x_val):
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return

        self.origin_pos = self.toolhead.get_position()
        self.current_pos = list(self.origin_pos)

        # Determine closest bound to start direction
        dist_to_min = abs(self.current_pos[0] - self.min_x)
        dist_to_max = abs(self.current_pos[0] - self.max_x)
        self.direction = -1 if dist_to_min < dist_to_max else 1

        self.is_running = True
        gcmd.respond_info(f"Starting drip-feed motion: step={self.step_distance}mm, speed={self.speed}mm/s, bounds=({self.min_x},{self.max_x})")
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
        if not self._apply_preset(x_val):
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return

        # Continue from current actual position
        self.current_pos = list(self.toolhead.get_position())
        gcmd.respond_info(f"Motion changed: step={self.step_distance}mm, speed={self.speed}mm/s (continuing from current position)")

    def _schedule_next_move(self, eventtime=None):
        """Schedule next drip move. Accepts eventtime for reactor callback compatibility."""
        if not self.is_running or self.toolhead is None:
            return None

        # Compute next X position
        next_x = self.current_pos[0] + (self.step_distance * self.direction)

        # Clamp to bounds and reverse direction if needed
        if next_x > self.max_x:
            next_x = self.max_x
            self.direction = -1
        elif next_x < self.min_x:
            next_x = self.min_x
            self.direction = 1

        # Build new position: copy current and update X
        new_pos = list(self.current_pos)
        new_pos[0] = next_x

        drip_completion = self.reactor.completion()

        try:
            self.toolhead.drip_move(new_pos, self.speed, drip_completion)
        except Exception:
            logging.exception("Error during drip motion")
            self.is_running = False
            return None

        # Update current position
        self.current_pos = new_pos

        logging.debug("Drip move to X=%.3f, direction=%d", next_x, self.direction)

        # Chain next move: register method directly (accepts eventtime)
        self.reactor.register_callback(self._schedule_next_move, self.reactor.NOW)
        return None

def load_config(config):
    return LoopMoveX(config)
