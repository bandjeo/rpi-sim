import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)

TRIG_PIN = 4
ECHO_PIN = 17

GPIO.setup(TRIG_PIN, GPIO.OUT)
GPIO.setup(ECHO_PIN, GPIO.IN)

def get_distance(timeout=0.04):
    GPIO.output(TRIG_PIN, False)
    time.sleep(0.05)

    GPIO.output(TRIG_PIN, True)
    time.sleep(0.00001)
    GPIO.output(TRIG_PIN, False)

    capture = getattr(GPIO, "capture_pulses", None)
    if capture is not None:
        frame = capture(ECHO_PIN, timeout=timeout)
        if not frame:
            return None
        return frame[0][1] * 34300 / 2_000_000

    deadline = time.time() + timeout
    while GPIO.input(ECHO_PIN) == 0:
        if time.time() > deadline:
            return None
    pulse_start = time.time()

    deadline = pulse_start + timeout
    while GPIO.input(ECHO_PIN) == 1:
        if time.time() > deadline:
            return None
    pulse_end = time.time()

    return (pulse_end - pulse_start) * 34300 / 2

if __name__ == '__main__':
    try:
        while True:
            distance = get_distance()
            if distance is not None:
                print(f'Distance: {distance:.1f} cm')
            else:
                print('Measurement timed out')
            time.sleep(1)
    except KeyboardInterrupt:
        print('Measurement stopped by user')
    finally:
        GPIO.cleanup()
