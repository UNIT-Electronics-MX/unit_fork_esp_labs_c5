"""R2: detect an ISO14443-A card and print its UID once per placement."""
from time import sleep_ms
from pn532 import hex_bytes, open_reader

reader = open_reader()
print("R2 ready. Place a card on the PN532. Ctrl+C stops.")
previous = None
misses = 0
while True:
    tag = reader.read_tag()
    if tag is None:
        misses += 1
        if misses >= 3 and previous is not None:
            print("Card removed")
            previous = None
    else:
        misses = 0
        if tag["uid"] != previous:
            previous = tag["uid"]
            print("UID: {}  ATQA: {}  SAK: 0x{:02X}".format(
                hex_bytes(previous), hex_bytes(tag["atqa"]), tag["sak"]))
    sleep_ms(100)
