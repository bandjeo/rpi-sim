"""Display a temperature and clock on the PCF8574-backed LCD."""

import errno
from datetime import datetime
from pathlib import Path
from time import sleep

from Adafruit_LCD1602 import Adafruit_CharLCD
from PCF8574 import PCF8574_GPIO


def open_lcd():
    try:
        backpack = PCF8574_GPIO(0x27)
    except OSError as error:
        if error.errno != errno.ENXIO:
            raise
        backpack = PCF8574_GPIO(0x3F)
    lcd = Adafruit_CharLCD(pin_rs=0, pin_e=2, pins_db=[4, 5, 6, 7], GPIO=backpack)
    backpack.output(3, 1)
    lcd.begin(16, 2)
    return lcd


def get_cpu_temp():
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return float(path.read_text()) / 1000
    except FileNotFoundError:
        return 42.0


def main():
    lcd = open_lcd()
    print("LCD ready")
    try:
        while True:
            lcd.setCursor(0, 0)
            lcd.message(f"CPU: {get_cpu_temp():.2f} C".ljust(16))
            lcd.setCursor(0, 1)
            lcd.message(datetime.now().strftime("    %H:%M:%S"))
            sleep(1)
    except KeyboardInterrupt:
        lcd.clear()


if __name__ == "__main__":
    main()
