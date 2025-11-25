
# File: klippy/extras/continuous_move_test.py

class ContinuousMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # Register commands
        self.gcode.register_command('TEST_TOOLHEAD', self.cmd_test_toolhead)
        self.gcode.register_command('TEST_TIMER', self.cmd_test_timer)

        # State for timer
        self.timer = None
        self.counter = 0

    def cmd_test_toolhead(self, params):
        try:
            toolhead = self.printer.lookup_object('toolhead')
            pos = toolhead.get_position()
            reactor_info = "Reactor object found!" if self.reactor else "Reactor not found!"
            self.gcode.respond_info(f"Toolhead OK. Position: {pos}. {reactor_info}")
        except Exception as e:
            self.gcode.respond_info(f"Error: {e}")

    def cmd_test_timer(self, params):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer stopped.")
        else:
            self.counter = 0
            self.timer = self.reactor.register_timer(self._timer_callback, self.reactor.monotonic())
            self.gcode.respond_info("Timer started. Will print every second.")

    def _timer_callback(self, eventtime):
        self.counter += 1
        self.gcode.respond_info(f"Timer tick: {self.counter}")
        if self.counter >= 5:  # Stop after 5 ticks for safety
            self.reactor.unregister_timer(self.timer)
            self.timer = None
            self.gcode.respond_info("Timer finished.")
            return self.reactor.NEVER
        return eventtime + 1.0  # Schedule next tick in 1 second

def load_config(config):
    return ContinuousMove(config)
