#!/usr/bin/env python3
"""Prova de conceito: ler a tabela de DPI do M711 via hidraw, sem libusb.

Cada frame do protocolo tem o report ID como primeiro byte, que e
exatamente o que o ioctl HIDIOCGFEATURE espera. A interface de vendor
do M711 e a 1.2 (subclass 0), a mesma que o upstream acessa com
wIndex=0x0002 nos control transfers.
"""
import ctypes
import fcntl
import os
import re
import sys

VID, PID = "04d9", "fc30"

# _IOC(dir,type,nr,size) = (dir<<30) | (type<<8) | nr | (size<<16)
# HIDIOCGFEATURE(len) = _IOR('H', 0x01, len)  -> dir = _IOC_READ  = 2
# HIDIOCSFEATURE(len) = _IOW('H', 0x06, len)  -> dir = _IOC_WRITE = 1
_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT, _IOC_TYPESHIFT, _IOC_SIZESHIFT = 0, 8, 16
_IOC_DIRSHIFT = 30
_IOC_READ, _IOC_WRITE = 2, 1


def _ioc(direction, typ, nr, size):
    return ((direction << _IOC_DIRSHIFT)
            | (ord(typ) << _IOC_TYPESHIFT)
            | (nr << _IOC_NRSHIFT)
            | (size << _IOC_SIZESHIFT))


HIDIOCGFEATURE = lambda n: _ioc(_IOC_READ, "H", 0x01, n)
HIDIOCSFEATURE = lambda n: _ioc(_IOC_WRITE, "H", 0x06, n)


def nosso_hidraw():
    """Acha o /dev/hidraw da interface de vendor (1.2) do M711."""
    for n in sorted(os.listdir("/sys/class/hidraw")):
        p = f"/sys/class/hidraw/{n}/device"
        try:
            uevent = open(f"{p}/uevent").read()
        except OSError:
            continue
        if f"HID_ID=0003:0000{VID.upper()}:0000{PID.upper()}" not in uevent:
            continue
        # o caminho do sysfs da interface diz qual eh (ex.: 2-2:1.2)
        real = os.path.realpath(p)
        m = re.search(r"(\S+):1\.(\d+)", real)
        iface = m.group(2) if m else "?"
        print(f"  {n}: interface 1.{iface}  ({real.split('/')[-2]})")
        yield n, iface


def ler(fd, report_id, tamanho):
    buf = bytearray(tamanho)
    buf[0] = report_id
    fcntl.ioctl(fd, HIDIOCGFEATURE(tamanho), buf, True)
    return bytes(buf)


def main():
    print("Procurando o M711...")
    cands = list(nosso_hidraw())
    if not cands:
        print("M711 nao encontrado")
        return 1

    # a interface de vendor e a 1.2
    alvo = next((n for n, i in cands if i == "2"), cands[0][0])
    print(f"\nUsando hidraw{alvo} (interface de vendor)\n")

    fd = os.open(os.path.join("/dev", alvo), os.O_RDWR)
    try:
        # o dump cru do M711 le a tabela de DPI com um report de 40 bytes,
        # report ID 0x03, um perfil por leitura (indice em [8:10])
        for perfil in range(5):
            buf = bytearray(40)
            buf[0] = 0x03
            try:
                fcntl.ioctl(fd, HIDIOCGFEATURE(40), buf, True)
            except OSError as e:
                print(f"  perfil {perfil+1}: falha no ioctl -> {e}")
                return 1
            if buf[0] != 0x03 or buf[1] != 0x08:
                print(f"  perfil {perfil+1}: resposta inesperada "
                      f"{buf[0]:02x} {buf[1]:02x} (esperado 03 08)")
                continue
            idx = buf[8] | (buf[9] << 8)
            dpi = []
            for j in range(5):
                b = 10 + 6 * j
                x = buf[b + 1] | (buf[b + 2] << 8)
                y = buf[b + 3] | (buf[b + 4] << 8)
                dpi.append(f"{x}" + (f"/{y}" if x != y else ""))
            print(f"  perfil {idx+1}: codigos = {', '.join(dpi)}"
                  f"   -> {', '.join(str(int(c.split('/')[0]) * 44.4444) for c in dpi)} DPI")
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
