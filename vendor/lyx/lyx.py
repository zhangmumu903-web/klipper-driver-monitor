# Common helper utilities for LYX stepper driver
# License: GNU GPLv3

import collections


######################################################################
# Bit mask position helper
######################################################################
def ffs(mask):
    """Return zero-based offset of least significant set bit"""
    return (mask & -mask).bit_length() - 1


######################################################################
# Register field access abstraction
######################################################################
class FieldHelper:
    """Manage register field read/write,
    signed value conversion and formatting"""

    def __init__(self, all_fields, signed_fields=[], field_formatters={}):
        self.all_fields = all_fields
        self.signed_fields = set(signed_fields)
        self.field_formatters = field_formatters
        self.registers = collections.OrderedDict()
        # Map field name to parent register address
        self.field_to_register = {
            f: r for r, fields in self.all_fields.items() for f in fields
        }

    def get_field(self, field_name, reg_value=None):
        """Read decoded value of specified field"""
        reg_name = self.field_to_register[field_name]
        reg_val = (self.registers.get(reg_name, 0)
                   if reg_value is None else reg_value)
        mask = self.all_fields[reg_name][field_name]
        val = (reg_val & mask) >> ffs(mask)
        # Convert 16-bit two's complement for signed fields
        if field_name in self.signed_fields and val & (1 << 15):
            val -= (1 << 16)
        return val

    def set_field(self, field_name, value):
        """Write raw value to target register field"""
        reg_name = self.field_to_register[field_name]
        reg_val = self.registers.get(reg_name, 0)
        mask = self.all_fields[reg_name][field_name]
        bits = (mask >> ffs(mask)).bit_length()
        low, high = 0, mask >> ffs(mask)
        if field_name in self.signed_fields:
            low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        if not isinstance(value, int) or not low <= value <= high:
            raise ValueError("Field %s must be in %d..%d" % (
                field_name, low, high))
        value &= 0xFFFF
        new_val = (reg_val & ~mask) | ((value << ffs(mask)) & mask)
        self.registers[reg_name] = new_val & 0xFFFF
        return new_val & 0xFFFF

    def set_config_field(self, config, field_name, default):
        """Load config value and apply to register field with type conversion"""
        config_name = "driver_" + field_name
        reg_name = self.field_to_register[field_name]
        mask = self.all_fields[reg_name][field_name]
        maxval = mask >> ffs(mask)
        if maxval == 1:
            val = config.getboolean(config_name, default)
        elif field_name in self.signed_fields:
            val = config.getint(config_name, default, minval=-32768,
                                maxval=32767)
        else:
            val = config.getint(config_name, default, minval=0, maxval=maxval)
        return self.set_field(field_name, val)

    def pretty_format(self, reg_name, reg_value):
        """Format register value with
        human-readable field labels for dump output"""
        fields = []
        for field_name, mask in self.all_fields.get(reg_name, {}).items():
            val = self.get_field(field_name, reg_value)
            sval = self.field_formatters.get(field_name, str)(val)
            if sval and sval != "0":
                fields.append(" {}={}".format(field_name, sval))
        return "%-12s %04x%s" % (reg_name + ":", reg_value, "".join(fields))


######################################################################
# Motor current calculation helper
######################################################################
class LYXCurrentHelper:
    """Convert ampere current setting to register raw values and vice versa"""

    def __init__(self, config, mcu_lyx):
        self.printer = config.get_printer()
        self.fields = mcu_lyx.get_fields()

        self.sense_resistor = config.getfloat('sense_resistor',
                                              0.050, above=0.)
        run_current = config.getfloat('run_current', 1.4, above=0.)
        hold_current = config.getfloat('hold_current', None, minval=0.)
        if hold_current is None:
            hold_current = run_current * 0.5
        # Scale factor to convert register value to physical current (Amps)
        self._current_scale = (0.025 / self.sense_resistor) * 6.4 / 2048.0
        self.max_current = 1900 * self._current_scale

        self.set_current(run_current, hold_current, None)

    def get_current(self):
        """Return decoded run/hold current values in Amps"""
        run_reg = self.fields.get_field("run_current")
        run_current = run_reg * self._current_scale
        half_ratio = self.fields.get_field("half_cur_ratio") / 128.0
        hold_current = run_current * half_ratio
        return run_current, hold_current, hold_current, self.max_current

    def set_current(self, run_current, hold_current, print_time):
        """Calculate and write raw register values from ampere inputs"""
        if run_current <= 0.:
            raise ValueError("Run current must be greater than zero")
        run_reg = int(run_current / self._current_scale + 0.5)
        run_reg = max(50, min(1900, run_reg))
        self.fields.set_field("run_current", run_reg)
        half_ratio = int(hold_current / run_current * 128.0 + 0.5)
        half_ratio = max(0, min(128, half_ratio))
        self.fields.set_field("half_cur_ratio", half_ratio)


######################################################################
# Microstep subdivision conversion helper
######################################################################
class LYXMicrostepHelper:
    def __init__(self, config, mcu_lyx):
        self.fields = mcu_lyx.get_fields()
        self.base_div = 25600
        stepper = config.getsection(config.get_name().split(None, 1)[1])
        self.configured_microstep = stepper.getint('microsteps', minval=1)
        target = config.getint('microstep', self.configured_microstep, minval=1)
        try:
            if target != self.configured_microstep:
                raise ValueError("LYX microstep must match stepper microsteps")
            self.set_microstep(target, None)
        except ValueError as e:
            raise config.error(str(e))

    def get_microstep(self):
        raw = self.fields.get_field("microstep_ratio")
        if not 128 <= raw <= self.base_div or self.base_div % raw:
            return None
        return self.base_div // raw

    def raw_for_microstep(self, microstep):
        if (microstep < 1 or self.base_div % microstep
                or not 128 <= self.base_div // microstep <= self.base_div):
            raise ValueError(
                "LYX serial microsteps must divide 25600 exactly and yield "
                "register 0x19 in 128..25600 (256 is unsupported)")
        if microstep != self.configured_microstep:
            raise ValueError("Change stepper microsteps in config and restart "
                             "Klipper before changing LYX subdivision")
        return self.base_div // microstep

    def validate_register(self, name, value):
        if (name == "MICROSTEP_RATIO"
                and value != self.raw_for_microstep(self.configured_microstep)):
            raise ValueError("MICROSTEP_RATIO must match stepper microsteps")

    def set_microstep(self, micro_step, print_time):
        self.fields.set_field("microstep_ratio",
                              self.raw_for_microstep(micro_step))


######################################################################
# G-code command management helper
######################################################################
class LYXCommandHelper:
    """Register and handle LYX custom G-code commands"""

    def __init__(self, config, mcu_lyx, current_helper, micro_helper):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.mcu_lyx = mcu_lyx
        self.current_helper = current_helper
        self.micro_helper = micro_helper
        self.fields = mcu_lyx.get_fields()
        self.read_registers = []
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_LYX_FIELD", "STEPPER", self.name,
                                   self.cmd_SET_LYX_FIELD)
        gcode.register_mux_command("SET_LYX_CURRENT", "STEPPER", self.name,
                                   self.cmd_SET_LYX_CURRENT)
        gcode.register_mux_command("SET_LYX_MICROSTEP", "STEPPER", self.name,
                                   self.cmd_SET_LYX_MICROSTEP)

    def cmd_SET_LYX_FIELD(self, gcmd):
        """G-code: Modify single register field raw value"""
        field_name = gcmd.get('FIELD').lower()
        reg_name = self.fields.field_to_register.get(field_name)
        if reg_name is None:
            raise gcmd.error("Unknown field: {}".format(field_name))
        value = gcmd.get_int('VALUE')
        before = self.fields.registers.copy()
        try:
            raw = self.fields.set_field(field_name, value)
        except ValueError as e:
            raise gcmd.error(str(e))
        finally:
            self.fields.registers.clear()
            self.fields.registers.update(before)
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        self.mcu_lyx.set_register(reg_name, raw, print_time)

    def cmd_SET_LYX_CURRENT(self, gcmd):
        """G-code: Adjust motor run/hold current in Amps"""
        ch = self.current_helper
        prev_run, prev_hold, _, max_cur = ch.get_current()
        run = gcmd.get_float('CURRENT', None, above=0., maxval=max_cur)
        hold = gcmd.get_float('HOLDCURRENT', None, minval=0., maxval=max_cur)
        if run is None and hold is None:
            gcmd.respond_info("Run: {:.2f}A  Hold: {:.2f}A"
                              .format(prev_run, prev_hold))
            return
        run = run if run is not None else prev_run
        hold = hold if hold is not None else prev_hold
        before = self.fields.registers.copy()
        try:
            ch.set_current(run, hold, None)
            targets = [(name, self.fields.registers[name])
                       for name in ("RUN_CURRENT", "HALF_CUR_RATIO")]
        except ValueError as e:
            raise gcmd.error(str(e))
        finally:
            self.fields.registers.clear()
            self.fields.registers.update(before)
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        for reg_name, value in targets:
            self.mcu_lyx.set_register(reg_name, value, print_time)
        run, hold, _, _ = ch.get_current()
        gcmd.respond_info("Run: {:.2f}A  Hold: {:.2f}A"
                          .format(run, hold))

    def cmd_SET_LYX_MICROSTEP(self, gcmd):
        mh = self.micro_helper
        prev_micro = mh.get_microstep()
        microstep = gcmd.get_int('MICROSTEP', None, minval=1, maxval=256)
        if microstep is None:
            gcmd.respond_info("Current microstep: {}".format(prev_micro))
            return
        try:
            raw = mh.raw_for_microstep(microstep)
        except ValueError as e:
            raise gcmd.error(str(e))
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        self.mcu_lyx.set_register("MICROSTEP_RATIO", raw, print_time)
        gcmd.respond_info("Set microstep = {}, register raw={}"
                          .format(microstep, raw))

    def setup_register_dump(self, read_registers):
        """Register DUMP_LYX diagnostic command"""
        self.read_registers = read_registers
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("DUMP_LYX", "STEPPER", self.name,
                                   self.cmd_DUMP_LYX)

    def cmd_DUMP_LYX(self, gcmd):
        """G-code: Print all cached and live register values"""
        gcmd.respond_info("=== Write registers ===")
        for reg_name, val in self.fields.registers.items():
            if reg_name not in self.read_registers:
                gcmd.respond_info(self.fields.pretty_format(reg_name, val))
        gcmd.respond_info("=== Live registers ===")
        for reg_name in self.read_registers:
            val = self.mcu_lyx.get_register(reg_name)
            gcmd.respond_info(self.fields.pretty_format(reg_name, val))

    def get_status(self, eventtime=None):
        """Expose current values for printer status query"""
        cur = self.current_helper.get_current()
        microstep = self.micro_helper.get_microstep()
        return {
            'run_current': cur[0],
            'hold_current': cur[1],
            'microstep': microstep
        }
