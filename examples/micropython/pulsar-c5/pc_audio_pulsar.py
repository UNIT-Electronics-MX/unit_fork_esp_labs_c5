#!/usr/bin/env python3
"""Play audio from mic_ics43434_usb_master.py on the computer.

Install:  pip install pyserial sounddevice
Usage:    python3 pc_audio_pulsar.py /dev/ttyACM0
          python3 pc_audio_pulsar.py /dev/ttyACM0 --device esp_mic

Close any REPL or serial monitor before opening the port with this script.

On Linux with PulseAudio/PipeWire, a virtual microphone can be created with:
    pactl load-module module-null-sink sink_name=esp_mic
    python3 pc_audio_pulsar.py /dev/ttyACM0 --device esp_mic
Then select "Monitor of esp_mic" as the recording input in your application.
"""

import argparse
import threading
import time

import serial
import sounddevice as sd

SAMPLE_RATE = 48000  # must match mic_ics43434_usb_master.py
BAUD_RATE = 921600    # ignored by the native USB Serial/JTAG port
HEADER = b"RAW\r\n"
FRAME_MAGIC = b"AU"
MAX_FRAME_SAMPLES = 512  # FRAME_SAMPLES in mic_ics43434_usb_master.py
CHUNK_BYTES = 1024
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 // 5  # 200 ms; older audio is dropped to keep latency low
STALL_SECONDS = 1  # re-request the stream after this long without data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="native USB Serial/JTAG port, e.g. /dev/ttyACM0")
    parser.add_argument("--device", help="output device name or index (see: python3 -m sounddevice)")
    args = parser.parse_args()

    with serial.Serial(args.port, BAUD_RATE, timeout=0.1, write_timeout=1) as ser:
        try:
            wait_for_header(ser)
            stream(ser, args.device)
        finally:
            ser.write(b"S")


def wait_for_header(ser):
    ser.reset_input_buffer()
    window = b""
    deadline = time.monotonic() + 5
    last_request = 0
    while not window.endswith(HEADER):
        now = time.monotonic()
        if now > deadline:
            raise SystemExit(
                "No 'RAW' header received. Is mic_ics43434_usb_master.py "
                "running as main.py on the Pulsar C5?"
            )
        if now - last_request >= 0.5:
            ser.write(b"R")
            last_request = now
        window = (window + ser.read(1))[-len(HEADER):]


def extract_frames(buf):
    """Remove complete frames from buf; return (audio bytes, resync count).

    A frame whose checksum does not match lost bytes on the way; it is
    discarded and the search restarts at the next header.
    """
    audio = bytearray()
    resyncs = 0
    while True:
        start = buf.find(FRAME_MAGIC)
        if start < 0:
            del buf[:-1]  # keep a trailing 'A' that may start the next header
            return audio, resyncs
        if start:
            del buf[:start]
        if len(buf) < 4:
            return audio, resyncs
        count = int.from_bytes(buf[2:4], "little")
        end = 4 + 2 * count
        if not 0 < count <= MAX_FRAME_SAMPLES:
            del buf[:2]
            resyncs += 1
            continue
        if len(buf) < end + 2:
            return audio, resyncs
        samples = memoryview(buf)[4:end]
        checksum = sum(samples.cast("H")) & 0xFFFF
        samples.release()
        if checksum != int.from_bytes(buf[end:end + 2], "little"):
            del buf[:2]
            resyncs += 1
            continue
        audio += buf[4:end]
        del buf[:end + 2]


def stream(ser, device):
    # The serial reader and the audio callback run independently, so playback
    # never stalls the port. pending always holds a whole number of samples.
    pending = bytearray()
    lock = threading.Lock()

    def play(outdata, frames, time_info, status):
        with lock:
            n = min(len(outdata), len(pending))
            outdata[:n] = pending[:n]
            del pending[:n]
        outdata[n:] = b"\x00" * (len(outdata) - n)

    with sd.RawOutputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device, callback=play
    ):
        print("Streaming... Ctrl+C to stop")
        buf = bytearray()
        last_data = time.monotonic()
        while True:
            chunk = ser.read(CHUNK_BYTES)
            if chunk:
                last_data = time.monotonic()
            elif time.monotonic() - last_data > STALL_SECONDS:
                # Last resort if the board stops sending: answering a new
                # request makes it write and flush again.
                print("Stream stalled, requesting it again...")
                wait_for_header(ser)
                buf.clear()
                last_data = time.monotonic()
                continue
            buf += chunk
            audio, resyncs = extract_frames(buf)
            if resyncs:
                print(f"Lost bytes on USB, resynced ({resyncs})")
            if audio:
                with lock:
                    pending += audio
                    excess = len(pending) - MAX_BUFFER_BYTES
                    if excess > 0:
                        del pending[:excess]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
