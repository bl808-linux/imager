"""Linux usbfs control requests for the M0 DFU sector selector."""

import ctypes
import os
from pathlib import Path
import struct


class Control(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_uint8), ("request", ctypes.c_uint8),
                ("value", ctypes.c_uint16), ("index", ctypes.c_uint16),
                ("length", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
LIBC.ioctl.restype = ctypes.c_int
CONTROL = (3 << 30) | (ctypes.sizeof(Control) << 16) | (ord("U") << 8)


class Device:
    def __init__(self):
        devices = []
        for entry in Path("/sys/bus/usb/devices").iterdir():
            try:
                if ((entry / "idVendor").read_text().strip(),
                    (entry / "idProduct").read_text().strip(),
                    (entry / "product").read_text().strip()) == ("1209", "0001", "BL808 M0 DFU"):
                    devices.append(entry)
            except FileNotFoundError:
                continue
        if len(devices) != 1:
            raise RuntimeError(f"Expected exactly one BL808 M0 DFU device, found {len(devices)}")
        entry = devices[0]
        bus, number = int((entry / "busnum").read_text()), int((entry / "devnum").read_text())
        self.node = Path(f"/dev/bus/usb/{bus:03}/{number:03}")
        self.path = entry.name
        self.protocol()  # Check before any target/maintenance request.

    def protocol(self):
        data = self.control(0xc0, 1, 16)
        if len(data) != 16 or data[:6] != b"M0US\x02\x00":
            raise RuntimeError("M0 storage protocol 2 required; older firmware has different DFU targets")
        return data

    def enter(self, target):
        if target not in (0, 1):
            raise ValueError("Unknown storage target")
        self.control(0x40, 2, value=target)
        if not self.protocol()[7]:
            raise RuntimeError("D0 maintenance hold was not confirmed")

    def recover(self):
        state = self.control(0xa1, 5, 1)
        self.control(0x21, 4 if state == b"\x0a" else 6)

    def sd_info(self):
        self.enter(1)
        data = self.control(0xc0, 7, 48)
        if len(data) != 48 or data[:6] != b"M0SI\x02\x00":
            raise RuntimeError("Invalid SD card information")
        block_size, blocks = struct.unpack_from("<HQ", data, 6)
        if block_size != 512 or not 0 < blocks <= 2**32:
            raise RuntimeError("Unsupported SD geometry")
        return {"block_size": block_size, "blocks": blocks,
                "cid": data[16:32].hex(), "csd": data[32:].hex()}

    def select_sd(self, lba, blocks):
        if not 0 <= lba < 2**32 or not 1 <= blocks <= 8 or lba + blocks > 2**32:
            raise ValueError("Invalid SD transfer range")
        self.enter(1)
        self.control(0x40, 0x0f + blocks, value=lba & 0xffff, index=lba >> 16)
        data = self.control(0xc0, 6, 16)
        if data != struct.pack("<4sHHII", b"M0SD", 2, blocks, lba, blocks * 512):
            raise RuntimeError("SD selection differs from requested range")

    def control(self, kind, request, length=0, value=0, index=0, payload=None):
        if payload is not None:
            if kind & 0x80 or length:
                raise ValueError("OUT payload supplies its own length")
            length = len(payload)
        buffer = ctypes.create_string_buffer(payload, max(length, 1)) if payload is not None else ctypes.create_string_buffer(max(length, 1))
        transfer = Control(kind, request, value, index, length, 30000 if request == 2 and kind == 0x40 else 5000,
                           ctypes.addressof(buffer) if length else None)
        fd = os.open(self.node, os.O_RDWR | os.O_CLOEXEC)
        try:
            size = LIBC.ioctl(fd, CONTROL, ctypes.byref(transfer))
            if size < 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            return buffer.raw[:size]
        finally:
            os.close(fd)

    def selection(self):
        data = self.control(0xc0, 4, 16)
        if len(data) != 16:
            raise RuntimeError("Short M0 slot response")
        magic, version, slot, index, address = struct.unpack("<4sHHII", data)
        if magic != b"M0SL" or version != 2:
            raise RuntimeError("Unsupported M0 slot protocol")
        return slot, index, address

    def select(self, slot, index, address):
        self.enter(0)
        self.control(0x40, 4, value=slot, index=index)
        if self.selection() != (slot, index, address):
            raise RuntimeError("M0 slot mapping differs from the host layout")

    def command(self):
        return ["dfu-util", "-d", "1209:0001", "-p", self.path]

    def m0_writable(self, enabled):
        self.control(0x40, 5, value=0x4d30 if enabled else 0, index=0x5550 if enabled else 0)
        if self.control(0xc0, 5, 1) != bytes([enabled]):
            raise RuntimeError("M0 experimental write permission was not acknowledged")
