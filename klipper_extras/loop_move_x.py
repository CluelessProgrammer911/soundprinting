import logging

# Centralized motion presets: (step_distance, speed)
MOTION_SETTINGS_XY = {
    0: (0.0, 0.0),
    1: (0.45, 10.0),
    2: (0.80, 20.0),
    3: (1.05, 30.0),
    4: (1.20, 40.0),
    5: (1.25, 50.0)
}

MOTION_SETTINGS_Z = {
    0: (0.0, 0.0),
    1: (0.023, 0.5),
    2: (0.04, 1),
    3: (0.053, 1.5),
    4: (0.06, 2),
    5: (0.063, 2.5)
}

class AxisConfig:
    """Configuration for a single axis."""
    def __init__(self, index, name, domain, settings_dict, default_step=1.25, default_speed=50.0):
        self.index = index  # Position index in toolhead coords (0=X, 1=Y, 2=Z)
        self.name = name
        self.domain = domain  # (min, max) tuple
        self.settings_dict = settings_dict
        self.step_distance = default_step
        self.speed = default_speed
        self.direction = 1

    def apply_preset(self, val):
        """Apply motion preset and validate. Returns True if successful."""
        preset = self.settings_dict.get(val)
        if preset is None:
            return False
        step, speed = preset
        if step < 0 or speed < 0:  # Allow 0 for stationary axes
            logging.warning("Invalid %s preset values: step=%s speed=%s", self.name, step, speed)
            return False
        self.step_distance, self.speed = step, speed
        return True

    def determine_initial_direction(self, current_pos):
        """Determine closest bound to start direction."""
        dist_to_min = abs(current_pos - self.domain[0])
        dist_to_max = abs(current_pos - self.domain[1])
        self.direction = -1 if dist_to_min < dist_to_max else 1

    def compute_next_position(self, current_pos):
        """Compute next position and update direction if bounds are hit."""
        # If step_distance is 0, axis is stationary
        if self.step_distance == 0:
            return current_pos
        
        next_pos = current_pos + (self.step_distance * self.direction)
        
        # Clamp to bounds and reverse direction if needed
        if next_pos > self.domain[1]:
            next_pos = self.domain[1]
            self.direction = -1
        elif next_pos < self.domain[0]:
            next_pos = self.domain[0]
            self.direction = 1
        
        return next_pos

    def get_info_string(self):
        """Get formatted info string for this axis."""
        return f"{self.name}[step={self.step_distance}mm, speed={self.speed}mm/s, bounds={self.domain}]"


class LoopMoveX:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
        self.is_running = False
        self.origin_pos = None
        self.current_pos = None
        
        # Configure axes
        self.axes = {
            'X': AxisConfig(0, 'X', (-10.0, 234.0), MOTION_SETTINGS_XY, 1.25, 50.0),
            'Y': AxisConfig(1, 'Y', (-8.0, 234.0), MOTION_SETTINGS_XY, 1.25, 50.0),
            'Z': AxisConfig(2, 'Z', (2.0, 270.0), MOTION_SETTINGS_Z, 0.063, 2.5)
        }

        # Register G-code commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('START_MOTION', self.cmd_START_MOTION,
                               desc="Start continuous X-Y-Z axis drip motion between bounds")
        gcode.register_command('STOP_MOTION', self.cmd_STOP_MOTION,
                               desc="Stop continuous X-Y-Z axis drip motion")
        gcode.register_command('CHANGE_MOTION', self.cmd_CHANGE_MOTION,
                               desc="Change motion parameters during loop")

        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')

    def cmd_START_MOTION(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet.")
            return
        if self.is_running:
            gcmd.respond_info("Loop motion already running.")
            return

        # Apply presets for each axis
        for axis_name, axis in self.axes.items():
            val = gcmd.get_int(axis_name, None)
            if not axis.apply_preset(val):
                gcmd.respond_info(f"Invalid {axis_name} value. Use {axis_name}=0..5.")
                return

        self.origin_pos = self.toolhead.get_position()
        self.current_pos = list(self.origin_pos)

        # Determine initial direction for each axis
        for axis in self.axes.values():
            axis.determine_initial_direction(self.current_pos[axis.index])

        self.is_running = True
        info_strings = [axis.get_info_string() for axis in self.axes.values()]
        gcmd.respond_info(f"Starting drip-feed motion: {', '.join(info_strings)}")
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

        # Apply presets for each axis if provided
        for axis_name, axis in self.axes.items():
            val = gcmd.get_int(axis_name, None)
            if val is not None and not axis.apply_preset(val):
                gcmd.respond_info(f"Invalid {axis_name} value. Use {axis_name}=0..5.")
                return

        # Continue from current actual position
        self.current_pos = list(self.toolhead.get_position())
        info_strings = [axis.get_info_string() for axis in self.axes.values()]
        gcmd.respond_info(f"Motion changed: {', '.join(info_strings)} (continuing from current position)")

    def _schedule_next_move(self, eventtime=None):
        """Schedule next drip move. Accepts eventtime for reactor callback compatibility."""
        if not self.is_running or self.toolhead is None:
            return None

        # Compute next position for each axis
        new_pos = list(self.current_pos)
        for axis in self.axes.values():
            new_pos[axis.index] = axis.compute_next_position(self.current_pos[axis.index])

        # Use the maximum speed across all axes to ensure coordinated motion
        # Filter out stationary axes (speed=0) when calculating move speed
        active_speeds = [axis.speed for axis in self.axes.values() if axis.speed > 0]
        move_speed = max(active_speeds) if active_speeds else 1.0  # Default to 1.0 if all stationary

        drip_completion = self.reactor.completion()

        try:
            self.toolhead.drip_move(new_pos, move_speed, drip_completion)
        except Exception:
            logging.exception("Error during drip motion")
            self.is_running = False
            return None

        # Update current position
        self.current_pos = new_pos

        # Debug logging
        debug_parts = [f"{axis.name}={new_pos[axis.index]:.3f} (dir={axis.direction})" 
                      for axis in self.axes.values()]
        logging.debug("Drip move to %s", ", ".join(debug_parts))

        # Chain next move: register method directly (accepts eventtime)
        self.reactor.register_callback(self._schedule_next_move, self.reactor.NOW)
        return None

def load_config(config):
    return LoopMoveX(config)
