"""R4: read user data from a test MIFARE Classic or Type 2 tag.

Classic starts with factory Key A (FF FF FF FF FF FF). Use a tag you own.
"""
from time import sleep_ms
from pn532 import hex_bytes, open_reader

CLASSIC_KEY = b"\xff" * 6  # Replace with your card's sector 1 key if needed.
CLASSIC_KEY_TYPE = 0x60  # 0x60 = Key A; 0x61 = Key B.

reader = open_reader()
print("R4 ready. Present one test tag for a read.")
tag = None
while tag is None:
    tag = reader.read_tag()
    sleep_ms(100)
print("UID", hex_bytes(tag["uid"]), "SAK 0x{:02X}".format(tag["sak"]))
if tag["sak"] in (0x08, 0x09, 0x18):
    if len(CLASSIC_KEY) != 6 or CLASSIC_KEY_TYPE not in (0x60, 0x61):
        raise ValueError("Use a six-byte Classic Key A (0x60) or Key B (0x61)")
    try:
        # InDataExchange works with both the original and updated pn532.py.
        _ = reader.exchange(bytes((CLASSIC_KEY_TYPE, 4)) + CLASSIC_KEY + tag["uid"][-4:])
    except OSError as error:
        if "status 0x14" not in str(error):
            raise
        print("PN532 0x14: MIFARE Classic authentication failed for sector 1.")
        print("Check this card's sector 1 key; edit CLASSIC_KEY and CLASSIC_KEY_TYPE.")
        print("Keep the card still on the reader, or try a fresh factory-key test tag.")
    else:
        data = reader.read_classic_block(4)
        print("Classic data block 4:", hex_bytes(data))
elif tag["sak"] == 0 and tag["atqa"] in (b"\x44\x00", b"\x00\x44"):
    data = reader.read_type2_pages(8)
    print("Type 2 pages 8-11:", hex_bytes(data))
else:
    print("This tag type needs a different memory protocol; UID reading still works.")
