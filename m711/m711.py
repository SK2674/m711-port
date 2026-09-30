#!/usr/bin/env python3
"""Ponto de entrada do port do Redragon M711.

Uso:
    python3 m711/m711.py <comando>

Ou, se estiver no PATH:
    m711 <comando>

Comandos: ler, bruto, exportar, led, backup, restaurar, diagnostico, verificar.
Veja `m711 --help`. Por padrão só lê; escrita exige --yes.
"""

import os
import sys

# Os módulos são planos (sem __init__.py), então o caminho do próprio
# arquivo precisa estar no sys.path para o import achar codigo, mouse, etc.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
