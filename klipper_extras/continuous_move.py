class ContinuousMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.toolhead = self.printer.lookup_object('toolhead')

        # Register commands
        self.gcode.register_command('TEST_TOOLHEAD', self.cmd_test_toolhead)
        self.gcode.register_command('TEST_TIMER_MOVE', self.cmd_test_timer_move)

        # State
        self.timer = None
        self.counter = 0
        self.current_pos = list(self.toolhead.get_position())  # [X, Y, Z, E]

    def cmd_test_toolhead(self, params):
        try:
            pos = self.toolhead.get_position()
            self.gcode.respond_info(f"Toolhead OK. Position: {pos}")
        except Exception as e:
            self.gcode.respond_info(f"Error: {e}")

    def cmd_test_timer_move(self, params):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer stopped.")
        else:
            self.counter = 0
            self.timer = self.reactor.register_timer(self._timer_callback, self.reactor.monotonic())
            self.gcode.respond_info("Timer started. Moving X axis by 0.1mm every second for 5 ticks.")

    def _timer_callback(self, eventtime):
        self.counter += 1

        # Increment X by 0.1mm
        self.current_pos[0] += 0.1
        try:
            self.toolhead.move(self.current_pos, 10.0)  # Speed = 10 mm/s
            self.gcode.respond_info(f"Tick {self.counter}: Moved to {self.current_pos}")
        except Exception as e:
            self.gcode.respond_info(f"Move error: {e}")
            return self.reactor.NEVER

        if self.counter >= 5:  # Stop after 5 moves
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer finished.")
            return self.reactor.NEVER

        return eventtime + 1.0  # Schedule next tick in 1 second

def load_config(config):
    return ContinuousMove(config)
