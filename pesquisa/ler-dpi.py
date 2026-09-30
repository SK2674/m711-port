#!/usr/bin/env python3
"""Le os DPI do M711 direto do dump bruto (mouse_m908 -D -).

O -R tem um bug no M711: o reader so' preenche 2 dos 4 bytes de cada
estagio (e ainda de outro buffer), entao o valor do eixo Y sai zerado.
O dump cru mostra o layout real:

  03 08 42 60 20 00 fa fa | PP 00 | [flag, X_lo, X_hi, Y_lo, Y_hi, 00] x 5

Logo: o DPI e' um valor de 16 bits little-endian, repetido nos eixos X e Y.
O codigo decodificado segue codigo = floor(DPI / 44.4444).

Uso:  python3 ~/Downloads/redragon/ler-dpi.py [perfil 1-5]
Somente leitura: nada e' alterado no mouse.
"""
import re
import subprocess
import sys
from pathlib import Path

DRIVER = Path.home() / "Downloads/redragon/port/mouse_m908/mouse_m908"
FATOR = 1 / 44.4444


def dump():
    out = subprocess.run([str(DRIVER), "-D", "-"], capture_output=True,
                         text=True, timeout=60)
    if "Part" not in out.stdout:
        print("ERRO: nao consegui ler o mouse.\n", out.stdout, out.stderr)
        sys.exit(1)
    return out.stdout


def extrair(texto, perfil):
    """Pega a linha do perfil pedido e devolve os 5 pares (X, Y)."""
    dentro = False
    for bloco in texto.split("Part"):
        for linha in bloco.splitlines():
            partes = linha.split()
            # descarta cabecalhos ("Part 1:") e linhas em branco
            if not partes or not all(re.fullmatch(r"[0-9a-fA-F]{2}", p) for p in partes):
                continue
            b = [int(x, 16) for x in partes]
            # assinatura: 03 08 .. .. 20 00 fa fa, com o perfil em [8:10].
            # a linha tem 10 bytes de cabecalho + 5 registros de 6 = 40 bytes.
            # cada linha do dump corresponde a um perfil.
            if len(b) >= 40 and b[0] == 0x03 and b[4] == 0x20 and b[5] == 0x00 \
                    and b[6] == 0xfa and b[7] == 0xfa:
                if b[8] == 0 and b[9] == perfil - 1:
                    return b[:40]
    return None


def main():
    perfil = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    b = extrair(dump(), perfil)
    if b is None:
        print(f"perfil {perfil} nao encontrado no dump")
        return

    print(f"Perfil {perfil} - DPI decodificado do dump bruto\n")
    print(f"  {'est':<5}{'X':>8}{'Y':>8}   {'DPI implicito':>16}   nota")
    print("  " + "-" * 60)

    for i in range(5):
        base = 10 + 6 * i
        flag = b[base]
        x = b[base + 1] | (b[base + 2] << 8)
        y = b[base + 3] | (b[base + 4] << 8)
        dpi = x * 44.4444
        nota = "" if x == y else "  << eixo Y DIFERENTE"
        if flag == 0:
            nota += "  (estagio desativado)"
        print(f"  {i+1:<5}{x:>8}{y:>8}   {dpi:>16.0f}{nota}")

    print(f"\n  formula de referencia: codigo = floor(DPI / 44.4444)")
    print("  (valores de fabrica dos perfis 2-5: 11, 22, 45, 67, 140")
    print("   => 500, 1000, 2000, 3000 e ~6222 DPI)")


if __name__ == "__main__":
    main()
