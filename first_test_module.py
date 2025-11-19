# first_test.py — Step 2 (fixed): deferred toolhead lookup

class FirstTest:
    def __init__(self, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object('gcode')

        self.toolhead = None  # will be assigned later when ready
        self.toolhead_claimed = False

        self.timer = None
        self.timer_running = False

        # Register commands
        self.gcode.register_command('FT_START_TIMER', self.cmd_start_timer)
        self.gcode.register_command('FT_STOP_TIMER', self.cmd_stop_timer)
        self.gcode.register_command('FT_CLAIM', self.cmd_claim_toolhead)
        self.gcode.register_command('FT_RELEASE', self.cmd_release_toolhead)

        # IMPORTANT: wait until Klipper is fully initialized
        printer.register_event_handler("klippy:ready", self._on_ready)

    # Called when Klippy is fully initialized
    def _on_ready(self):
        # Now it's safe to access toolhead
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode.respond_info("FirstTest: toolhead is now available")

    # ---- Timer callback ----
    def _timer_callback(self, eventtime):
        self.gcode.respond_info("⏱ Timer tick!")
        return eventtime + 1.0

    # ---- Start timer ----
    def cmd_start_timer(self, gcmd):
        if self.timer_running:
            gcmd.respond_info("Timer already running")
            return
        self.timer_running = True
        start_time = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self._timer_callback, start_time)
        gcmd.respond_info("Timer started")

    # ---- Stop timer ----
    def cmd_stop_timer(self, gcmd):
        if not self.timer_running:
            gcmd.respond_info("Timer not running")
            return
        self.reactor.unregister_timer(self.timer)
        self.timer_running = False
        gcmd.respond_info("Timer stopped")

    # ---- Claim toolhead ----
    def cmd_claim_toolhead(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet!")
            return

        if self.toolhead_claimed:
            gcmd.respond_info("Toolhead already claimed")
            return

        # Zero-length move claims manual control (safe)
        self.toolhead.manual_move(0.0, 0.0, 0.0, 1.0)
        self.toolhead_claimed = True
        gcmd.respond_info("Toolhead claimed")

    # ---- Release toolhead ----
    def cmd_release_toolhead(self, gcmd):
        if not self.toolhead_claimed:
            gcmd.respond_info("Toolhead not claimed")
            return

        self.toolhead.cmd_M400()  # wait for any queued moves
        self.toolhead_claimed = False
        gcmd.respond_info("Toolhead released")

def load_config(config):
    return FirstTest(config.get_printer())
