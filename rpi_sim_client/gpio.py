"""GPIO constants and header numbering shared by the simulator and client."""

LOW, HIGH = 0, 1
OUT, IN = 0, 1
BCM, BOARD = 11, 10
PUD_OFF, PUD_DOWN, PUD_UP = 20, 21, 22
RISING, FALLING, BOTH = 31, 32, 33

BOARD_TO_BCM = {
    3: 2, 5: 3, 7: 4, 8: 14, 10: 15, 11: 17, 12: 18, 13: 27,
    15: 22, 16: 23, 18: 24, 19: 10, 21: 9, 22: 25, 23: 11, 24: 8,
    26: 7, 27: 0, 28: 1, 29: 5, 31: 6, 32: 12, 33: 13, 35: 19,
    36: 16, 37: 26, 38: 20, 40: 21,
}
BCM_TO_BOARD = {bcm: physical for physical, bcm in BOARD_TO_BCM.items()}
