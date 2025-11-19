# first_test.py — Step 2: Claim/release toolhead (no movement)

class FirstTest:
    def __init__(self, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object('gcode')
        self.toolhead = printer.lookup_object('toolhead')

        self.timer = None
        self.timer_running = False
        self.toolhead_claimed = False

        # Commands
        self.gcode.register_command('FT_START_TIMER', self.cmd_start_timer)
        self.gcode.register_command('FT_STOP_TIMER', self.cmd_stop_timer)
        self.gcode.register_command('FT_CLAIM', self.cmd_claim_toolhead)
        self.gcode.register_command('FT_RELEASE', self.cmd_release_toolhead)

    # ---- Timer callback ----
    def _timer_callback(self, eventtime):
        self.gcode.respond_info("⏱ Timer tick!")
        return eventtime + 1.0

    # ---- Timer commands ----
    def cmd_start_timer(self, gcmd):
        if self.timer_running:
            gcmd.respond_info("Timer already running")
            return
        self.timer_running = True
        start_time = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(self._timer_callback, start_time)
        gcmd.respond_info("Timer started")

    def cmd_stop_timer(self, gcmd):
        if not self.timer_running:
            gcmd.respond_info("Timer not running")
            return
        self.reactor.unregister_timer(self.timer)
        self.timer_running = False
        gcmd.respond_info("Timer stopped")

    # ---- Toolhead claim ----
    def cmd_claim_toolhead(self, gcmd):
        if self.toolhead_claimed:
            gcmd.respond_info("Toolhead already claimed")
            return

        # This forces the toolhead into "manual move mode"
        # This 0,0,0 movement does NOTHING — it's only to claim
        self.toolhead.manual_move(0.0, 0.0, 0.0, 1.0)

        self.toolhead_claimed = True
        gcmd.respond_info("Toolhead claimed (manual mode enabled)")

    # ---- Toolhead release ----
    def cmd_release_toolhead(self, gcmd):
        if not self.toolhead_claimed:
            gcmd.respond_info("Toolhead not claimed")
            return

        # Restore normal behavior
        self.toolhead.cmd_M400()  # wait for moves to clear
        self.toolhead_claimed = False
        gcmd.respond_info("Toolhead released")

def load_config(config):
    return FirstTest(config.get_printer())
