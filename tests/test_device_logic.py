import pytest

from lapguard.sensors.device_logic import DeviceFilter, classify, physical_key

USB_STICK = [
    r"\\?\USB#VID_0781&PID_5581#4C530001#{a5dcbf10-6530-11d2-901f-00c04fb951ed}",
    r"\\?\USBSTOR#Disk&Ven_SanDisk&Prod_Ultra#4C530001&0#{53f56307-b6bf-11d0-94f2-00a0c91efb8b}",
    r"\\?\STORAGE#Volume#_??_USBSTOR#{53f5630d-b6bf-11d0-94f2-00a0c91efb8b}",
    "E:",
]


def armed_filter(**kw):
    f = DeviceFilter(**kw)
    f.arm(now=100.0)
    return f


def test_nothing_accepted_before_arming_or_during_settle():
    f = DeviceFilter()
    assert not f.accept("added", USB_STICK[0], 100.0)
    f.arm(100.0)
    assert not f.accept("added", USB_STICK[0], 100.5)  # settle period
    assert f.accept("added", USB_STICK[0], 101.0)


def test_burst_from_one_plug_collapses_to_one_event():
    f = armed_filter()
    results = [f.accept("added", dev, 105.0 + i * 0.1) for i, dev in enumerate(USB_STICK)]
    assert results == [True, False, False, False]


def test_new_plug_after_burst_window_is_accepted_again():
    f = armed_filter(burst_window_s=2.0)
    assert f.accept("added", USB_STICK[0], 105.0)
    assert not f.accept("removed", USB_STICK[0], 106.9)
    assert f.accept("removed", USB_STICK[0], 107.0)


@pytest.mark.parametrize("action,on_arrival,on_removal,expected", [
    ("added", True, False, True),
    ("added", False, True, False),
    ("removed", True, False, False),
    ("removed", False, True, True),
    ("audio_connected", False, True, False),       # arrival-type audio event
    ("default_audio_changed", True, False, True),
    ("audio_disconnected", False, True, True),     # removal-type audio event
])
def test_direction_toggles(action, on_arrival, on_removal, expected):
    f = armed_filter(on_arrival=on_arrival, on_removal=on_removal)
    assert f.accept(action, USB_STICK[0], 105.0) is expected


def test_ignore_list_is_case_insensitive_substring():
    f = armed_filter(ignore=("vid_0781&pid_5581", ""))
    assert not f.accept("added", USB_STICK[0], 105.0)
    assert f.accept("added", r"\\?\USB#VID_046D&PID_C52B#x#{a5dcbf10-6530-11d2-901f-00c04fb951ed}", 105.0)


def test_disarm_resets():
    f = armed_filter()
    assert f.accept("added", USB_STICK[0], 105.0)
    f.disarm()
    assert not f.accept("added", USB_STICK[0], 110.0)


def test_physical_key_and_classify():
    assert physical_key(USB_STICK[0]) == "usb:0781:5581"
    assert physical_key(r"\\?\HID#VID_046D&PID_C52B&MI_00#7&1#{4d1e55b2-f16f-11cf-88cb-001111000030}") == "usb:046D:C52B"
    assert physical_key(USB_STICK[2]) == r"STORAGE#VOLUME#_??_USBSTOR"
    assert classify("{A5DCBF10-6530-11D2-901F-00C04FB951ED}") == "usb"
    assert classify("e6f07b5f-ee97-4a90-b076-33f57bf4eaa7") == "monitor"
    assert classify("00000000-0000-0000-0000-000000000000") == "other"
