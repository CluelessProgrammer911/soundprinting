
class ContinuousMove:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        # Try to access reactor
        self.reactor = self.printer.get_reactor()

        # Register a simple command to test toolhead and reactor access
        self.gcode.register_command('TEST_TOOLHEAD', self.cmd_test_toolhead)

    def cmd_test_toolhead(self, params):
        try:
            toolhead = self.printer.lookup_object('toolhead')
            pos = toolhead.get_position()
            reactor_info = "Reactor object found!" if self.reactor else "Reactor not found!"
            self.gcode.respond_info(f"Toolhead OK. Position: {pos}. {reactor_info}")
        except Exception as e:
            self.gcode.respond_info(f"Error: {e}")

def load_config(config):
    return ContinuousMove(config)

