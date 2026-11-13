"""ES8311 codec on Pulsar C5: send microphone PCM over USB for a WAV file.

MicroPython version of the Arduino example es8311_mic_serial_pcm.ino from
unit_devlab_codec_library. Save libraries/es8311.py on the board as es8311.py
and save this script as main.py. Wiring is the same as es8311_mic_capture.py.

The packets match the codec library's receivers. Close the web console first:

  python3 extras/serial_pcm_to_wav.py /dev/ttyACM0 microphone.wav --seconds 10
  python3 extras/serial_pcm_web.py /dev/ttyACM0

Packet: "DLM1" + uint32 sequence + uint16 frames (256) + uint16 CRC16-CCITT
of the payload, then 256 little-endian PCM16 samples. Nothing else is printed
while streaming. USB Serial/JTAG carries the 33 KB/s of 16 kHz PCM.
"""

import array
import struct
import sys

import es8311

SDA_PIN = 1         # QWIIC SDA
SCL_PIN = 6         # QWIIC SCL
BCLK_PIN = 27       # J20 SCK
WS_PIN = 26        # J20 WS
DOUT_PIN = 7       # J20 SD: ES8311 DOUT -> Pulsar C5 I2S input
MCLK_PIN = -1       # no MCLK wire: the codec clocks from BCLK

SAMPLE_RATE = 16000
INPUT_GAIN = 0.6    # 24 dB
FRAMES_PER_PACKET = 256

_CRC_SRC = """
@micropython.viper
def crc16(buf: ptr8, length: int) -> int:
    crc = 0xFFFF
    for i in range(length):
        crc ^= buf[i] << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
"""


def _crc16_table():
    table = array.array("H", bytes(512))
    for n in range(256):
        crc = n << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table[n] = crc
    return table


try:
    _ns = {}
    exec(_CRC_SRC, _ns)
    _crc_viper = _ns["crc16"]

    def crc16(samples):
        return _crc_viper(samples, len(samples) * 2)  # ptr8 sees raw bytes
except Exception:  # no native emitter: table-driven fallback
    _TABLE = _crc16_table()

    def crc16(samples):
        crc = 0xFFFF
        for byte in bytes(samples):
            crc = ((crc << 8) & 0xFFFF) ^ _TABLE[(crc >> 8) ^ byte]
        return crc


def main():
    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_in=DOUT_PIN)
    audio.begin_rx(rate=SAMPLE_RATE, gain=INPUT_GAIN)

    out = sys.stdout.buffer  # raw bytes: no "\n" -> "\r\n" translation
    pcm = array.array("h", bytes(FRAMES_PER_PACKET * 2))
    chunk = array.array("h", bytes(FRAMES_PER_PACKET * 2))
    header = bytearray(b"DLM1" + bytes(8))
    sequence = 0
    filled = 0
    try:
        while True:
            if filled == 0:
                filled = audio.read_mono(pcm)
            else:
                got = audio.read_mono(chunk, FRAMES_PER_PACKET - filled)
                pcm[filled:filled + got] = chunk[:got]
                filled += got
            if filled < FRAMES_PER_PACKET:
                continue
            struct.pack_into("<IHH", header, 4, sequence & 0xFFFFFFFF,
                             FRAMES_PER_PACKET, crc16(pcm))
            out.write(header)
            out.write(pcm)
            sequence += 1
            filled = 0
    finally:
        audio.end()


main()
