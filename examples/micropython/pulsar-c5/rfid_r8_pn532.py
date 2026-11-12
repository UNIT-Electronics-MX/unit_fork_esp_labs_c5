"""R8: give each card a saved character; tapping prints its scene cue.

Commands: tap, give NAME, list, forget UID, help. The UID-to-friend mapping
is stored on the board, so no tag memory write is needed.
"""
import json
from time import sleep_ms
from pn532 import hex_bytes, open_reader

STORE = "rfid_r8_friends.json"
FRIENDS = ("fox", "robot", "dragon", "penguin", "alien", "cat", "frog", "unicorn")


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
last = None
print("R8 card friends. Type help; Ctrl+C stops.")
while True:
    line = input("friends> ").strip().lower()
    action, _, value = line.partition(" ")
    value = value.strip()
    if action == "help":
        print("tap | give NAME | list | forget UID | quit")
        print("Friends:", ", ".join(FRIENDS))
    elif action in ("tap", "give"):
        if action == "give" and value not in FRIENDS:
            print("Choose:", ", ".join(FRIENDS))
            continue
        uid = tap(reader)
        if action == "give":
            cards[uid] = value
        elif uid not in cards:
            cards[uid] = FRIENDS[len(cards) % len(FRIENDS)]
        save(cards)
        print("FRIEND,{},{}".format(uid, cards[uid]))
        if last and last != uid:
            print("TOGETHER,{},{}".format(cards.get(last, "?"), cards[uid]))
        last = uid
    elif action == "list":
        for uid, friend in cards.items():
            print(uid, friend)
    elif action == "forget" and value.upper() in cards:
        del cards[value.upper()]
        save(cards)
        print("forgot", value.upper())
    elif action in ("quit", "exit"):
        break
    else:
        print("Unknown command. Type help.")
