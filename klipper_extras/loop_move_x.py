import logging

# Centralized motion presets: (step_distance, speed)
MOTION_SETTINGS_XY = {
    0: (0.0, 0.0),
    1: (0.95, 10.0),
    2: (1.80, 20.0),
    3: (3.75, 50.0),
    4: (4.80, 80.0),
    5: (5.00, 100.0)
}

MOTION_SETTINGS_Z = {
    0: (0.0, 0.0),
    1: (0.090, 1),
    2: (0.16, 2),
    3: (0.21, 3),
    4: (0.24, 4),
    5: (0.25, 5)
}

# Fan speed presets: level -> speed (0.0-1.0)
FAN_SPEED_PRESETS = {
    0: 0.0,
    1: 0.2,
    2: 0.4,
    3: 0.6,
    4: 0.8,
    5: 1.0
}

# Axis bounds: (min, max) in mm
AXIS_BOUNDS_X = (-10.0, 234.0)
AXIS_BOUNDS_Y = (-8.0, 234.0)
AXIS_BOUNDS_Z = (2.0, 270.0)

MIN_CYCLE_TIME_MS = 50  # Minimum time for fan cycling to avoid system issues

class AxisConfig:
    """Configuration for a single axis."""
    def __init__(self, index, name, domain, settings_dict):
        self.index = index  # Position index in toolhead coords (0=X, 1=Y, 2=Z)
        self.name = name
        self.domain = domain  # (min, max) tuple
        self.settings_dict = settings_dict
        self.step_distance = 0.0
        self.speed = 0.0
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


class FanController:
    """Helper class to manage fan control operations."""
    
    @staticmethod
    def set_part_cooling_fan(printer, speed):
        """Set part cooling fan speed using async request."""
        fan = printer.lookup_object('fan')
        fan.fan.gcrq.send_async_request(speed)
    
    @staticmethod
    def set_hotend_fan_state(printer, turn_on):
        """Set hotend heater fan on or off by manipulating heater_temp."""
        hotend_fan = printer.lookup_object('heater_fan hotend_fan')
        
        # Cache original heater_temp on first use
        if not hasattr(hotend_fan, '_orig_heater_temp'):
            hotend_fan._orig_heater_temp = float(getattr(hotend_fan, 'heater_temp', 50.0))
        
        default_on_speed = float(getattr(hotend_fan, 'fan_speed', 1.0))
        
        if turn_on:
            # Force ON: make heater_temp very low
            hotend_fan.heater_temp = -999999.0
            hotend_fan.last_speed = default_on_speed
            hotend_fan.fan.set_speed(default_on_speed)
        else:
            # Force OFF: make heater_temp huge
            hotend_fan.heater_temp = 999999.0
            hotend_fan.last_speed = 0.0
            hotend_fan.fan.set_speed(0.0)
    
    @staticmethod
    def get_hotend_fan_state(printer):
        """Get current hotend fan state (True if on, False if off)."""
        hotend_fan = printer.lookup_object('heater_fan hotend_fan')
        current_speed = float(getattr(hotend_fan, 'last_speed', 0.0))
        return current_speed > 0.0

class FanPlayController:
    """Base class for fan play (cycling) functionality."""
    
    def __init__(self, reactor):
        self.reactor = reactor
        self.timer = None
        self.on_time = 0.0
        self.off_time = 0.0
        self.is_on = False
    
    def stop(self):
        """Stop the cycling timer."""
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
    
    def start(self, on_time_ms, off_time_ms):
        """Start cycling with specified on/off times."""
        self.on_time = on_time_ms / 1000.0
        self.off_time = off_time_ms / 1000.0
        self.is_on = False
        
        eventtime = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self._callback, eventtime)
    
    def _callback(self, eventtime):
        """Timer callback - must be overridden by subclass."""
        raise NotImplementedError
    
    def _cycle(self, eventtime, turn_on_func, turn_off_func):
        """Common cycling logic."""
        if self.timer is None:
            return self.reactor.NEVER
        
        try:
            if self.is_on:
                turn_off_func()
                self.is_on = False
                return eventtime + self.off_time
            else:
                turn_on_func()
                self.is_on = True
                return eventtime + self.on_time
        except Exception:
            logging.exception("Error in fan play callback")
            return self.reactor.NEVER

class PartCoolingFanPlay(FanPlayController):
    """Manages part cooling fan cycling."""
    
    def __init__(self, printer):
        super().__init__(printer.get_reactor())
        self.printer = printer
        self.speed = 0.0
    
    def start(self, on_time_ms, off_time_ms, speed):
        """Start cycling with specified speed."""
        self.speed = speed
        super().start(on_time_ms, off_time_ms)
    
    def _callback(self, eventtime):
        return self._cycle(
            eventtime,
            lambda: FanController.set_part_cooling_fan(self.printer, self.speed),
            lambda: FanController.set_part_cooling_fan(self.printer, 0.0)
        )

class HotendFanPlay(FanPlayController):
    """Manages hotend fan cycling."""
    
    def __init__(self, printer):
        super().__init__(printer.get_reactor())
        self.printer = printer
    
    def _callback(self, eventtime):
        return self._cycle(
            eventtime,
            lambda: FanController.set_hotend_fan_state(self.printer, True),
            lambda: FanController.set_hotend_fan_state(self.printer, False)
        )

class LoopMoveX:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
        self.is_running = False
        self.origin_pos = None
        self.current_pos = None
        
        # Fan play controllers
        self.part_fan_play = PartCoolingFanPlay(self.printer)
        self.hotend_fan_play = HotendFanPlay(self.printer)
        
        # Configure axes
        self.axes = {
            'X': AxisConfig(0, 'X', AXIS_BOUNDS_X, MOTION_SETTINGS_XY),
            'Y': AxisConfig(1, 'Y', AXIS_BOUNDS_Y, MOTION_SETTINGS_XY),
            'Z': AxisConfig(2, 'Z', AXIS_BOUNDS_Z, MOTION_SETTINGS_Z)
        }

        # Register G-code commands
        self._register_commands()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _register_commands(self):
        """Register all G-code commands."""
        gcode = self.printer.lookup_object('gcode')
        commands = {
            'START_MOTION': (self.cmd_START_MOTION, "Start continuous X-Y-Z axis drip motion between bounds"),
            'STOP_MOTION': (self.cmd_STOP_MOTION, "Stop continuous X-Y-Z axis drip motion"),
            'CHANGE_MOTION': (self.cmd_CHANGE_MOTION, "Change motion parameters during loop"),
            'TUNE_FAN': (self.cmd_TUNE_FAN, "Set fan speed using presets (S=0..5)"),
            'TOGGLE_HOTEND_FAN': (self.cmd_TOGGLE_HOTEND_FAN, "Toggle hotend heater fan on/off"),
            'PLAY_FAN': (self.cmd_PLAY_FAN, "Cycle fan on/off: S=speed(0-5) U=on_ms D=off_ms"),
            'PLAY_HOTEND_FAN': (self.cmd_PLAY_HOTEND_FAN, "Cycle hotend fan on/off: U=on_ms D=off_ms")
        }
        for cmd_name, (cmd_func, desc) in commands.items():
            gcode.register_command(cmd_name, cmd_func, desc=desc)

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

    def cmd_TUNE_FAN(self, gcmd):
        """Set part cooling fan to a specific speed level."""
        level = gcmd.get_int('S', None)
        
        if level not in FAN_SPEED_PRESETS:
            gcmd.respond_info("Invalid S value. Use S=0..5.")
            return
        
        speed = FAN_SPEED_PRESETS[level]
        logging.info(f"Setting fan speed to level {level} ({speed}) (async)")
        
        self.part_fan_play.stop()
        
        try:
            FanController.set_part_cooling_fan(self.printer, speed)
            gcmd.respond_info(f"Fan speed set to level {level} ({speed})")
        except Exception as e:
            gcmd.respond_info(f"Error setting fan speed: {e}")
            logging.exception("Error in TUNE_FAN command")

    def cmd_TOGGLE_HOTEND_FAN(self, gcmd):
        """Toggle hotend heater fan on/off."""
        self.hotend_fan_play.stop()
        
        try:
            current_state = FanController.get_hotend_fan_state(self.printer)
            new_state = not current_state
            FanController.set_hotend_fan_state(self.printer, new_state)
            
            state_msg = "ON" if new_state else "OFF"
            gcmd.respond_info(f"Hotend fan toggled to {state_msg}")
        except Exception as e:
            logging.exception("Error toggling hotend heater fan")
            gcmd.respond_info(f"Error toggling hotend fan: {e}")

    def cmd_PLAY_FAN(self, gcmd):
        """Cycle part cooling fan on/off at specified speed and timing."""
        speed_level = gcmd.get_int('S', None)
        on_time_ms = gcmd.get_int('U', None)
        off_time_ms = gcmd.get_int('D', None)

        # Validate parameters
        if speed_level not in FAN_SPEED_PRESETS:
            gcmd.respond_info("Invalid S value. Use S=0..5.")
            return
        if on_time_ms is None or on_time_ms < 0:
            gcmd.respond_info("Invalid U value. Must be non-negative milliseconds.")
            return
        if off_time_ms is None or off_time_ms < 0:
            gcmd.respond_info("Invalid D value. Must be non-negative milliseconds.")
            return

        self.part_fan_play.stop()

        # Handle edge cases for minimum cycle times
        if on_time_ms < MIN_CYCLE_TIME_MS:
            FanController.set_part_cooling_fan(self.printer, 0.0)
            gcmd.respond_info("U below 50ms: fan set to OFF (no cycling)")
            return

        if off_time_ms < MIN_CYCLE_TIME_MS:
            speed = FAN_SPEED_PRESETS[speed_level]
            FanController.set_part_cooling_fan(self.printer, speed)
            gcmd.respond_info(f"D below 50ms: fan set to level {speed_level} ({speed}) (no cycling)")
            return

        if speed_level == 0:
            FanController.set_part_cooling_fan(self.printer, 0.0)
            gcmd.respond_info("Fan play S=0: fan turned OFF (no cycling)")
            return

        # Start cycling
        speed = FAN_SPEED_PRESETS[speed_level]
        self.part_fan_play.start(on_time_ms, off_time_ms, speed)
        gcmd.respond_info(f"Fan play started: S={speed_level} ({speed}) U={on_time_ms}ms D={off_time_ms}ms")

    def cmd_PLAY_HOTEND_FAN(self, gcmd):
        """Cycle hotend fan on/off at specified timing."""
        on_time_ms = gcmd.get_int('U', None)
        off_time_ms = gcmd.get_int('D', None)

        # Validate parameters
        if on_time_ms is None or on_time_ms < 0:
            gcmd.respond_info("Invalid U value. Must be non-negative milliseconds.")
            return
        if off_time_ms is None or off_time_ms < 0:
            gcmd.respond_info("Invalid D value. Must be non-negative milliseconds.")
            return

        self.hotend_fan_play.stop()

        # Handle edge cases for minimum cycle times
        if on_time_ms < MIN_CYCLE_TIME_MS:
            FanController.set_hotend_fan_state(self.printer, False)
            gcmd.respond_info("U below 50ms: hotend fan set to OFF (no cycling)")
            return

        if off_time_ms < MIN_CYCLE_TIME_MS:
            FanController.set_hotend_fan_state(self.printer, True)
            gcmd.respond_info("D below 50ms: hotend fan set to ON (no cycling)")
            return

        # Start cycling
        self.hotend_fan_play.start(on_time_ms, off_time_ms)
        gcmd.respond_info(f"Hotend fan play started: U={on_time_ms}ms D={off_time_ms}ms")

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