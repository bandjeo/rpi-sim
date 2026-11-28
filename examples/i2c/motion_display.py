"""Show MPU6050 motion on the LCD sharing its I²C bus."""

from time import sleep

from LCD1602 import open_lcd
from gyro import open_gyro, read_motion


def main():
    lcd = open_lcd()
    lcd.message("Starting MPU6050")
    mpu = open_gyro()
    print("MPU6050 and LCD ready on I²C bus 1")
    try:
        while True:
            acceleration, rotation = read_motion(mpu)
            lcd.setCursor(0, 0)
            lcd.message(f"Accel Z {acceleration[2]:+5.1f}g".ljust(16))
            lcd.setCursor(0, 1)
            lcd.message(f"Gyro Z {rotation[2]:+6.0f}d/s".ljust(16)[:16])
            sleep(0.2)
    except KeyboardInterrupt:
        lcd.clear()


if __name__ == "__main__":
    main()
