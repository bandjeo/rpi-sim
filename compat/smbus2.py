"""Expose the simulator's SMBus interface under the smbus2 import name."""

from smbus import I2C_M_NOSTART, I2C_M_RD, I2C_M_STOP, I2C_M_TEN, SMBus, i2c_msg

__all__ = ["SMBus", "i2c_msg", "I2C_M_RD", "I2C_M_TEN", "I2C_M_NOSTART", "I2C_M_STOP"]
