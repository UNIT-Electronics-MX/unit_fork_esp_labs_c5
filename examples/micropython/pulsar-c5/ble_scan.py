"""Pulsar C5: scan Bluetooth LE advertisements for ten seconds."""

import bluetooth
from time import sleep_ms


IRQ_SCAN_RESULT = 5
IRQ_SCAN_DONE = 6
ble = bluetooth.BLE()
ble.active(True)


def name_from_advertisement(data):
    index = 0
    while index < len(data):
        length = data[index]
        if length == 0 or index + length >= len(data):
            break
        field_type = data[index + 1]
        if field_type in (0x08, 0x09):
            return bytes(data[index + 2:index + 1 + length]).decode("utf-8", "ignore")
        index += length + 1
    return "unnamed"


def on_ble_event(event, data):
    if event == IRQ_SCAN_RESULT:
        _address_type, address, _advertisement_type, rssi, payload = data
        mac = ":".join("{:02X}".format(byte) for byte in bytes(address))
        print("{}  {} dBm  {}".format(mac, rssi, name_from_advertisement(payload)))
    elif event == IRQ_SCAN_DONE:
        print("Scan complete")


ble.irq(on_ble_event)
print("Scanning for BLE devices...")
ble.gap_scan(10_000, 30_000, 30_000, True)
sleep_ms(11_000)
ble.gap_scan(None)
