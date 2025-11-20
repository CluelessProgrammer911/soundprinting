# First experiment: make stepper_x take one single step after Klippy is ready.

class Instrument:
    def __init__(self, printer):
        self.printer = printer
        self.gcode = printer.lookup_object('gcode')
        self.stepper_x = None
        self.toolhead = None

        # Register command
        self.gcode.register_command('FT_STEP', self.cmd_step)

        # Defer hardware lookup
        printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.stepper_x = self.printer.lookup_object('stepper_x')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode.respond_info("✅ Instrument ready: stepper_x and toolhead available")

    def cmd_step(self, gcmd):
        if not self.stepper_x or not self.toolhead:
            gcmd.respond_info("⚠️ Hardware not ready yet")
            return

        # Flush queued moves
        self.toolhead.flush_stepper_moves()

        # Take one microstep forward
        self.stepper_x.step(1, 1)

        gcmd.respond_info("✅ One step executed on X axis")

def load_config(config):
    return Instrument(config.get_printer())