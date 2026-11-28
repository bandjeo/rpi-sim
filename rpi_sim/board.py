"""Virtual GPIO board and components, independent of the desktop interface."""

import errno
import json
import math
import time
from dataclasses import asdict, dataclass
from itertools import count
from pathlib import Path

from rpi_sim_client import gpio

from .i2c import I2CBus
from .i2c_devices import LCD1602, MPU6050

COMPONENT_NAMES = {
    "led": "LED", "button": "Push button", "pir": "PIR sensor",
    "buzzer": "Buzzer", "rgb": "RGB LED", "lcd": "LCD1602", "mpu6050": "MPU6050",
}
I2C_DEFAULTS = {"lcd": 0x27, "mpu6050": 0x68}
I2C_ADDRESSES = {"lcd": (*range(0x20, 0x28), *range(0x38, 0x40)), "mpu6050": (0x68, 0x69)}


@dataclass(frozen=True)
class Component:
    id: str
    kind: str
    pin: int | None = None
    name: str = ""
    active_low: bool = False
    green_pin: int | None = None
    blue_pin: int | None = None
    bus: int | None = None
    address: int | None = None

    @property
    def pins(self):
        if self.kind in I2C_ADDRESSES:
            return ()
        if self.kind == "rgb":
            return self.pin, self.green_pin, self.blue_pin
        return (self.pin,)


@dataclass
class PWMState:
    registration: int
    frequency: float
    duty: float | None = None
    started_at: float = 0.0


@dataclass(frozen=True)
class BuzzerTone:
    frequency: float
    duty: float | None = None


@dataclass
class Pin:
    direction: int | None = None
    pull: int = gpio.PUD_OFF
    output: int = gpio.LOW
    pwm: PWMState | None = None


@dataclass
class Edge:
    direction: int
    channel: int
    registration: int
    debounce: float
    last_seen: float = float("-inf")
    pending: bool = False


class Board:
    def __init__(self, pin_changed, edge_detected, i2c_changed=None):
        self.pin_changed = pin_changed
        self.edge_detected = edge_detected
        self.components = {}
        self.input_active = {}
        self.mode = None
        self.pins = {bcm: Pin() for bcm in gpio.BCM_TO_BOARD}
        self.edges = {}
        self.i2c_changed = i2c_changed
        self.i2c_buses = {1: I2CBus()}
        self.pwm_registrations = count(1)
        self.commands = {
            "setmode": self.setmode, "getmode": self.getmode, "setup": self.setup,
            "input": self.input, "output": self.output, "cleanup": self.cleanup,
            "gpio_function": self.gpio_function, "add_event_detect": self.add_event_detect,
            "remove_event_detect": self.remove_event_detect, "event_detected": self.event_detected,
            "pwm_create": self.pwm_create, "pwm_start": self.pwm_start, "pwm_stop": self.pwm_stop,
            "pwm_frequency": self.pwm_frequency, "pwm_duty": self.pwm_duty, "pwm_release": self.pwm_release,
            "i2c_open": self.i2c_open,
            "i2c_transfer": lambda bus, messages: self.i2c_bus(bus).transfer(messages),
            "i2c_contend": lambda bus, *options: self.i2c_bus(bus).contend(*options),
            "i2c_configure": lambda bus, *options: self.i2c_bus(bus).configure(*options),
            "i2c_trace": lambda bus, *options: self.i2c_bus(bus).trace(*options),
            "i2c_recover": lambda bus: self.i2c_bus(bus).recover(),
        }

    def replace_components(self, components):
        components = list(components)
        validate_components(components)
        self.components = {component.id: component for component in components}
        self.input_active = {}
        self.i2c_buses = {1: I2CBus()}
        for component in components:
            self._attach_device(component)

    def put_component(self, component):
        existing = [item for item in self.components.values() if item.id != component.id]
        validate_components([*existing, component])
        previous = self.components.get(component.id)
        self.components[component.id] = component
        if previous is None or (previous.kind, previous.bus, previous.address) != (
            component.kind, component.bus, component.address
        ):
            if previous is not None:
                self._detach_device(previous)
            self._attach_device(component)
        if previous and previous.pins != component.pins:
            self.input_active.pop(component.id, None)
            for bcm in set(previous.pins) - set(component.pins):
                self.pin_changed(bcm)
        for bcm in component.pins:
            self.pin_changed(bcm)

    def remove_component(self, component_id):
        component = self.components.pop(component_id)
        self.input_active.pop(component_id, None)
        self._detach_device(component)
        for bcm in component.pins:
            self.pin_changed(bcm)

    def component_at(self, bcm):
        return next((component for component in self.components.values() if bcm in component.pins), None)

    def _attach_device(self, component):
        if component.kind not in I2C_ADDRESSES:
            return
        if component.kind == "lcd":
            target = LCD1602(lambda: self.i2c_changed(component.id) if self.i2c_changed else None)
        else:
            target = MPU6050()
        if component.bus not in self.i2c_buses:
            self.i2c_buses[component.bus] = I2CBus()
        self.i2c_buses[component.bus].attach(component.address, target)

    def _detach_device(self, component):
        if component.kind in I2C_ADDRESSES:
            del self.i2c_buses[component.bus].targets[(False, component.address)]

    def i2c_bus(self, number):
        if type(number) is not int or not 0 <= number <= 255:
            raise ValueError("I²C bus number must be between 0 and 255")
        if number not in self.i2c_buses:
            raise OSError(errno.ENOENT, f"I²C bus {number} is not configured")
        return self.i2c_buses[number]

    def i2c_open(self, number):
        self.i2c_bus(number)

    def i2c_device(self, component):
        return self.i2c_bus(component.bus).targets[(False, component.address)]

    def set_motion(self, component_id, acceleration, rotation):
        device = self.i2c_device(self.components[component_id])
        device.acceleration, device.rotation = acceleration, rotation

    def set_input(self, component_id, active):
        component = self.components.get(component_id)
        if component is None or component.kind not in ("button", "pir"):
            return
        bcm = component.pin
        previous = self.level(bcm)
        self.input_active[component_id] = active
        self._pin_updated(bcm, previous, detect_edge=self.pins[bcm].direction == gpio.IN)

    def level(self, bcm):
        pin = self.pins[bcm]
        if pin.direction == gpio.OUT:
            pwm = pin.pwm
            if pwm and pwm.duty is not None:
                if pwm.duty == 0.0 or pwm.duty == 1.0:
                    return int(pwm.duty)
                period = 1.0 / pwm.frequency
                return int((time.monotonic() - pwm.started_at) % period < pwm.duty * period)
            return pin.output
        component = self.component_at(bcm)
        if component:
            active = self.input_active.get(component.id, False)
            if component.kind == "pir":
                return gpio.HIGH if active else gpio.LOW
            if component.kind == "button" and active:
                return gpio.LOW
        return gpio.HIGH if pin.pull == gpio.PUD_UP else gpio.LOW

    def component_active(self, component):
        if component.kind in ("led", "rgb"):
            return any(self.intensity(bcm, component.active_low) > 0 for bcm in component.pins)
        if component.kind == "buzzer":
            return self.buzzer_tone(component.pin) is not None
        return self.input_active.get(component.id, False)

    def intensity(self, bcm, active_low=False):
        pin = self.pins[bcm]
        if pin.direction != gpio.OUT:
            return 0.0
        high = pin.pwm.duty if pin.pwm and pin.pwm.duty is not None else float(pin.output)
        return 1.0 - high if active_low else high

    def rgb_color(self, component):
        return tuple(round(255 * self.intensity(bcm, component.active_low)) for bcm in component.pins)

    def buzzer_tone(self, bcm):
        pin = self.pins[bcm]
        if self.intensity(bcm) == 0:
            return None
        if pin.pwm and pin.pwm.duty is not None and pin.pwm.duty < 1:
            return BuzzerTone(pin.pwm.frequency, pin.pwm.duty)
        return BuzzerTone(880.0)

    def setmode(self, mode):
        if mode not in (gpio.BOARD, gpio.BCM):
            raise ValueError("Mode must be GPIO.BOARD or GPIO.BCM")
        if self.mode is not None and mode != self.mode:
            raise ValueError("A different mode has already been set")
        self.mode = mode

    def getmode(self):
        return self.mode

    def _pin(self, channel):
        if self.mode is None:
            raise RuntimeError("Set pin numbering with GPIO.setmode(GPIO.BOARD) or GPIO.setmode(GPIO.BCM)")
        if type(channel) is int:
            if self.mode == gpio.BOARD and channel in gpio.BOARD_TO_BCM:
                return gpio.BOARD_TO_BCM[channel]
            if self.mode == gpio.BCM and channel in self.pins:
                return channel
        raise ValueError(f"{channel!r} is not a GPIO channel in the selected numbering mode")

    def _channels(self, channels):
        return [self._pin(channel) for channel in channels] if isinstance(channels, list) else [self._pin(channels)]

    def setup(self, channels, direction, pull=gpio.PUD_OFF, initial=None):
        if direction not in (gpio.IN, gpio.OUT):
            raise ValueError("Direction must be GPIO.IN or GPIO.OUT")
        if pull not in (gpio.PUD_OFF, gpio.PUD_UP, gpio.PUD_DOWN):
            raise ValueError("Invalid GPIO pull setting")
        if direction == gpio.OUT and pull != gpio.PUD_OFF:
            raise ValueError("Pull resistors apply to GPIO inputs")
        if initial is not None and (direction != gpio.OUT or initial not in (gpio.LOW, gpio.HIGH)):
            raise ValueError("Initial level must be GPIO.LOW or GPIO.HIGH on an output")
        for bcm in self._channels(channels):
            pin = self.pins[bcm]
            if direction == gpio.OUT:
                if initial is not None:
                    pin.output = int(initial)
                self.edges.pop(bcm, None)
            else:
                pin.pwm = None
            pin.direction = direction
            pin.pull = pull
            self.pin_changed(bcm)

    def input(self, channel):
        bcm = self._pin(channel)
        if self.pins[bcm].direction is None:
            raise RuntimeError("Set up the GPIO channel before reading it")
        return self.level(bcm)

    def output(self, channels, values):
        pins = self._channels(channels)
        if not isinstance(values, list):
            values = [values] * len(pins)
        if len(pins) != len(values) or any(value not in (gpio.LOW, gpio.HIGH) for value in values):
            raise ValueError("Supply a GPIO.LOW or GPIO.HIGH value for each channel")
        if any(self.pins[bcm].direction != gpio.OUT for bcm in pins):
            raise RuntimeError("Set up the GPIO channel as an output before writing to it")
        changed = set()
        for bcm, value in zip(pins, values):
            pin = self.pins[bcm]
            if pin.output != value:
                pin.output = int(value)
                changed.add(bcm)
        for bcm in changed:
            self.pin_changed(bcm)

    def gpio_function(self, channel):
        direction = self.pins[self._pin(channel)].direction
        return gpio.IN if direction is None else direction

    def pwm_create(self, channel, frequency):
        pin = self.pins[self._pin(channel)]
        if pin.direction != gpio.OUT:
            raise RuntimeError("Set up the GPIO channel as an output before creating PWM")
        if pin.pwm is not None:
            raise RuntimeError("A PWM object already exists for this channel")
        frequency = _pwm_frequency(frequency)
        registration = next(self.pwm_registrations)
        pin.pwm = PWMState(registration, frequency)
        return registration

    def _pwm_pin(self, channel, registration):
        bcm = self._pin(channel)
        pwm = self.pins[bcm].pwm
        if pwm is None or pwm.registration != registration:
            raise RuntimeError("This PWM object no longer owns the GPIO channel")
        return bcm, pwm

    def pwm_start(self, channel, registration, duty):
        bcm, pwm = self._pwm_pin(channel, registration)
        pwm.duty = _pwm_duty(duty)
        pwm.started_at = time.monotonic()
        self.pin_changed(bcm)

    def pwm_duty(self, channel, registration, duty):
        bcm, pwm = self._pwm_pin(channel, registration)
        duty = _pwm_duty(duty)
        if pwm.duty is not None and duty != pwm.duty:
            pwm.duty = duty
            self.pin_changed(bcm)

    def pwm_frequency(self, channel, registration, frequency):
        bcm, pwm = self._pwm_pin(channel, registration)
        pwm.frequency = _pwm_frequency(frequency)
        pwm.started_at = time.monotonic()
        if pwm.duty is not None:
            self.pin_changed(bcm)

    def pwm_stop(self, channel, registration):
        bcm, pwm = self._pwm_pin(channel, registration)
        if pwm.duty is not None:
            pwm.duty = None
            self.pins[bcm].output = gpio.LOW
            self.pin_changed(bcm)

    def pwm_release(self, channel, registration):
        bcm, pwm = self._pwm_pin(channel, registration)
        self.pins[bcm].pwm = None
        if pwm.duty is not None:
            self.pins[bcm].output = gpio.LOW
            self.pin_changed(bcm)

    def cleanup(self, channels=None):
        if channels is None:
            pins = list(self.pins)
            self.mode = None
        elif self.mode is None:
            return False
        else:
            pins = self._channels(channels)
        cleaned = False
        for bcm in pins:
            self.edges.pop(bcm, None)
            if self.pins[bcm].direction is not None:
                self.pins[bcm] = Pin()
                self.pin_changed(bcm)
                cleaned = True
        return cleaned

    def add_event_detect(self, channel, edge, bouncetime, registration):
        bcm = self._pin(channel)
        if self.pins[bcm].direction != gpio.IN:
            raise RuntimeError("Set up the GPIO channel as an input before watching for edges")
        if edge not in (gpio.RISING, gpio.FALLING, gpio.BOTH):
            raise ValueError("Invalid GPIO edge")
        if bouncetime is not None and (type(bouncetime) is not int or bouncetime <= 0):
            raise ValueError("Debounce time must be a positive integer in milliseconds")
        if bcm in self.edges:
            raise RuntimeError("Edge detection is already enabled for this channel")
        self.edges[bcm] = Edge(edge, channel, registration, (bouncetime or 0) / 1000)

    def remove_event_detect(self, channel):
        self.edges.pop(self._pin(channel), None)

    def event_detected(self, channel):
        edge = self.edges.get(self._pin(channel))
        if not edge:
            return False
        pending, edge.pending = edge.pending, False
        return pending

    def _pin_updated(self, bcm, previous, detect_edge):
        current = self.level(bcm)
        self.pin_changed(bcm)
        event = self.edges.get(bcm)
        if not detect_edge or not event or current == previous:
            return
        edge = gpio.RISING if current else gpio.FALLING
        if event.direction not in (edge, gpio.BOTH):
            return
        now = time.monotonic()
        if now - event.last_seen >= event.debounce:
            event.last_seen = now
            event.pending = True
            self.edge_detected(event.channel, event.registration)


def _finite_number(value):
    if not isinstance(value, (int, float)):
        return math.nan
    try:
        return float(value)
    except OverflowError:
        return math.nan


def _pwm_frequency(value):
    frequency = _finite_number(value)
    if not 0 < frequency < math.inf:
        raise ValueError("PWM frequency must be positive and finite")
    return frequency


def _pwm_duty(value):
    duty = _finite_number(value)
    if not 0 <= duty <= 100:
        raise ValueError("PWM duty cycle must be between 0 and 100")
    return duty / 100


def validate_components(components):
    ids, pins, addresses = set(), set(), set()
    for item in components:
        if not isinstance(item.id, str) or not item.id or item.id in ids:
            raise ValueError("Each component needs a unique ID")
        if not isinstance(item.kind, str) or item.kind not in COMPONENT_NAMES:
            raise ValueError("Unknown component type")
        if item.kind != "rgb" and (item.green_pin is not None or item.blue_pin is not None):
            raise ValueError("Extra color pins apply only to RGB LEDs")
        if item.kind in I2C_ADDRESSES:
            if item.pin is not None or type(item.bus) is not int or not 0 <= item.bus <= 255:
                raise ValueError("Choose a bus number between 0 and 255 for the I²C component")
            if type(item.address) is not int or item.address not in I2C_ADDRESSES[item.kind]:
                raise ValueError(f"Invalid I²C address for {COMPONENT_NAMES[item.kind]}")
            key = (item.bus, item.address)
            if key in addresses:
                raise ValueError(f"I²C bus {item.bus} address 0x{item.address:02X} is already assigned")
            addresses.add(key)
        elif item.bus is not None or item.address is not None:
            raise ValueError("Bus numbers and addresses apply only to I²C components")
        for bcm in item.pins:
            if type(bcm) is not int or bcm not in gpio.BCM_TO_BOARD:
                raise ValueError("Choose a valid BCM GPIO pin")
            if bcm in pins:
                raise ValueError(f"GPIO {bcm} is already assigned")
            pins.add(bcm)
        if not isinstance(item.name, str) or not item.name.strip() or len(item.name) > 60:
            raise ValueError("Component names must have between 1 and 60 characters")
        if type(item.active_low) is not bool or (item.active_low and item.kind not in ("led", "rgb")):
            raise ValueError("Active-low polarity is only configurable for LEDs")
        ids.add(item.id)


def load_layout(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("components"), list):
        raise ValueError("This file is not a supported component layout")
    try:
        components = [Component(**item) for item in data["components"]]
    except TypeError as error:
        raise ValueError("This layout contains an invalid component") from error
    validate_components(components)
    return components


def layout_json(components):
    records = [{key: value for key, value in asdict(item).items() if value is not None} for item in components]
    return json.dumps({"version": 1, "components": records}, indent=2) + "\n"
