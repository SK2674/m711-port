#!/usr/bin/env python3
"""Teste minimo: um ciclo SET+GET de um frame de leitura, via hidraw.

O que falta validar: se o hidraw aceita buffer de 16 bytes pro report ID 2
(o driver C++ pede exatamente 16 com wValue 0x0302).
"""
import fcntl
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dados

VID, PID = "04d9", "fc30"
_IHDR = 0x48  # 'H'


def _ioc(direction, tipo, nr, size):
    return (direction << 30) | (tipo << 8) | nr | (size << 16)


def gfeature(n):
    return _ioc(2, _IHDR, 0x01, n)


def sfeature(n):
    return _ioc(1, _IHDR, 0x06, n)


def achar_node():
    """Acha o hidraw da interface de vendor do M711 (a 1.2).

    O M711 expoe tres interfaces: 1.0 e 1.1 sao boot (sem feature report,
    dai no EINVAL) e a 1.2 e' a de vendor, que e' a unica que aceita SET/GET.
    Os numeros de hidraw mudam a cada replug, entao resolvemos por sysfs.
    """
    for hid in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        uevent = (hid / "device/uevent").read_text()
        if f"HID_ID=0003:{int(VID, 16):08X}:{int(PID, 16):08X}" not in uevent:
            continue
        real = os.path.realpath(hid / "device")
        achados = re.findall(r":1\.(\d+)", real)
        if achados and achados[-1] == "2":
            return f"/dev/{hid.name}", achados[-1]
    return None, None


def main():
    node, iface = achar_node()
    if not node:
        sys.exit("M711 nao encontrado no /sys/class/hidraw")
    print(f"node: {node}   interface de vendor: {iface}")

    fd = os.open(node, os.O_RDWR)
    try:
        # frame 1 do buffer 1 -> devolve o perfil ativo em buffer_in1[0]
        pedido = bytearray(dados._C_DATA_READ_1[1])
        print(f"enviando SET: {' '.join(f'{b:02x}' for b in pedido)}")

        fcntl.ioctl(fd, sfeature(16), pedido)
        print("SET ok")

        for tamanho in (16, 17, 15, 32):
            buf = bytearray(tamanho)
            try:
                fcntl.ioctl(fd, gfeature(tamanho), buf)
                print(f"GET len={tamanho:2d} ok -> {' '.join(f'{b:02x}' for b in buf)}")
                break
            except OSError as e:
                print(f"GET len={tamanho:2d} falhou: {e}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
