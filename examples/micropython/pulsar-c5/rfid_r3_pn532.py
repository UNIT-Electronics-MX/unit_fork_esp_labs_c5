"""R3: inventory different cards by presenting them one at a time.

The PN532 initializes one target per scan. This is a sequential inventory,
not an assertion that two overlapping cards can be resolved at once.
"""
from time import sleep_ms
from pn532 import hex_bytes, open_reader

reader = open_reader()
seen = {}
held = None
print("R3 ready. Tap cards one at a time. Ctrl+C prints the final list.")
try:
    while True:
        tag = reader.read_tag()
        uid = tag["uid"] if tag else None
        if uid is not None and uid != held:
            key = hex_bytes(uid)
            seen[key] = seen.get(key, 0) + 1
            print("{}: UID {}  SAK 0x{:02X}  seen {} time(s)".format(
                len(seen), key, tag["sak"], seen[key]))
        held = uid
        sleep_ms(150)
except KeyboardInterrupt:
    print("Unique cards:", len(seen))
    for uid, count in seen.items():
        print(uid, count)
