# RPi Simulator

Run Python GPIO exercises on a virtual Raspberry Pi board. Add multiple LEDs, push buttons, and PIR sensors, assign each one a GPIO pin, and interact with them while a script runs.

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

Scripts keep their `import RPi.GPIO as GPIO` import. The simulator supports `BCM` and `BOARD` numbering, setup, digital input and output, pulls, edge callbacks with debounce, `event_detected()`, and cleanup. PWM, `wait_for_edge()`, and timing-sensitive sensor protocols are outside the current implementation.

## Run a script from your terminal or IDE

On your PC, activate the Python environment you use for the script. From the `rpi-sim` directory, install the library and run the LED exercise.

```sh
python -m pip install .
python examples/led.py
```

Keep the simulator open with the demo layout loaded. Its **Motion light** is already assigned to BCM GPIO 18. The LED exercise turns it on for two seconds. Launch the exercise in your terminal or IDE as usual and leave the simulator's **Run** button idle.

The `rpi-gpio-sim` package provides the `RPi.GPIO` import without a Qt dependency. Install it in a virtual environment for simulation on a PC. Use the hardware library in the Python environment on your Raspberry Pi.

The simulator shows its local address in the header and marks an active terminal or IDE connection as **External script**. Scripts use a persistent TCP connection to `127.0.0.1:8765` by default. Set `RPI_SIM_PORT` to the same value in both environments to use another port. One script controls the board at a time. Disconnecting clears the script's GPIO configuration and leaves the component layout in place.
