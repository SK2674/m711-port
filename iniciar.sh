#!/usr/bin/env bash
# Inicia o software REDRAGON Gaming Mouse (RDCfg.exe) dentro do Wine/flatpak.
# Caminho interno do sandbox: WINEPREFIX=/var/data/wine
exec flatpak run org.winehq.Wine "/var/data/wine/drive_c/Program Files (x86)/REDRAGON Gaming Mouse/RDCfg.exe" "$@"
