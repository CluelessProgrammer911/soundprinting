class ContinuousMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.toolhead = None  # Will set later

        # Register commands
        self.gcode.register_command('TEST_TOOLHEAD', self.cmd_test_toolhead)
        self.gcode.register_command('TEST_TIMER_MOVE', self.cmd_test_timer_move)

        # Register event handler for when Klipper is ready
        self.printer.register_event_handler("klippy:ready", self._on_ready)

        # State
        self.timer = None
        self.counter = 0
        self.current_pos = None
        self.speed = 10.0  # Default speed in mm/s
        self.axes = {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'E': 0.0}  # Direction multipliers

    def _on_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.current_pos = list(self.toolhead.get_position())
        self.gcode.respond_info("ContinuousMove initialized: toolhead ready.")

    def cmd_test_toolhead(self, params):
        if not self.toolhead:
            self.gcode.respond_info("Toolhead not ready yet.")
            return
        pos = self.toolhead.get_position()
        self.gcode.respond_info(f"Toolhead OK. Position: {pos}")

    def cmd_test_timer_move(self, params):
        if not self.toolhead:
            self.gcode.respond_info("Toolhead not ready yet.")
            return

        # Parse SPEED
        self.speed = params.get_float('SPEED', self.speed)

        # Parse axis directions (X, Y, Z, E)
        for axis in ['X', 'Y', 'Z', 'E']:
            self.axes[axis] = params.get_float(axis, self.axes[axis])

        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer stopped.")
        else:
            self.counter = 0
            self.timer = self.reactor.register_timer(self._timer_callback, self.reactor.monotonic())
            self.gcode.respond_info(
                f"Timer started. Moving every second for 5 ticks at {self.speed} mm/s. "
                f"Directions: X={self.axes['X']} Y={self.axes['Y']} Z={self.axes['Z']} E={self.axes['E']}."
            )

    def _timer_callback(self, eventtime):
        self.counter += 1

        # Apply increments based on direction multipliers
        step_size = 0.1  # mm per tick
        self.current_pos[0] += self.axes['X'] * step_size
        self.current_pos[1] += self.axes['Y'] * step_size
        self.current_pos[2] += self.axes['Z'] * step_size
        self.current_pos[3] += self.axes['E'] * step_size

        # Move toolhead
        self.toolhead.move(self.current_pos, self.speed)
        self.gcode.respond_info(
            f"Tick {self.counter}: Moved to {self.current_pos} at speed {self.speed} mm/s"
        )

        if self.counter >= 5:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer finished.")
            return self.reactor.NEVER

        return eventtime + 1.0

def load_config(config):
    return ContinuousMove(config)
