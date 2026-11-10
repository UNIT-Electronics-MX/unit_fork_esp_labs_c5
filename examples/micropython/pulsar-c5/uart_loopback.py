"""P6: test UART1 with a jumper from TX0/GPIO11 to RX0/GPIO12."""

from machine import Pin, UART


# USB remains the REPL; these header pins carry the separate UART1 signal.
uart = UART(1, baudrate=115200, tx=Pin(11), rx=Pin(12), timeout=500)
print("P6 ready: bridge GPIO11 to GPIO12, then type a line")
try:
    while True:
        line = input("tx> ").strip()
        if not line:
            continue
        uart.write(line + "\n")
        response = uart.readline()
        if response is None:
            print("no response; check the jumper")
        else:
            print("loopback: {}".format(response.decode().strip()))
finally:
    uart.deinit()
