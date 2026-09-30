#!/usr/bin/env python3
"""Parseia o report descriptor do M711 e mostra o tamanho declarado de cada
report ID. Sem isso a gente chuta o tamanho do buffer do ioctl.

Formato de short item: byte0 = bTag (bits 0-3) | bType (bits 4-5) | bSize (bits 6-7)
  bType: 0=Main, 1=Global, 2=Local
Long item comeca com 0xfe.
"""
import sys
from pathlib import Path

MAIN = {0x08: "Input", 0x09: "Output", 0x0B: "Feature", 0x0A: "Collection", 0x0C: "End"}
GLOBAL = {0x75: "Report Size", 0x95: "Report Count", 0x85: "Report ID"}


def parse(dados):
    globais = {"Report Size": 0, "Report Count": 0, "Report ID": 0}
    relatorio = {}
    i = 0
    while i < len(dados):
        b0 = dados[i]
        if b0 == 0xFE:                       # long item
            tamanho, tag = dados[i + 1], dados[i + 2]
            i += 3 + tamanho
            continue
        btag, btype, bsize = b0 & 0x0F, (b0 >> 4) & 0x03, (b0 >> 6) & 0x03
        i += 1
        if bsize == 3:
            tamanho = 4
        else:
            tamanho = bsize
        valor = int.from_bytes(dados[i:i + tamanho], "little") if btype != 0 or btag in GLOBAL \
            else int.from_bytes(dados[i:i + tamanho], "little")
        i += tamanho

        if btype == 1 and btag in GLOBAL:    # Global
            globais[GLOBAL[btag]] = valor
        elif btype == 0 and btag in MAIN:    # Main
            if btag == 0x0B:                 # Feature: e' o que a gente usa
                bits = globais["Report Count"] * globais["Report Size"]
                rid = globais["Report ID"]
                relatorio[rid] = relatorio.get(rid, 0) + bits
            if btag == 0x0A:                 # Collection zera o estado
                pass
    return relatorio


def main():
    for hid in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        desc = hid / "device/report_descriptor"
        if not desc.exists():
            continue
        uevent = (hid / "device/uevent").read_text()
        if "0003:000004D9:0000FC30" not in uevent:
            continue
        real = Path(hid).resolve()
        if ":1.2" not in str(real):
            continue
        print(f"descriptor de {hid} (interface de vendor 1.2)")
        dados = desc.read_bytes()
        rel = parse(dados)
        print(f"\n  {'report ID':<12}{'bits':>8}{'bytes':>8}   (bytes = bits/8 + 1 p/ o ID)")
        for rid, bits in sorted(rel.items()):
            print(f"  {rid:<12}{bits:>8}{bits // 8 + 1:>8}")
        break


if __name__ == "__main__":
    main()
