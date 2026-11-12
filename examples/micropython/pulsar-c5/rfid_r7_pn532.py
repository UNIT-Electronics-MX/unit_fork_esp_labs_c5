"""R7: local UID allowlist for a simulated door, stored on the C5 filesystem.

Commands: enroll NAME, check, list, block UID, unblock UID, delete UID, help.
The printed decision is a classroom simulation. A UID is copyable and is not
an access credential for a real door.
"""
import json
from time import sleep_ms
from pn532 import hex_bytes, open_reader

STORE = "rfid_r7_cards.json"


def load():
    try:
        with open(STORE) as source:
            return json.load(source)
    except OSError:
        return {}


def save(cards):
    temp = STORE + ".tmp"
    with open(temp, "w") as output:
        json.dump(cards, output)
    import os
    os.rename(temp, STORE)


def tap(reader):
    print("Tap one card...")
    while True:
        tag = reader.read_tag()
        if tag:
            return hex_bytes(tag["uid"]).replace(" ", "")
        sleep_ms(100)


reader = open_reader()
cards = load()
print("R7 simulated door. Type help; Ctrl+C stops.")
while True:
    line = input("door> ").strip()
    action, _, value = line.partition(" ")
    value = value.strip()
    if action == "help":
        print("enroll NAME | check | list | block UID | unblock UID | delete UID | quit")
    elif action == "enroll" and value:
        uid = tap(reader)
        cards[uid] = {"name": value[:24], "blocked": False}
        save(cards)
        print("Enrolled", uid, cards[uid]["name"])
    elif action == "check":
        uid = tap(reader)
        card = cards.get(uid)
        print("GRANTED" if card and not card["blocked"] else "DENIED", uid,
              card["name"] if card else "unknown")
    elif action == "list":
        for uid, card in cards.items():
            print(uid, card["name"], "BLOCKED" if card["blocked"] else "allowed")
    elif action in ("block", "unblock") and value in cards:
        cards[value]["blocked"] = action == "block"
        save(cards)
        print(action, value)
    elif action == "delete" and value in cards:
        del cards[value]
        save(cards)
        print("deleted", value)
    elif action in ("quit", "exit"):
        break
    else:
        print("Unknown command or UID. Type help.")
