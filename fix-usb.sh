#!/usr/bin/env bash
# Da acesso de escrita no USB ao mouse Redragon M711 (Holtek 04d9:fc30).
# Necessario porque o mouse_m908 fala com o mouse via libusb, que exige O_RDWR
# no /dev/bus/usb/BBB/DDD. Sem isso da "libusb_open -> -3 (Access denied)".
set -euo pipefail

RULE=/etc/udev/rules.d/71-redragon-usb.rules

if [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: rode com sudo ->  sudo bash $0" >&2
  exit 1
fi

cat > "$RULE" <<'EOF'
# Redragon M711 (Holtek 04d9:fc30) - escrita no device node USB para libusb/mouse_m908
SUBSYSTEM=="usb", ATTRS{idVendor}=="04d9", ATTRS{idProduct}=="fc30", MODE="0660", TAG+="uaccess"
EOF

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb
sleep 1

echo "--- permissao do node USB do mouse (queremos rw + '+'):"
# os diretorios em /sys/bus/usb/devices sao nomeados "<bus>-<porta>[.<intf>]",
# e cada interface tem o proprio devnum em hex. O device node de cada interface
# e /dev/bus/usb/<bus>/<devnum>.
for d in /sys/bus/usb/devices/*/idVendor; do
  [ "$(cat "$d")" = "04d9" ] || continue
  b=$(dirname "$d")
  bus=$(basename "$b" | cut -d- -f1)
  dev=$(printf "%03d" "0x$(cat "$b/devnum")")
  ls -l "/dev/bus/usb/$bus/$dev" 2>/dev/null
done
echo
echo "Fim."
