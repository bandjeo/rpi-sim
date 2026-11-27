"""Exercise all four components in the built-in demo layout."""

import RPi.GPIO as GPIO

MOTION, BUTTON = 17, 24
MOTION_LIGHT, MANUAL_LIGHT = 18, 23


def show_motion(channel):
    active = GPIO.input(channel) == GPIO.HIGH
    GPIO.output(MOTION_LIGHT, active)
    print("Motion detected" if active else "Motion cleared")


def show_button(channel):
    active = GPIO.input(channel) == GPIO.LOW
    GPIO.output(MANUAL_LIGHT, active)
    print("Button pressed" if active else "Button released")


def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(MOTION, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup([MOTION_LIGHT, MANUAL_LIGHT], GPIO.OUT, initial=GPIO.LOW)
    try:
        GPIO.add_event_detect(MOTION, GPIO.BOTH, callback=show_motion)
        GPIO.add_event_detect(BUTTON, GPIO.BOTH, callback=show_button)
        GPIO.output(MOTION_LIGHT, GPIO.input(MOTION) == GPIO.HIGH)
        GPIO.output(MANUAL_LIGHT, GPIO.input(BUTTON) == GPIO.LOW)
        print("Use the PIR sensor and push button to control the two lights.")
        input("Send an empty line to exit.\n")
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
