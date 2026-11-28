# RPi Simulator

Run Python GPIO and I²C exercises on a virtual Raspberry Pi board. Add LEDs, RGB LEDs, buzzers, push buttons, and PIR sensors by GPIO pin. Add LCD1602 displays and MPU6050 sensors by I²C bus and address.

## Run

From this directory, use Python 3.10 or newer.

**macOS and Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m rpi_sim
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m rpi_sim
```

## Use the simulator

1. Select **Run** to start the built-in demo. **Trigger motion** lights one LED for two seconds. **Press button** lights the other until you select **Release button**. Both can be active together.
2. For your own script, choose **Layout → New layout**, then add components and match their GPIO pins to your script. Each pin picker shows the BCM number and its physical header position. Save or reopen layouts from the **Layout** menu.
3. Select **Browse** to open your script. Use **Python…** if the script needs packages from a different Python environment. Select **Run**. The console shows output and tracebacks, and **Send** enters a line into Python's `input()`.

A push button drives its pin low while held and releases it to the configured pull resistor. Configure a grounded button with `GPIO.PUD_UP` to get a high level on release. A PIR sensor drives high during motion and low when clear. LEDs default to lighting on a high output, with an active-low option in component settings.

Scripts keep their `import RPi.GPIO as GPIO` import. The simulator supports `BCM` and `BOARD` numbering, setup, digital input and output, pulls, edge callbacks with debounce, `event_detected()`, PWM, and cleanup. `wait_for_edge()` and timing-sensitive sensor protocols are outside the current implementation.

### Bundled examples

Choose **Layout → Open layout…** and open the example's JSON file. Then use **Browse…** to select the matching Python script and press **Run**.

| Script | Layout | What to expect |
| --- | --- | --- |
| [LED](examples/led.py) | [led.json](examples/led.json) | The LED on GPIO 18 lights for two seconds. |
| [Digital buzzer](examples/buzzer.py) | [buzzer.json](examples/buzzer.json) | The buzzer on GPIO 17 gives a short beep about once a second. |
| [Passive buzzer](examples/buzzer_passive.py) | [buzzer_passive.json](examples/buzzer_passive.json) | The buzzer on GPIO 18 plays a repeating 440 Hz PWM tone. |
| [RGB LED](examples/RGB_led.py) | [RGB_led.json](examples/RGB_led.json) | The RGB LED cycles through colors using red GPIO 12, green GPIO 13, and blue GPIO 19. |
| [Motion lights](examples/motion_lights.py) | [motion_lights.json](examples/motion_lights.json) | The button and PIR sensor control separate LEDs in the built-in demo. |
| [LCD1602 clock](examples/i2c/LCD1602.py) | [LCD1602.json](examples/i2c/LCD1602.json) | The 16×2 LCD at `0x27` shows the temperature and clock. On desktops without a CPU temperature file, the example uses 42 °C. |
| [MPU6050 motion](examples/i2c/gyro.py) | [gyro.json](examples/i2c/gyro.json) | The driver initializes the sensor at `0x68` and reports acceleration and rotation. Change the six values on the sensor card while it runs. |
| [Motion on the LCD](examples/i2c/motion_display.py) | [motion_display.json](examples/i2c/motion_display.json) | Both devices share bus 1, and the LCD displays the MPU6050's Z-axis readings. |

Buzzers play through the system's default audio output. A digital HIGH plays a fixed tone, and short digital pulses are extended to a brief audible beep. PWM plays at the requested frequency when the duty cycle is between 0% and 100%. A 0% duty cycle is silent, and a 100% duty cycle behaves like a steady HIGH.

RGB LEDs combine the digital or PWM brightness of their red, green, and blue channels and display the resulting hex color. Select active-low polarity for a common-anode RGB LED. Standard LEDs also show PWM brightness.

## Run a script from your terminal or IDE

On your PC, activate the Python environment you use for the script. From the `rpi-sim` directory, install the library and run the LED exercise.

```sh
python -m pip install .
python examples/led.py
```

Keep the simulator open with `examples/led.json` loaded. The LED exercise turns on the component at BCM GPIO 18 for two seconds. Launch the exercise in your terminal or IDE as usual and leave the simulator's **Run** button idle.

The `rpi-gpio-sim` package provides `RPi.GPIO`, `smbus`, and `smbus2` without a Qt dependency. Install it in a virtual environment for simulation on a PC. Use the hardware libraries in the Python environment on your Raspberry Pi.

The simulator shows its local address in the header and marks an active terminal or IDE connection as **External script**. Scripts use a persistent TCP connection to `127.0.0.1:8765` by default. Set `RPI_SIM_PORT` to the same value in both environments to use another port. One script controls the board at a time. Disconnecting clears the script's GPIO configuration and leaves the component layout in place.

## I²C simulation

Use `smbus.SMBus(1)` for byte, register, word, and block operations. Use `i2c_msg` with `SMBus.i2c_rdwr()` for raw combined transfers. GPIO and SMBus calls share a connection when a script uses both. The LCD supports the common PCF8574 and PCF8574A address ranges, including `0x27` and `0x3F`. The MPU6050 supports `0x68` and `0x69`. Addresses must be unique within a bus.

The virtual bus models 7-bit and 10-bit addressing, START and repeated START, STOP, acknowledgments, general call, optional Device ID, clock stretching, clock synchronization, arbitration, and SDA recovery. It uses simulated nanosecond timing. The GUI and Python scripts run on the desktop's wall clock.

`SMBus.configure()` selects `standard`, `fast`, `fast-plus`, `high-speed`, or `ultra-fast` mode. Use `trace=True` to record bus transitions and `SMBus.trace()` to retrieve rows of `[time_ns, SCL, SDA, event]`. `SMBus.contend()` starts multiple logical controllers on the same bus at the same simulated instant. The Ultra Fast-mode is write-only and requires a target that explicitly supports it. The bundled LCD and MPU6050 examples use Standard-mode.

```python
from smbus import SMBus, i2c_msg

with SMBus(1) as bus:
    bus.configure(trace=True)
    address = i2c_msg.write(0x68, [0x75])
    identity = i2c_msg.read(0x68, 1)
    bus.i2c_rdwr(address, identity)
    print(hex(list(identity)[0]))
    print([event for _, _, _, event in bus.trace() if event])
```

Load `examples/i2c/gyro.json` before running this snippet. The MPU6050 model simulates the register reads, memory verification, and FIFO behavior used by the driver's DMP initialization. Its FIFO supplies a stationary quaternion and the configured motion axes. The bus trace represents digital transitions and simulated time. Run the protocol and device tests with `python -m unittest discover -s tests`.

## Development summary

The project started with a goal of running existing Raspberry Pi GPIO exercises locally on macOS, Windows, and Linux. The Python and PySide6 desktop app uses direct pin assignment and supports multiple virtual LEDs, push buttons, and PIR sensors on one board. It can save component layouts and run existing scripts with an interactive console.

The next step was to support scripts launched from a terminal or IDE. The installable `rpi-gpio-sim` library keeps the original `RPi.GPIO` import and exchanges newline-delimited JSON with the desktop app over one persistent localhost TCP connection. The simulator acknowledges GPIO commands and pushes sensor edge events over the same connection. Both launch paths share one board model. A separate HTTP API was discussed and deferred.

The macOS checks ran unchanged LED, button, PIR, and BOARD-numbered blink exercises. They also verified simultaneous components, callbacks that make GPIO calls over TCP, and pin cleanup after a disconnect. In a loopback benchmark of 1,000 `GPIO.input()` calls, the median round-trip latency was 53 µs and the 95th percentile was 122 µs.

The standalone [LED example](examples/led.py) was copied unchanged from the original exercise. The project was published on GitHub with `master` as the default branch. [Public repository](https://github.com/bandjeo/rpi-sim)

PWM support added duty-cycle and frequency control, buzzer playback through the system's default audio output, and RGB LED color mixing. The original digital buzzer, passive PWM buzzer, and RGB LED exercises were copied into `examples` with matching layouts. The workspace virtual environment was updated with PySide6 Addons for Qt Multimedia.

The I²C work added `smbus` and `smbus2` compatibility on the same connection as GPIO. The digital bus uses simulated nanosecond timing for transfers and SDA/SCL traces. It models 7-bit and 10-bit addressing, repeated starts, clock stretching, arbitration, and bus recovery. The LCD1602 and MPU6050 can share a bus, as shown in the bundled motion display example.

On macOS, the supplied MPU6050 driver completed DMP initialization in about 1.3 seconds and reported motion at roughly 10 Hz. The LCD demo ran at both backpack addresses, and the installed client used I²C and GPIO together over TCP. [I²C examples](examples/i2c) · [Protocol and device tests](tests/test_i2c.py)
