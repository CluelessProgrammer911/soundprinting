# first_test.py  — Step 1: simple reactor timer

class FirstTest:
    def __init__(self, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object('gcode')

        self.timer = None
        self.timer_running = False

        # Register commands
        self.gcode.register_command('FT_START_TIMER', self.cmd_start_timer)
        self.gcode.register_command('FT_STOP_TIMER', self.cmd_stop_timer)

    # ---- Timer callback ----
    def _timer_callback(self, eventtime):
        self.gcode.respond_info("⏱ Timer tick!")
        # Schedule next tick in 1 second
        return eventtime + 1.0

    # ---- Command: start the timer ----
    def cmd_start_timer(self, gcmd):
        if self.timer_running:
            gcmd.respond_info("Timer already running")
            return

        self.timer_running = True

        # Register timer to start immediately
        start_time = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self._timer_callback, start_time)

        gcmd.respond_info("Timer started")

    # ---- Command: stop the timer ----
    def cmd_stop_timer(self, gcmd):
        if not self.timer_running:
            gcmd.respond_info("Timer not running")
            return

        # Unregister timer
        self.reactor.unregister_timer(self.timer)
        self.timer_running = False

        gcmd.respond_info("Timer stopped")


def load_config(config):
    return FirstTest(config.get_printer())
