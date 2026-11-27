"""Virtual GPIO board and components, independent of the desktop interface."""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from rpi_sim_client import gpio

COMPONENT_NAMES = {"led": "LED", "button": "Push button", "pir": "PIR sensor"}


@dataclass(frozen=True)
class Component:
    id: str
    kind: str
    pin: int
    name: str
    active_low: bool = False


@dataclass
class Pin:
    direction: int | None = None
    pull: int = gpio.PUD_OFF
    output: int = gpio.LOW


@dataclass
class Edge:
    direction: int
    channel: int
    registration: int
    debounce: float
    last_seen: float = float("-inf")
    pending: bool = False


class Board:
    def __init__(self, pin_changed, edge_detected):
        self.pin_changed = pin_changed
        self.edge_detected = edge_detected
        self.components = {}
        self.input_active = {}
        self.mode = None
        self.pins = {bcm: Pin() for bcm in gpio.BCM_TO_BOARD}
        self.edges = {}
        self.commands = {
            "setmode": self.setmode, "getmode": self.getmode, "setup": self.setup,
            "input": self.input, "output": self.output, "cleanup": self.cleanup,
            "gpio_function": self.gpio_function, "add_event_detect": self.add_event_detect,
            "remove_event_detect": self.remove_event_detect, "event_detected": self.event_detected,
        }

    def replace_components(self, components):
        components = list(components)
        validate_components(components)
        self.components = {component.id: component for component in components}
        self.input_active = {}

    def put_component(self, component):
        existing = [item for item in self.components.values() if item.id != component.id]
        validate_components([*existing, component])
        previous = self.components.get(component.id)
        self.components[component.id] = component
        if previous and previous.pin != component.pin:
            self.input_active.pop(component.id, None)
            self.pin_changed(previous.pin)
        self.pin_changed(component.pin)

    def remove_component(self, component_id):
        component = self.components.pop(component_id)
        self.input_active.pop(component_id, None)
        self.pin_changed(component.pin)

    def set_input(self, component_id, active):
        component = self.components.get(component_id)
        if component is None or component.kind == "led":
            return
        bcm = component.pin
        previous = self.level(bcm)
        self.input_active[component_id] = active
        self._pin_updated(bcm, previous, detect_edge=self.pins[bcm].direction == gpio.IN)

    def level(self, bcm):
        pin = self.pins[bcm]
        if pin.direction == gpio.OUT:
            return pin.output
        for component in self.components.values():
            if component.pin == bcm:
                active = self.input_active.get(component.id, False)
                if component.kind == "pir":
                    return gpio.HIGH if active else gpio.LOW
                if component.kind == "button" and active:
                    return gpio.LOW
                break
        return gpio.HIGH if pin.pull == gpio.PUD_UP else gpio.LOW

    def component_active(self, component):
        if component.kind != "led":
            return self.input_active.get(component.id, False)
        return (
            self.pins[component.pin].direction == gpio.OUT
            and self.level(component.pin) == (gpio.LOW if component.active_low else gpio.HIGH)
        )

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
        for bcm, value in zip(pins, values):
            pin = self.pins[bcm]
            if pin.output != value:
                pin.output = int(value)
                self.pin_changed(bcm)

    def gpio_function(self, channel):
        direction = self.pins[self._pin(channel)].direction
        return gpio.IN if direction is None else direction

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


def validate_components(components):
    ids, pins = set(), set()
    for item in components:
        if not isinstance(item.id, str) or not item.id or item.id in ids:
            raise ValueError("Each component needs a unique ID")
        if not isinstance(item.kind, str) or item.kind not in COMPONENT_NAMES:
            raise ValueError("Unknown component type")
        if type(item.pin) is not int or item.pin not in gpio.BCM_TO_BOARD:
            raise ValueError("Choose a valid BCM GPIO pin")
        if item.pin in pins:
            raise ValueError(f"GPIO {item.pin} already has a component")
        if not isinstance(item.name, str) or not item.name.strip() or len(item.name) > 60:
            raise ValueError("Component names must have between 1 and 60 characters")
        if type(item.active_low) is not bool or (item.active_low and item.kind != "led"):
            raise ValueError("Active-low polarity is only configurable for LEDs")
        ids.add(item.id)
        pins.add(item.pin)


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
    return json.dumps({"version": 1, "components": [asdict(item) for item in components]}, indent=2) + "\n"
