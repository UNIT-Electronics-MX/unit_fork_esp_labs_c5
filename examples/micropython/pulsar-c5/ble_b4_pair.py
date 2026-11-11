"""B4 Pair & trust: B3's Bench service plus a Vault only a paired phone may open.

Needs ble_kit.py saved on the board. Connect to "Bench B4" and read the Vault:
the phone asks to pair. Both sides show six digits; if they match, click BOOT
(or type yes). Long press (or no) refuses. The bond is kept in ble_bonds.json.
Type help in the serial console.
"""

from time import sleep_ms

import ble_kit as kit

# True: LED and Message need an encrypted link too. Change it and run the script again.
LOCK_CONTROLS = False

bonds = kit.BondStore()
radio = kit.Radio("Bench B4", bonds=bonds, io=kit.IO_DISPLAY_YESNO, mitm=True)
bench = kit.Bench(radio, vault=True, lock=LOCK_CONTROLS)
console = kit.Console()
button = kit.Button()
allow = "anyone"


def on_connect(data):
    # "allow bonded": ask for encryption at once. A bonded phone encrypts with its saved keys;
    # a new one would have to pair, and on_passkey refuses that.
    if allow == "bonded":
        radio.ble.gap_pair(data[0])


def on_passkey(_data):
    if allow == "bonded" and radio.pending_compare:
        print("A new phone wants to pair, but only bonded phones are allowed")
        radio.answer(False)


def on_encryption(data):
    connection, encrypted, _authenticated, bonded, _key_size = data
    if allow == "bonded" and not (encrypted and bonded):
        print("Not a bonded phone: hanging up")
        radio.ble.gap_disconnect(connection)


def run(text):
    global allow
    command, rest = kit.split_command(text)
    if command == "yes":
        radio.answer(True)
    elif command == "no":
        radio.answer(False)
    elif command == "secure":
        if not radio.connections:
            print("Connect a phone first")
        for connection in radio.connections:
            radio.ble.gap_pair(connection)
            print("Asked the phone to encrypt (it pairs if it has no keys)")
    elif command == "bonds":
        print("{} bonded device(s) in {}".format(bonds.count(), bonds.path))
    elif command == "forget":
        bonds.forget()
        print("Bonds deleted. Forget the board on the phone too, then pair again")
    elif command == "allow" and rest in ("bonded", "anyone"):
        allow = rest
        print("Allowing", allow)
    elif command == "vault" and rest:
        bench.set_vault(rest)
    elif command == "press":
        bench.press(kit.CLICK)
    elif command == "disconnect":
        radio.disconnect_all()
    else:
        print("yes | no | secure | bonds | forget | allow bonded|anyone | vault <text> | press | disconnect")


radio.on(kit.IRQ_CENTRAL_CONNECT, on_connect)
radio.on(kit.IRQ_PASSKEY_ACTION, on_passkey)
radio.on(kit.IRQ_ENCRYPTION_UPDATE, on_encryption)
print("B4 Pair & trust on", radio.address())
print("{} bonded device(s); LED and Message {}".format(
    bonds.count(), "need encryption" if LOCK_CONTROLS else "are open"))
bench.advertise()
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if radio.pending_compare and gesture in (kit.CLICK, kit.LONG):
            radio.answer(gesture == kit.CLICK)
        elif gesture:
            bench.press(gesture)
        bench.tick()
        radio.tick()
        sleep_ms(10)
finally:
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
