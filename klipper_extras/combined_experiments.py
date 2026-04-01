# combined_experiments.py
#
# This Klipper extra combines BLTouch pin movement and fan control using MCU print time scheduling.
# Usage: Add [combined_experiments] to printer.cfg and run COMBINED_EXPERIMENT.

class CombinedExperiments:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None  # Will set later when printer is ready

        # Lookup BLTouch and fan objects
        self.bltouch = self.printer.lookup_object('probe')
        self.fan = self.printer.lookup_object('fan')

        # Register G-code command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'COMBINED_EXPERIMENT',
            self.cmd_combined_experiment,
            desc="Deploy BLTouch pin and turn fan ON, then stow pin and turn fan OFF using MCU scheduling"
        )

        # Register event handler for printer ready
        self.printer.register_event_handler('klippy:ready', self._on_ready)

    def _on_ready(self):
        # Lookup toolhead after printer is ready
        self.toolhead = self.printer.lookup_object('toolhead')

    def cmd_combined_experiment(self, gcmd):
        if not self.toolhead:
            gcmd.respond_info("Toolhead not ready yet")
            return

        gcmd.respond_info("Starting combined experiment with MCU scheduling")

        # Get current print time
        print_time = self.toolhead.get_last_move_time()

        # Schedule BLTouch pin_down at current print_time
        self.bltouch.send_cmd('pin_down', duration=self.bltouch.pin_move_time)

        # Schedule fan ON at print_time + 0.01s
        self.fan.fan.set_speed(1.0, print_time + 0.01)

        # Schedule BLTouch pin_up at print_time + 2.0s
        self.toolhead.dwell(0.0)  # Ensure timeline advances
        self.bltouch.send_cmd('pin_up', duration=self.bltouch.pin_move_time)

        # Schedule fan OFF at print_time + 2.01s
        self.fan.fan.set_speed(0.0, print_time + 2.01)

        gcmd.respond_info("Commands queued: pin_down + fan ON, then pin_up + fan OFF")

# Klipper entry point
def load_config(config):
    return CombinedExperiments(config)
