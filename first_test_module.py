#Upload this script to ~/klipper/klippy/extras/first_test.py

class FirstTest:
    def __init__(self, printer):
        self.printer = printer
        gcode = printer.lookup_object('gcode')
        # Register a simple G-code command
        gcode.register_command('FIRST_TEST', self.cmd_first_test)

    def cmd_first_test(self, gcmd):
        # Respond in the console and log
        gcmd.respond_info("FirstTest module loaded and command executed!")
        self.printer.get_reactor().log("FIRST_TEST command was called.")

def load_config(config):
    return FirstTest(config.get_printer())