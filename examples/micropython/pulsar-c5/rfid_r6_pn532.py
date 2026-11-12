"""R6: measure the PN532's actual read rate and field edge.

Hold one tag at a measured distance during each ten-sample group. There is
no RSSI in these ISO14443-A results, so hits/10 is the observable metric.
"""
from time import sleep_ms, ticks_diff, ticks_ms
from pn532 import open_reader

reader = open_reader()
print("R6 ready. Move one tag: on antenna, then 1 cm, 2 cm, edge.")
for group in range(4):
    print("Position {}: keep the tag still for 10 scans".format(group + 1))
    sleep_ms(1500)
    hits = 0
    elapsed = 0
    for _ in range(10):
        start = ticks_ms()
        if reader.read_tag() is not None:
            hits += 1
        elapsed += ticks_diff(ticks_ms(), start)
        sleep_ms(100)
    print("Position {}: {}/10 hits, {} ms/scanning".format(group + 1, hits, elapsed // 10))
