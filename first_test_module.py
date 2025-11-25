# first_test.py

class FirstTest:
    def __init__(self, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        self.gcode = printer.lookup_object('gcode')

        self.toolhead = None
        self.claimed = False

        self._orig_G0 = None
        self._orig_G1 = None

        # ---- Register commands ----
        self.gcode.register_command('FT_CLAIM', self.cmd_claim_toolhead)
        self.gcode.register_command('FT_RELEASE', self.cmd_release_toolhead)

        # ---- Deferred toolhead lookup ----
        printer.register_event_handler("klippy:ready", self._on_ready)

    # Called when Klipper is fully initialized
    def _on_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode.respond_info("FirstTest: toolhead ready")

    # ---- Blocked movement handler ----
    def _blocked_move(self, gcmd):
        if self.claimed:
            raise gcmd.error("⛔ Movement blocked: Toolhead is claimed")

    # ---- Claim toolhead ----
    def cmd_claim_toolhead(self, gcmd):
        if self.toolhead is None:
            gcmd.respond_info("Toolhead not ready yet")
            return

        if self.claimed:
            gcmd.respond_info("Toolhead already claimed")
            return

        # Correct manual_move call
        self.toolhead.manual_move({'x': 0.0, 'y': 0.0, 'z': 0.0}, 1.0)

        # Backup original G0/G1 handlers
        self._orig_G0 = self.gcode.get_command_handler('G0')
        self._orig_G1 = self.gcode.get_command_handler('G1')

        # Override G0/G1 to block motion while claimed
        self.gcode.register_command('G0', self._blocked_move, when="before")
        self.gcode.register_command('G1', self._blocked_move, when="before")

        self.claimed = True
        gcmd.respond_info("🔒 Toolhead CLAIMED – all motion blocked")

    # ---- Release toolhead ----
    def cmd_release_toolhead(self, gcmd):
        if not self.claimed:
            gcmd.respond_info("Toolhead not claimed")
            return

        # Restore original G0/G1 handlers
        if self._orig_G0:
            self.gcode.register_command('G0', self._orig_G0)
        if self._orig_G1:
            self.gcode.register_command('G1', self._orig_G1)

        self.claimed = False
        gcmd.respond_info("🔓 Toolhead RELEASED – motion allowed again")


def load_config(config):
    return FirstTest(config.get_printer())
