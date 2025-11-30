
import logging

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
        self.max_x = 236.0         # Upper bound

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

    def cmd_START_MOTION(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet.")
            return
        if self.is_running:
            gcmd.respond_info("Loop motion already running.")
            return

        # Parse X parameter
        x_val = gcmd.get_int('X', None)
        settings = {
            1: (0.45, 10.0),
            2: (0.80, 20.0),
            3: (1.05, 30.0),
            4: (1.20, 40.0),
            5: (1.25, 50.0)
        }
        if x_val not in settings:
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return

        self.step_distance, self.speed = settings[x_val]

        self.origin_pos = self.toolhead.get_position()
        self.current_pos = list(self.origin_pos)

        # Determine closest bound
        dist_to_min = abs(self.current_pos[0] - self.min_x)
        dist_to_max = abs(self.current_pos[0] - self.max_x)
        if dist_to_min < dist_to_max:
            self.direction = -1  # Move toward min_x first
        else:
            self.direction = 1   # Move toward max_x first

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
        settings = {
            1: (0.45, 10.0),
            2: (0.80, 20.0),
            3: (1.05, 30.0),
            4: (1.20, 40.0),
            5: (1.25, 50.0)
        }
        if x_val not in settings:
            gcmd.respond_info("Invalid X value. Use X=1..5.")
            return

        # Apply new settings and continue from current position
        self.step_distance, self.speed = settings[x_val]
        self.current_pos = self.toolhead.get_position()

        gcmd.respond_info(f"Motion changed: step={self.step_distance}mm, speed={self.speed}mm/s (continuing from current position)")

    def _schedule_next_move(self):
        if not self.is_running or self.toolhead is None:
            return

        # Compute next X position within bounds
        next_x = self.current_pos[0] + (self.step_distance * self.direction)

        # Clamp to bounds and reverse direction if needed
        if next_x > self.max_x:
            next_x = self.max_x
            self.direction = -1
        elif next_x < self.min_x:
            next_x = self.min_x
            self.direction = 1

        new_pos = [next_x, self.origin_pos[1], self.origin_pos[2], self.origin_pos[3]]
        drip_completion = self.reactor.completion()

        try:
            self.toolhead.drip_move(new_pos, self.speed, drip_completion)
        except Exception as e:
            logging.exception("Error during drip motion")
            self.is_running = False
            return

        # Update current position
        self.current_pos = new_pos

        # Chain next move immediately after completion
        self.reactor.register_callback(lambda e: self._schedule_next_move(), self.reactor.NOW)

def load_config(config):
    return LoopMoveX(config)
