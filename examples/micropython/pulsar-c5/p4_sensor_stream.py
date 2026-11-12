"""Pulsar C5 P4: stream all five onboard sensors at 10 Hz to the web drone viewer (or Processing).

Install fht40.py, lps22hb.py, vcnl4040.py, max1704x.py and mmc5603.py first.
The sensor bus is GPIO2 SDA / GPIO3 SCL. Stop with Ctrl-C.
P4,seq,mask,temp,rh,pressure,pressureTemp,lux,proximity,volts,soc,mx,my,mz
A '-' means unavailable; mask bits mark successful readings for each sensor.
"""

from machine import I2C, Pin
from time import sleep_ms, ticks_diff, ticks_ms


i2c = I2C(0, sda=Pin(2), scl=Pin(3), freq=100000)
addresses = set(i2c.scan())
print("P4 sensor bus: " + ", ".join("0x{:02X}".format(a) for a in sorted(addresses)))

fht = lps = vcnl = battery = magnetic = None

if 0x44 in addresses:
    try:
        from fht40 import FHT40
        fht = FHT40(i2c)
    except Exception as error:
        print("P4_WARN,FHT40: " + repr(error))
else:
    print("P4_WARN,FHT40 0x44 absent")

if 0x5C in addresses:
    try:
        from lps22hb import LPS22HB
        lps = LPS22HB(i2c)
    except Exception as error:
        print("P4_WARN,LPS22HB: " + repr(error))
else:
    print("P4_WARN,LPS22HB 0x5C absent")

if 0x60 in addresses:
    try:
        from vcnl4040 import VCNL4040
        vcnl = VCNL4040(i2c)
        vcnl.light_integration_time = VCNL4040.ALS_160MS
    except Exception as error:
        print("P4_WARN,VCNL4040: " + repr(error))
else:
    print("P4_WARN,VCNL4040 0x60 absent")

if 0x36 in addresses:
    try:
        from max1704x import max1704x
        battery = max1704x(i2c, is_max17048=True)
        # Above 100 % the gauge has not learnt the cell (or no LiPo is connected and it reads the
        # charger): restart its estimate from the present voltage. The web viewer then trusts the
        # voltage while the gauge still reads above 100 %.
        if battery.getSoc() > 101:
            print("P4_WARN,MAX17048 read {:.0f} %: quick start".format(battery.getSoc()))
            battery.quickStart()
            sleep_ms(500)
    except Exception as error:
        print("P4_WARN,MAX17048: " + repr(error))
else:
    print("P4_WARN,MAX17048 0x36 absent")

if 0x30 in addresses:
    try:
        from mmc5603 import MMC5603, I2cAdapter
        magnetic = MMC5603(I2cAdapter(i2c))
        magnetic.set_update_rate(20)
        magnetic.start_measure(continuous_mode=True, auto_set_reset=True)
    except Exception as error:
        print("P4_WARN,MMC5603: " + repr(error))
else:
    print("P4_WARN,MMC5603 0x30 absent")

values = ["-"] * 11
mask = 0
seq = 0
last_env = last_pressure = last_battery = last_magnetic = ticks_ms()
first = True
last_warning = {}


def warn(name, error):
    now = ticks_ms()
    previous = last_warning.get(name)
    if previous is None or ticks_diff(now, previous) >= 5000:
        print("P4_WARN,{} read: {}".format(name, repr(error)))
        last_warning[name] = now


while True:
    now = ticks_ms()
    if first or ticks_diff(now, last_env) >= 500:
        last_env = now
        if fht is not None:
            try:
                fht.measure()
                values[0] = "{:.2f}".format(fht.temperature)
                values[1] = "{:.2f}".format(fht.humidity)
                mask |= 1
            except Exception as error:
                warn("FHT40", error)
                values[0] = values[1] = "-"
                mask &= ~1
        if vcnl is not None:
            try:
                values[4] = "{:.2f}".format(vcnl.lux)
                values[5] = str(vcnl.proximity)
                mask |= 4
            except Exception as error:
                warn("VCNL4040", error)
                values[4] = values[5] = "-"
                mask &= ~4

    if first or ticks_diff(now, last_pressure) >= 1000:
        last_pressure = now
        if lps is not None:
            try:
                values[2] = "{:.2f}".format(lps.pressure)
                values[3] = "{:.2f}".format(lps.temperature)
                mask |= 2
            except Exception as error:
                warn("LPS22HB", error)
                values[2] = values[3] = "-"
                mask &= ~2

    if first or ticks_diff(now, last_battery) >= 2000:
        last_battery = now
        if battery is not None:
            try:
                values[6] = "{:.3f}".format(battery.getVCell())
                values[7] = "{:.1f}".format(battery.getSoc())
                mask |= 8
            except Exception as error:
                warn("MAX17048", error)
                values[6] = values[7] = "-"
                mask &= ~8

    if magnetic is not None:
        try:
            if magnetic.is_data_ready():
                mx, my, mz = magnetic.get_axis(-1)
                values[8] = str(mx)
                values[9] = str(my)
                values[10] = str(mz)
                mask |= 16
                last_magnetic = now
        except Exception as error:
            warn("MMC5603", error)
            values[8] = values[9] = values[10] = "-"
            mask &= ~16
        if ticks_diff(now, last_magnetic) > 1000:
            warn("MMC5603", "no fresh sample for 1 s")
            values[8] = values[9] = values[10] = "-"
            mask &= ~16

    print("P4,{},{},{}".format(seq, mask, ",".join(values)))
    seq += 1
    first = False
    elapsed = ticks_diff(ticks_ms(), now)
    sleep_ms(max(1, 100 - elapsed))
