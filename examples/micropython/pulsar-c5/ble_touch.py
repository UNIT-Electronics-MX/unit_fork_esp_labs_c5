"""Pulsar C5: a Bluetooth LE peripheral that notifies when a TTP223B touch pad changes."""

import bluetooth
from machine import Pin
from micropython import const
from time import sleep_ms


DEVICE_NAME = "PulsarC5-BLE"
SERVICE_UUID = bluetooth.UUID("4fafc201-1fb5-459e-8fcc-c5c9c331914b")
TOUCH_UUID = bluetooth.UUID("beb5483e-36e1-4688-b7f5-ea07361b26a8")
TOUCH_PIN = 5  # A3

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_FLAG_READ = const(0x0002)
_FLAG_NOTIFY = const(0x0010)


def field(kind, data):
    """One advertising field: length, type, data."""
    return bytes((len(data) + 1, kind)) + data


# The 128-bit service UUID fills most of the 31-byte advertisement, so the name goes in the scan response.
ADVERTISEMENT = field(0x01, b"\x06") + field(0x07, bytes(SERVICE_UUID))
SCAN_RESPONSE = field(0x09, DEVICE_NAME.encode())

touch = Pin(TOUCH_PIN, Pin.IN)
ble = bluetooth.BLE()
ble.active(True)
ble.config(gap_name=DEVICE_NAME)
((touch_handle,),) = ble.gatts_register_services(
    ((SERVICE_UUID, ((TOUCH_UUID, _FLAG_READ | _FLAG_NOTIFY),)),))
ble.gatts_write(touch_handle, b"released")
connections = set()


def advertise():
    ble.gap_advertise(100_000, adv_data=ADVERTISEMENT, resp_data=SCAN_RESPONSE)


def on_ble_event(event, data):
    if event == _IRQ_CENTRAL_CONNECT:
        connections.add(data[0])
        print("Phone connected")
    elif event == _IRQ_CENTRAL_DISCONNECT:
        connections.discard(data[0])
        print("Phone disconnected, advertising again")
        advertise()


ble.irq(on_ble_event)
advertise()
print("Advertising as", DEVICE_NAME)

last = False
try:
    while True:
        touched = touch.value() == 1
        if touched != last:  # notify on change only: less radio time
            last = touched
            state = b"touched" if touched else b"released"
            ble.gatts_write(touch_handle, state)
            for connection in connections:
                ble.gatts_notify(connection, touch_handle)
            print(state.decode())
        sleep_ms(20)
finally:
    # Leave the radio off after Stop, so running the script again starts clean.
    ble.active(False)
