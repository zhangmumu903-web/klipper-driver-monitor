# LYX9231 Modbus RTU stepper driver module
# License: GNU GPLv3

from . import lyx, lyx_uart

######################################################################
# Modbus register address mapping table
######################################################################
Registers = {
    "SAVE_PARAM": 0x00,
    "BAUDRATE": 0x01,
    "COMM_ADDR": 0x02,
    "CHIP_MODEL": 0x03,
    "PHASE_B_RESIST": 0x04,
    "PHASE_A_RESIST": 0x05,
    "PHASE_B_INDUCT": 0x06,
    "PHASE_A_INDUCT": 0x07,
    "ALARM_CODE": 0x08,
    "CURRENT_KP": 0x09,
    "CURRENT_KI": 0x0A,
    "MOTOR_POS_H": 0x0C,
    "MOTOR_POS_L": 0x0D,
    "MOTOR_SPEED": 0x0E,
    "ERROR_ANGLE": 0x10,
    "MS_PIN_FUNC": 0x11,
    "MOTOR_TYPE": 0x12,
    "RUN_CURRENT": 0x13,
    "HALF_CUR_TIME": 0x14,
    "HALF_CUR_RATIO": 0x15,
    "HALF_CUR_EN": 0x16,
    "DIR_POLARITY": 0x17,
    "ENA_POLARITY": 0x18,
    "MICROSTEP_RATIO": 0x19,
    "DEAD_TIME": 0x1A,
    "OCL_THRESHOLD": 0x1B,
    "OCL_FILTER": 0x1C,
    "CUR_ANTISAT": 0x1D,
    "CUR_KP_GAIN": 0x1E,
    "CUR_KI_GAIN": 0x1F,
    "BOOST_LEVEL": 0x20,
    "OP_MODE": 0x21,
    "STALL_ANGLE": 0x22,
    "STALL_OUT_EN": 0x23,
    "MIN_SPEED": 0x26,
    "NOISE_EN": 0x41,
}
# V6.0 manual pp.13-15; online writes cannot change bus identity/mode.
WriteRanges = {
    "MOTOR_TYPE": (1, 2), "RUN_CURRENT": (50, 1900),
    "HALF_CUR_TIME": (3000, 30000), "HALF_CUR_RATIO": (0, 128),
    "HALF_CUR_EN": (0, 1), "DIR_POLARITY": (0, 1),
    "ENA_POLARITY": (0, 1), "MICROSTEP_RATIO": (128, 25600),
    "DEAD_TIME": (0, 127), "OCL_THRESHOLD": (0, 1000),
    "OCL_FILTER": (0, 31), "CUR_ANTISAT": (0, 2048),
    "CUR_KP_GAIN": (16, 1024), "CUR_KI_GAIN": (16, 1024),
    "BOOST_LEVEL": (0, 8), "OP_MODE": (0, 2),
    "STALL_ANGLE": (128, 32768), "STALL_OUT_EN": (0, 1),
    "MIN_SPEED": (0, 60000), "NOISE_EN": (0, 1),
}

# List of registers requiring runtime readback
ReadRegisters = [
    "CHIP_MODEL", "ALARM_CODE", "MOTOR_SPEED", "ERROR_ANGLE"
]

######################################################################
# Register field mask definitions (all 16-bit full-width registers)
######################################################################
Fields = {}
for reg_name in Registers:
    Fields[reg_name] = {reg_name.lower(): 0xFFFF}

# Registers with signed 16-bit value interpretation
SignedFields = ["error_angle", "motor_speed"]

# Human-readable value formatters for register dumps
FieldFormatters = {
    "alarm_code": lambda v: {
        0: "OK", 1: "OverCurrent", 2: "MotorDisconnected",
        3: "CoilAbnormal", 4: "FollowError", 5: "Stall"
    }.get(v, str(v)),
    "op_mode": lambda v: {
        0: "OpenLoop", 1: "NormalClosed", 2: "SuperClosed",
        3: "ServoClosed", 4: "TorqueMode"
    }.get(v, str(v)),
    "motor_type": lambda v: "1.8deg" if v == 1 else "0.9deg",
}


######################################################################
# Main LYX9231 driver entry class
######################################################################
class LYX9231:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]

        self.fields = lyx.FieldHelper(Fields, SignedFields, FieldFormatters)
        self.mcu_lyx = lyx_uart.MCU_LYX_uart(config, Registers, self.fields)
        stepper = config.getsection(config.get_name().split(None, 1)[1])
        full_steps = stepper.getint('full_steps_per_rotation', 200, minval=1)
        if full_steps not in (200, 400):
            raise config.error("LYX9231 requires 200 or 400 full steps per rotation")
        self.stepper_motor_type = full_steps // 200
        self._set_defaults(config)

        self.current_helper = lyx.LYXCurrentHelper(config, self.mcu_lyx)
        self.micro_helper = lyx.LYXMicrostepHelper(config, self.mcu_lyx)
        self.mcu_lyx.write_validator = self.validate_register
        try:
            for name, value in self.fields.registers.items():
                self.validate_register(name, value)
        except ValueError as e:
            raise config.error(str(e))
        self.cmd_helper = lyx.LYXCommandHelper(config, self.mcu_lyx,
                                               self.current_helper,
                                               self.micro_helper)
        self.cmd_helper.setup_register_dump(ReadRegisters)

        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)

        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_mux_command('LYX_READ_REG', "STEPPER", self.name,
                                        self.cmd_LYX_READ_REG)
        self.gcode.register_mux_command('LYX_WRITE_REG', "STEPPER", self.name,
                                        self.cmd_LYX_WRITE_REG)

    def _set_defaults(self, config):
        """Populate default register values from printer config"""
        for option in ('driver_run_current', 'driver_half_cur_ratio'):
            if config.get(option, None) is not None:
                raise config.error("Use run_current and hold_current instead "
                                   "of raw current configuration options")
        s = self.fields.set_config_field
        s(config, "motor_type", self.stepper_motor_type)
        s(config, "op_mode", 2)
        # Preserve initialization order; the current helper supplies values.
        self.fields.set_field("run_current", 896)
        s(config, "half_cur_en", 0)
        s(config, "half_cur_time", 3000)
        self.fields.set_field("half_cur_ratio", 64)
        s(config, "boost_level", 1)
        s(config, "noise_en", 0)

    def validate_register(self, name, value):
        if name not in WriteRanges:
            raise ValueError("Register %s is read-only or not supported for "
                             "online writes (bus settings and SAVE_PARAM "
                             "require a separate setup procedure)" % name)
        low, high = WriteRanges[name]
        if not low <= value <= high:
            raise ValueError("Register %s must be in %d..%d" % (name, low, high))
        if name == "MOTOR_TYPE" and value != self.stepper_motor_type:
            raise ValueError("MOTOR_TYPE must match stepper full_steps_per_rotation")
        self.micro_helper.validate_register(name, value)

    def _handle_connect(self):
        """Initialize driver registers on Klippy connect event"""
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        for reg_name, val in self.fields.registers.items():
            try:
                self.mcu_lyx.set_register(reg_name, val, print_time)
            except self.printer.command_error as e:
                raise self.printer.config_error(
                    "LYX '%s' initialization failed at %s: %s" % (
                        self.name, reg_name, e))

    def cmd_LYX_READ_REG(self, gcmd):
        """G-code: Read single Modbus register and print value"""
        reg_name = gcmd.get('REGISTER').upper()
        if reg_name not in Registers:
            raise gcmd.error("Unknown register: {}".format(reg_name))
        if reg_name == "SAVE_PARAM":
            raise gcmd.error("SAVE_PARAM is write-only")
        val = self.mcu_lyx.get_register(reg_name)
        gcmd.respond_info("{} (0x{:02X}) = {}"
                          .format(reg_name, Registers[reg_name], val))

    def cmd_LYX_WRITE_REG(self, gcmd):
        """G-code: Write raw value to
        target Modbus register with readback verify"""
        reg_name = gcmd.get('REGISTER').upper()
        value = gcmd.get_int('VALUE', minval=0, maxval=65535)
        if reg_name not in Registers:
            raise gcmd.error("Unknown register: {}".format(reg_name))
        self.mcu_lyx.set_register(reg_name, value)
        gcmd.respond_info("{} write {} -> OK".format(reg_name, value))

    def get_status(self, eventtime=None):
        """Provide driver state for printer status reporting"""
        return self.cmd_helper.get_status(eventtime)


def load_config_prefix(config):
    """Klippy config loader entry for [lyx923] sections"""
    return LYX9231(config)
