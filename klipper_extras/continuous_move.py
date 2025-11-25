
class ContinuousMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        # Register a simple command to test toolhead access
        self.gcode.register_command('TEST_TOOLHEAD', self.cmd_test_toolhead)

    def cmd_test_toolhead(self, params):
        try:
            toolhead = self.printer.lookup_object('toolhead')
            pos = toolhead.get_position()
            self.gcode.respond_info(f"Toolhead object found! Current position: {pos}")
        except Exception as e:
            self.gcode.respond_info(f"Error accessing toolhead: {e}")

def load_config(config):
    return ContinuousMove(config)
