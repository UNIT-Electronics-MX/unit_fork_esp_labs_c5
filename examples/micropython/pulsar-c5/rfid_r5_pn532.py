"""R5: write and verify a disposable test tag's user memory.

Set ARM_WRITE=True in the editor after reading the old bytes. This overwrites
Classic block 4 (16 bytes) or Type 2 page 8 (4 bytes). Never use a valued tag.
"""
from time import sleep_ms
from pn532 import hex_bytes, open_reader

ARM_WRITE = False
CLASSIC_KEY = b"\xff" * 6  # Replace with your card's sector 1 key if needed.
CLASSIC_KEY_TYPE = 0x60  # 0x60 = Key A; 0x61 = Key B.
CLASSIC_TEXT = b"PN532 C5 LAB R5!"  # Exactly 16 bytes.
TYPE2_TEXT = b"C5R5"             # Exactly 4 bytes.

reader = open_reader()
print("R5 ready. Present a disposable tag; ARM_WRITE =", ARM_WRITE)
tag = None
while tag is None:
    tag = reader.read_tag()
    sleep_ms(100)
print("UID", hex_bytes(tag["uid"]))
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
        print("No bytes changed. Check CLASSIC_KEY and CLASSIC_KEY_TYPE.")
    else:
        before = reader.read_classic_block(4)
        print("Classic block 4 before:", hex_bytes(before))
        if ARM_WRITE:
            reader.write_classic_block(4, CLASSIC_TEXT)
            after = reader.read_classic_block(4)
            print("After:", hex_bytes(after), "verified:", after == CLASSIC_TEXT)
elif tag["sak"] == 0 and tag["atqa"] in (b"\x44\x00", b"\x00\x44"):
    before = reader.read_type2_pages(8)[:4]
    print("Type 2 page 8 before:", hex_bytes(before))
    if ARM_WRITE:
        reader.write_type2_page(TYPE2_TEXT)
        after = reader.read_type2_pages(8)[:4]
        print("After:", hex_bytes(after), "verified:", after == TYPE2_TEXT)
else:
    print("No write: tag is neither Classic nor a supported Type 2 tag.")
if not ARM_WRITE:
    print("Read-only pass complete: ARM_WRITE is False, so no write was attempted.")
    print("For this disposable tag, set ARM_WRITE = True in the editor and run R5 again.")
