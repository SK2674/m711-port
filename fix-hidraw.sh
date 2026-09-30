#!/usr/bin/env bash
# Libera acesso do usuariologado aos dispositivos hidraw do mouse Redragon M711
# (Holtek 04d9:fc30) para que o software da Redragon dentro do Wine consiga falar com o mouse.
set -euo pipefail

RULE=/etc/udev/rules.d/70-redragon-hidraw.rules

if [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: rode com sudo ->  sudo bash $0" >&2
  exit 1
fi

cat > "$RULE" <<'EOF'
# Redragon M711 (Holtek 04d9:fc30) - acesso ao hidraw para o software da Redragon via Wine
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="04d9", ATTRS{idProduct}=="fc30", TAG+="uaccess"
EOF

udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw
sleep 1

echo "--- Permissoes apos a regra:"
ls -l /dev/hidraw* | grep -i "$(echo 04d9)" || true
ls -l /dev/hidraw*
echo
echo "OK. Se o mouse continuar sem 'rw' + '+', tire e conecte o mouse de novo (usb)."
