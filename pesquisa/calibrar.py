#!/usr/bin/env python3
"""Calibra a tabela DPI do M711: cruza os valores legiveis do app oficial
(os .prof) com os codigos crus lidos do mouse (mouse_m908 -R -).

Uso:  python3 ~/Downloads/redragon/calibrar.py [perfil 1-5]

O app oficial (RDCfg.exe via Wine) e' o unico que escreve no mouse.
Este script so' le. Nada e' alterado no hardware.
"""
import re
import subprocess
import sys
from pathlib import Path

DRIVER = Path.home() / "Downloads/redragon/port/mouse_m908/mouse_m908"
PROF_DIR = (Path.home() / ".var/app/org.winehq.Wine/data/wine/drive_c"
            / "Program Files (x86)/REDRAGON Gaming Mouse/REDRAGON Gaming Mouse/M711/Profile")


def ler_mouse():
    """Roda mouse_m908 -R - e devolve {perfil: [codigos dpi hex]}."""
    out = subprocess.run([str(DRIVER), "-R", "-"], capture_output=True,
                         text=True, timeout=60)
    if "Currently active profile" not in out.stdout:
        print("ERRO: nao consegui ler o mouse.\n", out.stdout, out.stderr)
        sys.exit(1)

    perfis, atual = {}, None
    for linha in out.stdout.splitlines():
        m = re.match(r"\[profile(\d)\]", linha.strip())
        if m:
            atual = int(m.group(1))
            perfis[atual] = {}
            continue
        m = re.match(r"dpi(\d)=0x([0-9a-f]+)", linha.strip())
        if m and atual:
            # o driver devolve 4 bytes little-endian; o codigo real e' o 1o byte
            bruto = bytes.fromhex(m.group(2))
            perfis[atual][int(m.group(1))] = bruto[0]
    return perfis


def ler_prof(perfil):
    """Le os DPI (em centenas) do .prof do app oficial."""
    arq = PROF_DIR / (f"Profile {perfil}.prof" if perfil else "DefaultProfile.prof")
    if not arq.exists():
        return {}
    valores = {}
    for linha in arq.read_text(errors="replace").splitlines():
        m = re.match(r"DPI_INFO_(\d)_xy=(-?\d+)", linha.strip())
        if m:
            valores[int(m.group(1)) + 1] = int(m.group(2))
    return valores


def main():
    perfil = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    mouse = ler_mouse()
    app = ler_prof(perfil)

    print(f"Mouse lido OK. Correlacionando com 'Profile {perfil}.prof' do app oficial:\n")
    print(f"  {'estagio':<9}{'DPI (app)':>12}{'codigo (mouse)':>18}   observacao")
    print("  " + "-" * 62)

    pares = []
    for est in range(1, 6):
        cod = mouse.get(perfil, {}).get(est)
        dpi = app.get(est)
        if dpi is None or cod is None:
            print(f"  {est:<9}{str(dpi or '?'):>12}{str(cod if cod is not None else '?'):>18}")
            continue
        dpi_real = dpi * 100
        pares.append((dpi_real, cod))
        print(f"  {est:<9}{dpi_real:>12}{('0x%02x (%d)' % (cod, cod)):>18}")

    if pares:
        known = sorted(set(pares))
        print("\n  tabela DPI -> codigo:")
        print("   " + ", ".join(f"{d}:0x{c:02x}" for d, c in known))
        # razao codigo/DPI, util pra checar linearidade
        print("\n  razao codigo/DPI (linear => protocolo bem mapeado):")
        for d, c in known:
            print(f"    {d:>6} -> 0x{c:02x} = {c:<4} razao {c / d:.5f}")
    print()


if __name__ == "__main__":
    main()
