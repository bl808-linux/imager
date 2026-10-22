# bl808-linux-imager

Build the images with `uv run imager`.

For a new board, connect the UART USB port, enter ROM Flash Mode (BOOT + RST), and install M0 once:

```sh
python3 -m venv /tmp/bl-mcu-tool
/tmp/bl-mcu-tool/bin/pip install bflb-mcu-tool
/tmp/bl-mcu-tool/bin/bflb-mcu-tool --chipname bl808 --port /dev/ttyUSB1 \
  --baudrate 2000000 --firmware out/firmware/m0/m0.bin --addr 0x58000000
```

Press RST without BOOT after flashing.

Install USB access once:

```sh
sudo apt install dfu-util
sudo install -m 644 udev/70-bl808-m0.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=1209 --attr-match=idProduct=0001
```

For updates, keep a USB cable connected to the board's **OTG port** and run without pressing buttons.

