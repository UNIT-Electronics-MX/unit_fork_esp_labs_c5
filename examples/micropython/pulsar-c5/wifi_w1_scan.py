"""W1 Scan & join: the Pulsar C5 lists every Wi-Fi network around it, 2.4 and 5 GHz, then joins yours.

Needs wifi_kit.py saved on the board. Type help in the serial console.
Pick your network from the scan table with join <#> and type the password on the next line
(or join <#> <password>, or join <network name> for one the table lacks):
it is saved in NVS on the chip, never in this script. Once joined: internet time, bench.local
and a live signal chart. The saved network is shared with W2 and W3.

Views: networks | channels | status | signal
BOOT: click = next view, double-click = scan now, long press = auto-scan on/off
The LED: blue while scanning, amber blink while joining, green when joined, red after a failure.
"""

import wifi_kit as kit

if getattr(kit, "VERSION", 1) < 3:
    raise SystemExit("wifi_kit.py on the board is too old: press Install wifi_kit.py, then run"
                     " import machine; machine.soft_reset() and start this script again.")

FORGET_SAVED = False  # True: erase the saved network when the script starts, then set it back

VIEWS = ("networks", "channels", "status", "signal")

if FORGET_SAVED:
    kit.forget_saved()

connection = kit.Connection()
console = kit.Console(mask=kit.hide_join_password)
led = kit.StatusLed()
lab = kit.ScanLab(connection, console, led)
button = kit.Button()
SHOW = (lab.show_networks, lab.show_channels, lab.show_status, lab.show_signal)
view = 0


def set_view(number):
    global view
    view = number % len(VIEWS)
    print("view {}: {}".format(view + 1, VIEWS[view]))
    SHOW[view]()


def show_help():
    print()
    print("Commands: scan | view <1-4> | networks | channels | status | signal | auto on|off")
    print("          join <#> [password] | join <network name> | forget | reconnect")
    print("          tz <+1 | -6 | 5:30> | reach [ip] [port] | stop | help")
    print("BOOT: click = next view, double-click = scan now, long press = auto-scan on/off")


def handle(text):
    if lab.take_password(text):
        return
    command, argument = kit.split_command(text)
    if command in VIEWS:
        set_view(VIEWS.index(command))
    elif command == "view" and argument.isdigit() and 1 <= int(argument) <= len(VIEWS):
        set_view(int(argument) - 1)
    elif command in ("stop", "exit", "quit"):
        raise SystemExit  # leaves the loop through finally: LED off, back to the >>> prompt
    elif command in ("help", "?"):
        show_help()
    elif not lab.handle(command, argument):
        print("unknown command '{}' (type help)".format(text))


print("wifi_w1_scan: MAC {}. Radio: 2.4 and 5 GHz.".format(kit.mac(connection.wlan.config("mac"))))
show_help()
if connection.state != kit.CONNECTING:
    lab.scan_now("start-up")

try:
    while True:
        line = console.line()
        if line is not None:
            handle(line)
        gesture = button.poll()
        if gesture == kit.CLICK:
            set_view(view + 1)
        elif gesture == kit.DOUBLE:
            lab.scan_now("BOOT")
        elif gesture == kit.LONG:
            lab.set_auto(not lab.auto_scan)

        if lab.report(connection.tick()):
            set_view(VIEWS.index("status"))
        if lab.auto_scan_due():
            lab.scan_now("auto", quiet=True)
        led.wifi(connection.state)
        kit.pause()
finally:
    led.off()
    print("Stopped. Wi-Fi stays joined, so you can try it in the REPL; W2 and W3 use the same saved network.")
