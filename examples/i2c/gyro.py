"""Run the supplied MPU6050 driver's startup and read the raw motion axes."""

from time import sleep

from MPU6050 import MPU6050


def open_gyro():
    mpu = MPU6050()
    status = mpu.dmp_initialize()
    if status:
        raise RuntimeError(f"MPU6050 DMP initialization failed with code {status}")
    return mpu


def read_motion(mpu):
    acceleration = tuple(value / 16384.0 for value in mpu.get_acceleration())
    rotation = tuple(value / 16.4 for value in mpu.get_rotation())
    return acceleration, rotation


def main():
    mpu = open_gyro()
    print("MPU6050 initialized")
    try:
        while True:
            acceleration, rotation = read_motion(mpu)
            print("a (g): {:+.2f} {:+.2f} {:+.2f}  g (°/s): {:+.2f} {:+.2f} {:+.2f}".format(
                *acceleration, *rotation
            ))
            sleep(0.1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
