"""B3 Connect & talk: the Pulsar C5 as a GATT server your phone talks to.

Needs ble_kit.py saved on the board. Connect from nRF Connect or LightBlue to
"Bench B3": write 3 bytes to LED (FF 00 00 = red), write text to Message,
subscribe to Button and Temperature. Type help in the serial console.
BOOT: every click, double-click and long press is sent as a Button notification.
"""

from time import sleep_ms

import ble_kit as kit

radio = kit.Radio("Bench B3")
bench = kit.Bench(radio)
console = kit.Console()
button = kit.Button()


def run(text):
    command, rest = kit.split_command(text)
    if command == "adv" and rest in ("on", "off"):
        if rest == "on":
            bench.advertise()
        else:
            radio.stop_advertising()
            print("Advertising off: invisible to phones")
    elif command == "message" and rest:
        bench.set_message(rest)
    elif command == "press":
        bench.press(kit.CLICK)
    elif command == "disconnect":
        if radio.connections:
            radio.disconnect_all()
        else:
            print("Nobody is connected")
    elif command == "led" and rest == "auto":
        radio.led.claimed = None
        print("The LED shows the radio again")
    else:
        print("adv on|off | message <text> | press | disconnect | led auto")


print("B3 Connect & talk on", radio.address())
bench.advertise()
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture:
            bench.press(gesture)
        bench.tick()
        radio.tick()
        sleep_ms(10)
finally:
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
