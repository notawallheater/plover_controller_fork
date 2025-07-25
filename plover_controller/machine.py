# This file is part of plover-controller.  # Copyright (C) 2022 Tadeo Kondrak
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# {{{ imports


#from collections import OrderedDict
from dataclasses import dataclass
from math import cos, sin, tau, hypot
import sdl2
import threading
from threading import Timer
from time import sleep
from uuid import UUID, uuid4
import ctypes
import typing
from plover_controller.config import (
    Stick,
    Mappings,
)
from .util import stick_segment, buttons_to_keys
from copy import copy
#from plover import system
#from plover.config import system_keymap_option, Config
from plover.engine import StenoEngine
from plover.gui_qt.tool import Tool
from plover import log
from plover.machine.base import StenotypeBase
#from plover.machine.keymap import Keymap
from plover.misc import boolean
from plover.resource import resource_exists, resource_filename
from PyQt5.QtCore import QVariant, pyqtSignal, Qt, QSize, QLineF, QPointF, QRectF
from PyQt5.QtGui import QFont, QPainter, QPen, QBrush
from typing import Any, Callable, Optional
from PyQt5.QtWidgets import (
    QWidget,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from sdl2 import (
    SDL_Event,
    SDL_free,
    SDL_GetError,
    SDL_HAT_CENTERED,
    SDL_HAT_DOWN,
    SDL_HAT_LEFT,
    SDL_HAT_LEFTDOWN,
    SDL_HAT_LEFTUP,
    SDL_HAT_RIGHT,
    SDL_HAT_RIGHTDOWN,
    SDL_HAT_RIGHTUP,
    SDL_HAT_UP,
    SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS,
    SDL_HINT_JOYSTICK_HIDAPI,
    SDL_HINT_JOYSTICK_RAWINPUT_CORRELATE_XINPUT,
    SDL_HINT_JOYSTICK_RAWINPUT,
    SDL_HINT_JOYSTICK_THREAD,
    SDL_HINT_NO_SIGNAL_HANDLERS,
    SDL_INIT_JOYSTICK,
    SDL_INIT_VIDEO,
    SDL_Init,
    SDL_JoystickOpen,
    SDL_NumJoysticks,
    SDL_PushEvent,
    SDL_Quit,
    SDL_RegisterEvents,
    SDL_SetHint,
    SDL_WaitEvent,
#    SDL_AddTimer,
#    SDL_TimerCallback,
#    SDL_INIT_TIMER,
)

SDL_strdup_void = sdl2.dll._bind("SDL_strdup", [ctypes.c_char_p], ctypes.c_void_p)
# }}}

# {{{ mapping
HAT_VALUES = {
    SDL_HAT_CENTERED: "c",
    SDL_HAT_UP: "u",
    SDL_HAT_RIGHT: "r",
    SDL_HAT_DOWN: "d",
    SDL_HAT_LEFT: "l",
    SDL_HAT_RIGHTUP: "ur",
    SDL_HAT_RIGHTDOWN: "dr",
    SDL_HAT_LEFTUP: "ul",
    SDL_HAT_LEFTDOWN: "dl",
}

mapping_path = "asset:plover_controller:assets/default_mapping.txt"
if not resource_exists(mapping_path):
    raise Exception("couldn't find default mapping file")

with open(typing.cast(str, resource_filename(mapping_path)), "r") as f:
    DEFAULT_MAPPING = f.read()

# }}}


# {{{ event classes
@dataclass
class Event:
    maybe_stroke_complete_event: Optional[int] = None

    
    @classmethod
    def from_sdl(cls, ev: SDL_Event) -> Optional["Event"]:
        if ev.type == cls.maybe_stroke_complete_event:
            _char_p = ctypes.cast(ev.user.data1, ctypes.c_char_p)
            _py_string = _char_p.value.decode('utf-8')
            SDL_free(ev.user.data1)
            return CheckCompleteEvent(
                stickname=_py_string,
            )
        elif ev.type == sdl2.SDL_JOYAXISMOTION:
            return AxisEvent(
                axis=ev.jaxis.axis,
                value=float(ev.jaxis.value) / 32768,
                device=ev.jaxis.which,
            )
        elif ev.type == sdl2.SDL_JOYBALLMOTION:
            return BallEvent(
                ball=ev.jball.ball,
                device=ev.jball.which,
            )
        elif ev.type == sdl2.SDL_JOYHATMOTION:
            return HatEvent(
                hat=ev.jhat.hat,
                value=ev.jhat.value,
                device=ev.jhat.which,
            )
        elif ev.type == sdl2.SDL_JOYBUTTONDOWN:
            return ButtonEvent(
                state=True,
                button=ev.jbutton.button,
                device=ev.jbutton.which,
            )
        elif ev.type == sdl2.SDL_JOYBUTTONUP:
            return ButtonEvent(
                state=False,
                button=ev.jbutton.button,
                device=ev.jbutton.which,
            )
        elif ev.type == sdl2.SDL_JOYDEVICEADDED:
            return DeviceEvent(
                added=True,
                which=ev.jdevice.which,
            )
        elif ev.type == sdl2.SDL_JOYDEVICEREMOVED:
            return DeviceEvent(
                added=False,
                which=ev.jdevice.which,
            )


@dataclass
class CheckCompleteEvent(Event):
    stickname: str = ""
    
@dataclass
class AxisEvent(Event):
    axis: int = -1
    value: float = 0.0
    device: int = -1


@dataclass
class BallEvent(Event):
    ball: int = -1
    device: int = -1


@dataclass
class HatEvent(Event):
    hat: int = -1
    value: int = -1
    device: int = -1


@dataclass
class ButtonEvent(Event):
    button: int = -1
    state: bool = False
    device: int = -1


@dataclass
class DeviceEvent(Event):
    which: int = -1
    added: bool = False
# }}}

# {{{ controller thread
controller_thread_instance = None


def get_controller_thread():
    global controller_thread_instance
    if controller_thread_instance is not None:
        return controller_thread_instance
    controller_thread_instance = ControllerThread()
    controller_thread_instance.start()
    return controller_thread_instance

def get_sdl_error():
    error = typing.cast(bytes, SDL_GetError())
    if error:
        raise Exception(f"SDL error occurred: {error.decode('utf-8')}")
    else:
        raise Exception("Unknown SDL error occurred")

class ControllerThread(threading.Thread):
    lock = threading.Lock()
    set_hint_event_type: Optional[int] = None
    listeners: set[Callable[[Event], None]] = set()

    def __init__(self):
        super().__init__()

    def run(self):
        with self.lock:
            SDL_Quit()
            SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
            SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, b"1")
            SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK) #| SDL_INIT_TIMER)
            self.set_hint_event_type = SDL_RegisterEvents(1)
            Event.maybe_stroke_complete_event = SDL_RegisterEvents(1)

            for i in range(typing.cast(int, SDL_NumJoysticks())):
                SDL_JoystickOpen(i)

        event = SDL_Event()
        while True:
            if not SDL_WaitEvent(event):
                get_sdl_error()
            with self.lock:
                if event.type == self.set_hint_event_type:
                    SDL_SetHint(
                        ctypes.cast(event.user.data1, ctypes.c_char_p),
                        ctypes.cast(event.user.data2, ctypes.c_char_p),
                    )
                    SDL_free(event.user.data1)
                    SDL_free(event.user.data2)
                else:
                    if converted_event := Event.from_sdl(event):
                        if isinstance(converted_event, DeviceEvent):
                            if converted_event.added:
                                SDL_JoystickOpen(converted_event.which)
                        for listener in self.listeners:
                            listener(converted_event)

    def add_listener(self, listener: Callable[[Event], None]):
        with self.lock:
            self.listeners.add(listener)

    def remove_listener(self, listener: Callable[[Event], None]):
        with self.lock:
            self.listeners.remove(listener)

    def set_hint(self, name: bytes, value: bytes):
        with self.lock:
            event = SDL_Event()
            event.type = self.set_hint_event_type
            event.user.data1 = SDL_strdup_void(name)
            event.user.data2 = SDL_strdup_void(value)
            SDL_PushEvent(event)
# }}}

# {{{ controller state

class ControllerState:
    # Machine settings
    _params: dict[str, Any] = {}
    # Parsed configuration file
    #_mappings: Mappings = Mappings.empty() #Mappings.parse creates instance
    _mappings: Mappings = None
    # Last received axis values for mapped sticks, keyed by a{int}
    _stick_states: dict[str, float] = {}
    _stick_listening: dict[str, bool] = {}
    _stick_active: dict[str, bool] = {}
    # Whether a stick was in the deadzone in the previous check
    _fresh_from_deadzone: dict[str, bool] = {}
    _fresh_from_holdzone: dict[str, bool] = {}
    # Last received axis values for mapped triggers, keyed by a{int}
    _trigger_states: dict[str, float] = {}
    # Last received values for hats, keyed by alias
    _hat_states: dict[str, int] = {}
    # Keys fully triggered by completed chords
    _pending_keys: set[str] = set()
#    _pending_stick_movements: dict[str, list[str]] = {}
    _pending_stick_movements: dict[str, list[list[str]]] = {}
    _previous_segments: dict[str, int] = {}
    _pending_hat_values: dict[str, set[int]] = {}
    _unsequenced_buttons_and_hats: set[str] = set()
    # All buttons currently pressed
    _currently_pressed_buttons: set[str] = set()
    _currently_uncentered_hats: set[str] = set()
    # Function called with stroke data when complete
    _notify: Callable[[list[str]], None]
    # timers for delayed callbacks
    _timers: dict[UUID] = {}

    def __init__(self, params: dict[str, Any], notify: Callable[[list[str]], None]):
        super().__init__()
        self._params = params
        self._mappings = Mappings.parse(self._params["mapping"])
        self._pending_stick_movements = {stick.name: [[]] for stick in self._mappings.sticks.values()}
        log.error(f"init psm is : {self._pending_stick_movements}")
        self._notify = notify

    def _handle_event(self, event: Event):
        if isinstance(event, CheckCompleteEvent):
            self._handle_checkcomplete_event(event)
        elif isinstance(event, AxisEvent):
            self._handle_axis_event(event)
        elif isinstance(event, BallEvent):
            self._handle_ball_event(event)
        elif isinstance(event, HatEvent):
            self._handle_hat_event(event)
        elif isinstance(event, ButtonEvent):
            self._handle_button_event(event)
        elif isinstance(event, DeviceEvent):
            self._handle_device_event(event)

    def _handle_axis_event(self, event: AxisEvent):
        axis = f"a{event.axis}"
        if axis in self._mappings.triggers:
            self._trigger_states[axis] = event.value
            self.check_trigger_axes()
        elif axis in self._mappings.stick_axes:
            self._stick_states[axis] = event.value
            self.check_stick(self._mappings.stick_axes[axis])
        self.maybe_complete_stroke()

    def _handle_ball_event(self, event: BallEvent):
        pass

    def _handle_hat_event(self, event: HatEvent):
        hat = f"h{event.hat}"
        if hat_entry := self._mappings.hats.get(hat):
            hat = hat_entry.renamed
        self._hat_states[hat] = event.value
        if event.value == 0:
            self.complete_hat(hat)
            self._currently_uncentered_hats.discard(hat)
            self.maybe_complete_stroke()
        else:
            self._currently_uncentered_hats.add(hat)
            self._pending_hat_values.setdefault(hat, set()).add(event.value)

    def _handle_button_event(self, event: ButtonEvent):
        button = f"b{event.button}"
        if button_entry := self._mappings.buttons.get(button):
            button = button_entry.renamed
        if event.state:
            self._currently_pressed_buttons.add(button)
            if button not in self._unsequenced_buttons_and_hats:
                self._unsequenced_buttons_and_hats.add(button)
        else:
            self._currently_pressed_buttons.discard(button)
            self.maybe_complete_stroke()

    def _handle_device_event(self, event: DeviceEvent):
        pass

    def complete_hat(self, hat: str):
        pending_values = self._pending_hat_values.get(hat, set())
        if SDL_HAT_RIGHTUP in pending_values:
            pending_values.discard(SDL_HAT_RIGHT)
            pending_values.discard(SDL_HAT_UP)
        if SDL_HAT_RIGHTDOWN in pending_values:
            pending_values.discard(SDL_HAT_RIGHT)
            pending_values.discard(SDL_HAT_DOWN)
        if SDL_HAT_LEFTUP in pending_values:
            pending_values.discard(SDL_HAT_LEFT)
            pending_values.discard(SDL_HAT_UP)
        if SDL_HAT_LEFTDOWN in pending_values:
            pending_values.discard(SDL_HAT_LEFT)
            pending_values.discard(SDL_HAT_DOWN)
        for value in pending_values:
            self._unsequenced_buttons_and_hats.add(f"{hat}{HAT_VALUES[value]}")
        del self._pending_hat_values[hat]

    def any_active_inputs(self):
        return (
            any(self._stick_listening.get(stick.name, False)
                    or
                self._stick_active.get(stick.name, False)
                    for
                stick in self._mappings.sticks.values()
            )
            or any(v > 0 for v in self._trigger_states.values())
            or self._currently_pressed_buttons
            or self._currently_uncentered_hats
        )

    def process_stick_movements(self):

        def process(start_idx, end_idx):
            if start_idx == end_idx:
                for key in pending_movements[start_idx:]:
                    self._unsequenced_buttons_and_hats.add(key)
            else:
                key = tuple(pending_movements[start_idx:end_idx])
                ordered_mapping = self._mappings.ordered_mappings.get(key)
                if ordered_mapping is not None:
                    self._pending_keys.update(ordered_mapping)
                    process(end_idx, len(pending_movements))
                else:
                    process(start_idx, end_idx - 1)

        for stick in self._mappings.sticks.values():
            for pending_movements in self._pending_stick_movements[stick.name]:
                process(0, len(pending_movements))
            self._pending_stick_movements[stick.name] = [[]]



    def maybe_complete_stroke(self):
        if self.any_active_inputs():
            return
        self.process_stick_movements()
        keys = buttons_to_keys(
            self._unsequenced_buttons_and_hats,
            self._mappings.unordered_mappings,
        ).union(self._pending_keys)
        self._unsequenced_buttons_and_hats.clear()
        self._pending_keys.clear()
        if keys:
            self._notify(list(keys))


    def check_trigger_axes(self):
        for trigger in self._mappings.triggers.values():
            val = self._trigger_states.get(trigger.actual, 0)
            if val > 0:
                self._unsequenced_buttons_and_hats.add(trigger.renamed)

    def _handle_checkcomplete_event(self, ev:CheckCompleteEvent):
        self._stick_listening[ev.stickname] = False
        self.maybe_complete_stroke()

        

    def stick_timeout_cb(self, stickname: str, timer_id: UUID):
        event = SDL_Event()
        event.type = Event.maybe_stroke_complete_event
        event.user.data1 = SDL_strdup_void(stickname.encode('utf-8'))
        SDL_PushEvent(event)
        del self._timers[timer_id]
        

    def add_timer(self, stick: Stick):
        t_id = uuid4()
        t = Timer(self._params["stick_timeout"] / 1000.0, self.stick_timeout_cb, [stick.name, t_id])
        self._timers[t_id] = t
        t.start()


#    def check_stick(self, stick: Stick, lr: float, ud: float):
    def check_stick(self, stick: Stick):

        lr = self._stick_states.get(stick.x_axis, 0.0)
        ud = self._stick_states.get(stick.y_axis, 0.0)
        d = hypot(lr, ud)


        if ((d < self._params["stroke_end_threshold"])
            and self._stick_active.get(stick.name, False)
            and not self._stick_listening.get(stick.name, False)
        ):
            self.add_timer(stick)
            self._stick_listening[stick.name] = True
            self._stick_active[stick.name] = False
            self._pending_stick_movements[stick.name].append([])
            

        if d < self._params["stick_dead_zone"]:
            self._fresh_from_deadzone[stick.name] = True
            self._fresh_from_holdzone[stick.name] = False
            self._previous_segments[stick.name] = None
        elif d < self._params["stick_hold_zone"]:
            self._fresh_from_holdzone[stick.name] = True
        elif (segment_index := stick_segment(
            offset=stick.offset,
            segment_count=len(stick.segments),
            previous_segment=self._previous_segments.get(stick.name),
            jitter_guard=self._params["jitter_guard"],
            lr=lr,
            ud=ud)
        ) is not None:
            self._previous_segments[stick.name]=segment_index
            direction = stick.segments[segment_index]
            segment_name = f"{stick.name}{direction}"
            inorder_list = self._pending_stick_movements[stick.name][-1]
            if (len(inorder_list) == 0 or
                segment_name != inorder_list[-1] or
                self._fresh_from_holdzone[stick.name]
            ):
                inorder_list.append(segment_name)
            self._stick_active[stick.name] = True
            self._fresh_from_deadzone[stick.name] = False
            self._fresh_from_holdzone[stick.name] = False
# }}}


# {{{ controller machine

class ControllerMachine(StenotypeBase):

    KEYS_LAYOUT = '''
        #
        🎮- 🎹- 🦐- 🐦- 🦅-
        S- T- K- P- W- H- R-
        A- O-
        *
        -E -U
        -F -R -P -B -L -G -T -S -D -Z
    '''

    _state: ControllerState

    def __init__(self, params: dict[str, Any]):
        super().__init__()
        ControllerMachine._state = ControllerState(params, self._wrap_notify)

    def _wrap_notify(self, keys: list[str]):
        self._notify(self.keymap.keys_to_actions(keys))

    def start_capture(self):
        self._initializing()
        get_controller_thread().add_listener(self._state._handle_event)
        hints = [
            (SDL_HINT_JOYSTICK_HIDAPI, self._state._params["use_hidapi"]),
            (SDL_HINT_JOYSTICK_RAWINPUT, self._state._params["use_rawinput"]),
            (
                SDL_HINT_JOYSTICK_RAWINPUT_CORRELATE_XINPUT,
                self._state._params["correlate_rawinput"],
            ),
            (SDL_HINT_JOYSTICK_THREAD, self._state._params["use_joystick_thread"]),
        ]
        for name, value in hints:
            get_controller_thread().set_hint(name, b"1" if value else b"0")
        self._ready()

    def stop_capture(self):
        get_controller_thread().remove_listener(self._state._handle_event)
        self._stopped()

    @classmethod
    def get_option_info(cls) -> dict[str, tuple[Any, Callable[[str], Any]]]:
        return {
            "mapping": (DEFAULT_MAPPING, str),
            "stick_timeout": (100, int),
            "stroke_end_threshold": (0.4, float),
            "stick_dead_zone": (0.6, float),
            "stick_hold_zone": (0.9, float),
            "trigger_dead_zone": (0.9, float),
            "jitter_guard": (0.3, float),
            "use_hidapi": (True, boolean),
            "use_rawinput": (False, boolean),
            "correlate_rawinput": (False, boolean),
            "use_joystick_thread": (False, boolean),
        }

# }}}

# {{{ gui

class ControllerOption(QGroupBox):
    axis_message = pyqtSignal(str)
    other_message = pyqtSignal(str)
    valueChanged = pyqtSignal(QVariant)
    _value = {}
    _last_axis_message = None
    _last_other_message = None
    _spin_boxes = {}
    _check_boxes = {}

    SPIN_BOXES = {
        "stroke_end_threshold": "Stick stroke end threshold ([0-1]):",
        "stick_dead_zone": "Stick dead zone:",
        "stick_timeout": "Stick dead zone timeout (milliseconds)",
        "stick_hold_zone": "Stick hold zone:",
        "jitter_guard": "Stick jitter guard (radians):",
        "trigger_dead_zone": "Trigger dead zone:",
    }

    CHECK_BOXES = {
        "use_hidapi": "Use hidapi drivers:\n(reconnect controller and/or restart after change)",
        "use_rawinput": "Use rawinput drivers:\n(reconnect controller and/or restart after change)",
        "correlate_rawinput": "Correlate rawinput and xinput data:\n(reconnect controller and/or restart after change)",
        "use_joystick_thread": "Use joystick thread:\n(restart after change)",
    }

    def __init__(self):
        super().__init__()
        self.valueChanged.connect(self.setValue)

        self._form_layout = QFormLayout(self)

        for property, description in __class__.SPIN_BOXES.items():

            def value_changed(value, property=property):
                if value == self._value.get(property):
                    return
                self._value[property] = value
                self.valueChanged.emit(self._value)

            label = QLabel(description, self)
            spin_box = QDoubleSpinBox(self)
            spin_box.setSingleStep(0.1)
            spin_box.valueChanged.connect(value_changed)
            self._form_layout.addRow(label, spin_box)
            self._spin_boxes[property] = spin_box

        for property, description in __class__.CHECK_BOXES.items():

            def state_changed(state, property=property):
                value = state == Qt.CheckState.Checked
                if value == self._value.get(property):
                    return
                self._value[property] = value
                self.valueChanged.emit(self._value)

            label = QLabel(description, self)
            check_box = QCheckBox(self)
            check_box.stateChanged.connect(state_changed)
            self._form_layout.addRow(label, check_box)
            self._check_boxes[property] = check_box

        self._mapping_label = QLabel("Mapping:", self)
        self._mapping_text_edit = QTextEdit(self)
        self._mapping_text_edit.setFont(QFont("Monospace"))
        self._mapping_text_edit.textChanged.connect(self.mapping_changed)
        self._mapping_reset_button = QPushButton("Reset mapping to default", self)
        self._mapping_reset_button.clicked.connect(self.reset_mapping)
        self._mapping_layout = QVBoxLayout()
        self._mapping_layout.addWidget(self._mapping_text_edit)
        self._mapping_layout.addWidget(self._mapping_reset_button)
        self._form_layout.addRow(self._mapping_label, self._mapping_layout)

        self._axis_feedback_label = QLabel("Last axis event:", self)
        self._axis_feedback_output_label = QLabel(self)
        self._axis_feedback_output_label.setFont(QFont("Monospace"))
        self._form_layout.addRow(
            self._axis_feedback_label, self._axis_feedback_output_label
        )
        self.axis_message.connect(self._axis_feedback_output_label.setText)

        self._feedback_label = QLabel("Last other event:", self)
        self._feedback_output_label = QLabel(self)
        self._feedback_output_label.setFont(QFont("Monospace"))
        self._form_layout.addRow(self._feedback_label, self._feedback_output_label)
        self.other_message.connect(self._feedback_output_label.setText)

        get_controller_thread().add_listener(self._handle_event)

        def handle_destroy():
            get_controller_thread().remove_listener(self._handle_event)

        self.destroyed.connect(handle_destroy)

    def _handle_event(self, ev: Event):
        if isinstance(ev, AxisEvent):
            if ev.value < 0.25:
                return
            message = f"Axis {ev.axis} motion (device: {ev.device}, value: {ev.value:.4f})"
            if message != self._last_axis_message:
                try:
                    self.axis_message.emit(message)
                except RuntimeError:
                    pass
            self._last_axis_message = message
            return

        elif isinstance(ev, BallEvent):
            message = f"Ball {ev.ball} motion (device: {ev.device})"

        elif isinstance(ev, HatEvent):
            if ev.value == 0:
                message = f"Hat {ev.hat} centered (device: {ev.device})"
            else:
                message = (
                    f"Hat {ev.hat} event {HAT_VALUES[ev.value]} (device: {ev.device})"
                )

        elif isinstance(ev, ButtonEvent):
            message = f"Button {ev.button} {'pressed' if ev.state else 'released'} (device: {ev.device})"

        elif isinstance(ev, DeviceEvent):
            message = f"Device {ev.which} {'added' if ev.added else 'removed'}"

        else:
            return

        if message != self._last_other_message:
            try:
                self.other_message.emit(message)
            except RuntimeError:
                pass
        self._last_other_message = message

    def setValue(self, value):
        self._value = copy(value)
        for property in __class__.SPIN_BOXES.keys():
            if property in value:
                self._spin_boxes[property].setValue(value[property])
        for property in __class__.CHECK_BOXES.keys():
            if property in value:
                if value[property] == True:
                    self._check_boxes[property].setCheckState(Qt.CheckState.Checked)
                else:
                    self._check_boxes[property].setCheckState(Qt.CheckState.Unchecked)
        if (mapping := value.get("mapping")) is not None:
            existing = self._mapping_text_edit.toPlainText()
            if mapping != existing:
                self._mapping_text_edit.setPlainText(mapping)

    def mapping_changed(self):
        text = self._mapping_text_edit.toPlainText()
        if text == self._value.get("mapping"):
            return
        self._value["mapping"] = text
        self.valueChanged.emit(self._value)

    def reset_mapping(self):
        self._mapping_text_edit.setPlainText(DEFAULT_MAPPING)


class StickWidget(QWidget):
    stick: Stick
    state: ControllerState

    def sizeHint(self) -> QSize:
        return QSize(100, 100)

    def minimumSizeHint(self) -> QSize:
        return QSize(50, 50)

    def paintEvent(self, event):
        def convx(v: float) -> float:
            return width / 2 + v * width / 2

        def convy(v: float) -> float:
            return height / 2 + v * height / 2

        def draw_deadzone(v: float):
            painter.drawArc(
                QRectF(
                    width / 2 - width * v / 2,
                    height / 2 - height * v / 2,
                    width * v,
                    height * v,
                ),
                0,
                16 * 360,
            )

        painter = QPainter(self)
        dev = painter.device()
        assert dev is not None
        width = dev.width()
        height = dev.height()
        x = self.state._stick_states.get(self.stick.x_axis, 0.0)
        y = self.state._stick_states.get(self.stick.y_axis, 0.0)
        painter.drawLine(QLineF(convx(0), convy(0), convx(x), convy(y)))

        painter.setPen(QPen(Qt.GlobalColor.lightGray, 1, Qt.PenStyle.DotLine))
        draw_deadzone(self.state._params["stroke_end_threshold"])
        painter.setPen(QPen(Qt.GlobalColor.darkGray, 1, Qt.PenStyle.DotLine))
        draw_deadzone(self.state._params["stick_dead_zone"])

        angle = self.stick.offset / 360 * tau - tau / 4
        step = tau / len(self.stick.segments)
        for segment in self.stick.segments:
            x, y = -sin(angle), cos(angle)
            painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QLineF(convx(0), convy(0), convx(x), convy(y)))

            font_metrics = painter.fontMetrics()
            text_width = font_metrics.horizontalAdvance(segment) / width
            text_height = font_metrics.height() / height

            midx, midy = (
                -sin(angle + step / 2) * 0.9 - text_width / 2,
                cos(angle + step / 2) * 0.9 + text_height / 2,
            )
            painter.setPen(QPen(Qt.GlobalColor.black, 1, Qt.PenStyle.SolidLine))
            painter.setBrush(QBrush(Qt.GlobalColor.black))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(QPointF(convx(midx), convy(midy)), segment)

            angle += step


class ControllerDisplayTool(Tool):
    TITLE = "Controller Display"
    ICON = ""
    ROLE = "controller_display_tool"

    events = pyqtSignal(Event)

    _state: ControllerState
    _sticks: dict[str, StickWidget] = {}
    _dying: bool = False

    def __init__(self, engine: StenoEngine):
        super().__init__(engine)

        self.events.connect(self._handle_event_signal)
        self._layout = QFormLayout(self)
        self._engine.signal_connect("config_changed", self._handle_config_changed)
        self._handle_config_changed(None)

        get_controller_thread().add_listener(self._handle_event)

        def handle_destroy():
            get_controller_thread().remove_listener(self._handle_event)

        self.destroyed.connect(handle_destroy)

    def _handle_config_changed(self, _: Any):
        if self._dying:
            return
        if self._engine.config["machine_type"] != "Controller":
            self._sticks.clear()
            return
        params = self._engine.config["machine_specific_options"]
        self._state = ControllerState(params, self._ignore_notify)
        self._sticks.clear()
        for stick in self._state._mappings.sticks.values():
            widget = StickWidget(self)
            widget.stick = stick
            widget.state = self._state
            self._sticks[stick.name] = widget
            self._layout.addRow(QLabel(f"Stick {stick.name}", self), widget)

    def _ignore_notify(self, _: list[str]):
        pass

    def _make_stick_widget(self, stick: Stick):
        pass

    def _handle_event(self, event: Event):
        self.events.emit(event)

    def _handle_event_signal(self, event: Event):
        self._state._handle_event(event)
        if not self._dying:
            self.update()
# }}}
