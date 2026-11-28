import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
buzzer_pin = 18
GPIO.setup(buzzer_pin, GPIO.OUT)
buzzer = GPIO.PWM(buzzer_pin, 440)

def buzz(pitch=440, duration = 0.5):
    buzzer.ChangeFrequency(pitch)
    buzzer.start(50)
    time.sleep(duration)
    buzzer.stop()

try:
    while True:
        buzz()
        time.sleep(1)
except KeyboardInterrupt:
    GPIO.cleanup()