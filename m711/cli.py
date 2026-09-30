"""Interface de linha de comando do port do M711.

Comandos:

    ler                 estado dos 5 perfis
    bruto [perfil]     hexdump dos buffers crus
    exportar [-o arq]   .ini no formato mouse_m908
    led ...             altera cor/modo/brilho/velocidade
    backup [arq]        salva o estado completo + os buffers crus
    restaurar arq       reescreve tudo a partir de um backup
    diagnostico         iface, permissões, mouse presente, libusb
    verificar           só lê: confere o read-modify-write

Convenções:

- Nada é escrito sem ``--yes``. Sem ele, o comando imprime o diff e para.
- ``--simular`` é o comportamento padrão de qualquer escrita.
- Se algo der errado depois de escrever, a saída de recuperação é rodar
  ``./iniciar.sh`` na raiz do projeto (o software oficial via Wine).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import codigo
import ini as ini_mod
import mouse as mouse_mod
import usb

DIR_PROJETO = Path(__file__).resolve().parent.parent
DIR_BACKUP = Path.home() / ".local" / "state" / "m711"

# Só o LED é liberado para escrita nesta fase (ver ARQUITETURA.md §4).
CAMPOS_LED = ("cor", "modo", "brilho", "velocidade")

# Modos em que a cor é IGNORADA pelo firmware — ele tem efeito próprio e
# usa os canais RGB por conta própria.
#
# Escrever a cor em um desses modos TRAVA O LED: o registro fica com os
# valores que o efeito não espera e a animação não reinicia. Medido em
# 2026-09-29: perfil 1 em arcoiris (branco) + `m711 led --cor ff0000`
# deixou o LED travado — os bytes voltavam certainos
# (`ff 00 00 01 08 08 03`) e o mouse seguia enumerando, mas não animava.
# Voltar para `estatico` com cor branca destravou.
#
# A causa provável é o firmware ler os bytes R/G/B como parte do estado do
# efeito; a correção segura é nunca mexer na cor desses modos. Escrever o
# modo E a cor juntos na mesma operação continua seguro, porque aí a cor
# vai para um modo que a usa.
MODOS_SEM_COR = ("off", "arcoiris", "onda", "respirar_arcoiris")


# ---------------------------------------------------------------------------
# Apresentação
# ---------------------------------------------------------------------------

def _cor(texto: str, *codigos: str) -> str:
    """Aplica cor ANSI se a saída for um terminal."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return texto
    return "".join(codigos) + texto + "\033[0m"


def _verde(t: str) -> str:
    return _cor(t, "\033[32m")


def _vermelho(t: str) -> str:
    return _cor(t, "\033[31m")


def _amarelo(t: str) -> str:
    return _cor(t, "\033[33m")


def _ciano(t: str) -> str:
    return _cor(t, "\033[36m")


def _negrito(t: str) -> str:
    return _cor(t, "\033[1m")


def mostra_estado(estado: dict[str, Any]) -> None:
    """Imprime o estado dos 5 perfis de forma legível."""
    ativo = estado["perfil_ativo"]
    print(_negrito(f"Perfil ativo: {ativo}"))
    print()
    for p in estado["perfis"]:
        num = p["numero"]
        marca = _verde(">") if num == ativo else " "
        led = p["led"]
        print(f"{marca} {_negrito('perfil %d' % num)}")
        print(f"    LED     cor={led['cor']}  modo={led['modo']}  "
              f"brilho={led['brilho']}  velocidade={led['velocidade']}")
        print(f"    scroll  {p['scrollspeed']}   poll {p['report_rate']} Hz")
        dpis = []
        for i, d in enumerate(p["dpi"], 1):
            if not d["ativo"]:
                dpis.append(f"{i}:off")
            elif d["x"] == d["y"]:
                dpis.append(f"{i}:{d['x']}")
            else:
                dpis.append(f"{i}:X{d['x']}Y{d['y']}")
        print(f"    DPI     {'  '.join(dpis)}")
        alterados = {k: v for k, v in p["botoes"].items() if v != "none"}
        for nome, valor in p["botoes"].items():
            if nome in alterados:
                print(f"      {nome:12s} {valor}")
        print()


def mostra_dpi(niveis: list[dict[str, Any]]) -> None:
    """Imprime os 5 níveis de DPI de um perfil."""
    for i, d in enumerate(niveis, 1):
        if not d["ativo"]:
            print(f"      {i}: desativado")
        elif d["x"] == d["y"]:
            print(f"      {i}: {d['x']}")
        else:
            print(f"      {i}: X{d['x']} Y{d['y']}")


def mostra_diff(diff: list[tuple[str, int, int, int]]) -> None:
    """Imprime o diff byte a byte."""
    if not diff:
        print(_verde("Nenhum byte muda."))
        return
    print(_amarelo(f"{len(diff)} byte(s) mudam:"))
    print(f"  {'buffer':8s} {'offset':>6s}  {'antes':>6s}  {'depois':>6s}")
    for buf, off, antes, depois in diff:
        print(f"  {buf:8s} {off:6d}  {antes:02x}     {depois:02x}    "
              f"{_vermelho('^') if antes != depois else ''}")


# ---------------------------------------------------------------------------
# Diagnóstico e erros
# ---------------------------------------------------------------------------

def _lembre_recuperacao() -> str:
    return (f"\n{_amarelo('Se algo deu errado no mouse:')} "
            f"rode {_ciano(str(DIR_PROJETO / 'iniciar.sh'))} "
            f"para abrir o software oficial via Wine.\n")


def cmd_diagnostico(args: argparse.Namespace) -> int:
    """Mostra o ambiente sem precisar do mouse."""
    print(_negrito("Diagnóstico M711"))
    print()
    print(f"  libusb         {usb.versao_libusb() or _vermelho('não encontrada')}"
          "   (instale libusb-1.0-0 / libusb)")
    print(f"  python         {sys.version.split()[0]}  ({sys.executable})")
    print()

    try:
        devs = usb.descobrir(mouse_mod.VID, mouse_mod.PID)
    except usb.ErroUsb as e:
        print(f"  enumeração     {_vermelho(e.mensagem)}")
        return 1

    if not devs:
        print(f"  mouse          {_vermelho('não encontrado')}")
        print("                 Conecte o mouse e confira com: lsusb | grep -i 04d9:fc30")
        return 1

    for ident, caminho in devs:
        vid, pid, bus, addr = ident
        print(f"  mouse          {_verde('encontrado')}  {caminho}")
        print(f"                 VID:PID {vid:04x}:{pid:04x}  bus {bus} device {addr}")
        # Permissão de escrita no nó USB (libusb precisa de O_RDWR)
        nodo = Path(caminho)
        if nodo.exists():
            # O bit de escrita do dono (S_IWUSR = 0o200) é o que a libusb
            # precisa para abrir o nó em O_RDWR.
            tem_escrita = bool(nodo.stat().st_mode & 0o200)
            if tem_escrita:
                print(f"                 {_verde('permissão de escrita OK')}")
            else:
                print(f"                 {_vermelho('sem permissão de escrita')}"
                      f"  → rode {_ciano(str(DIR_PROJETO / 'fix-usb.sh'))}")
        else:
            print(f"                 {_amarelo('nó USB sumiu (mouse desconectado?)')}")

    # Testa a abertura e uma leitura mínima de verdade — só leitura, não
    # escreve nada no mouse. Só pede o perfil ativo para não gastar 200
    # leituras de macro num diagnóstico.
    # Usa ler_bruto() (e não ler_buffer1 direto) porque o mouse precisa ser
    # destravado antes da leitura e retravado depois — sem o par, o mouse
    # fica instável.
    try:
        with mouse_mod.Mouse() as m:
            m._travar(False)
            try:
                b1 = m.ler_buffer1()
            finally:
                m._travar(True)
        print(f"  abertura       {_verde('OK')}  "
              f"(perfil ativo = {b1[0][0] + 1})")
    except usb.ErroUsb as e:
        print(f"  abertura       {_vermelho(e.mensagem)}")
        if e.codigo == usb.ERRO_ACCESS:
            print(f"                 → rode {_ciano(str(DIR_PROJETO / 'fix-usb.sh'))} e reconecte")
    except Exception as e:  # noqa: BLE001
        print(f"  abertura       {_vermelho(str(e))}")
    return 0


def _erro(msg: str, recuperacao: bool = False) -> int:
    print(_vermelho(f"erro: {msg}"), file=sys.stderr)
    if recuperacao:
        print(_lembre_recuperacao(), file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def cmd_ler(args: argparse.Namespace) -> int:
    with mouse_mod.Mouse() as m:
        estado = m.ler()
    if args.json:
        print(json.dumps(estado, indent=2, ensure_ascii=False))
    else:
        mostra_estado(estado)
    return 0


def _hexdump(dados: bytes, base: int = 0, por_linha: int = 16) -> str:
    linhas = []
    for i in range(0, len(dados), por_linha):
        pedaco = dados[i:i + por_linha]
        hexa = " ".join(f"{b:02x}" for b in pedaco)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in pedaco)
        linhas.append(f"{base + i:08x}  {hexa:<{por_linha * 3}}  |{ascii_}|")
    return "\n".join(linhas)


def cmd_bruto(args: argparse.Namespace) -> int:
    with mouse_mod.Mouse() as m:
        bruto = m.ler_bruto()

    if args.perfil:
        n = args.perfil
        if not 1 <= n <= 5:
            return _erro(f"perfil deve ser 1..5 (veio {n})")
        i = n - 1
        print(_negrito(f"Perfil {n} — LED"))
        print(_hexdump(bruto["b1"][n]))
        print()
        print(_negrito(f"Perfil {n} — DPI (32 bytes)"))
        print(_hexdump(bruto["b2"][i]))
        print()
        print(_negrito(f"Perfil {n} — keymap (10 botões × 4 bytes)"))
        for j, b in enumerate(bruto["keymap"][i]):
            nome = codigo.NOMES_BOTAO[j]
            print(f"  {nome:12s} {b.hex(' ')}  {codigo.botao_para_texto(tuple(b))}")
        print()
        print(f"  scrollspeed = {bruto['scrollspeed'][i]}")
        print(f"  poll rate   = {codigo.bytes_para_pollrate(bruto['pollrate'][i])} Hz")
        return 0

    print(_negrito("buffer1 — perfil ativo + LED dos 5 perfis"))
    for i, q in enumerate(bruto["b1"]):
        rot = "perfil ativo" if i == 0 else f"LED perfil {i}"
        print(f"  [{i}] {rot}: {q.hex(' ')}")
    print()
    print(_negrito("buffer2 — tabela de DPI (5 perfis × 32 bytes)"))
    for i in range(5):
        print(f"  perfil {i + 1}: {bruto['b2'][i].hex(' ')}")
    print()
    print(_negrito("keymap — 10 botões × 5 perfis"))
    for i in range(5):
        print(f"  perfil {i + 1}:")
        for j, b in enumerate(bruto["keymap"][i]):
            print(f"    {codigo.NOMES_BOTAO[j]:12s} {b.hex(' ')}  "
                  f"{codigo.botao_para_texto(tuple(b))}")
    print()
    print(_negrito("macros — 15 slots (200 bytes cada, hex)"))
    for macro in bruto["macros"]:
        print(f"  slot {macro['slot']:2d}: {macro['bruto'][:80]}...")
    return 0


def cmd_exportar(args: argparse.Namespace) -> int:
    with mouse_mod.Mouse() as m:
        estado = m.ler()
    texto = ini_mod.para_texto(estado)
    if args.saida:
        caminho = Path(args.saida)
        ini_mod.salvar_arquivo(caminho, estado)
        print(f"{_verde('gravado')} em {caminho}")
    else:
        sys.stdout.write(texto)
    return 0


# ---------------------------------------------------------------------------
# Verificação do read-modify-write (só leitura)
# ---------------------------------------------------------------------------

def cmd_verificar(args: argparse.Namespace) -> int:
    """Confere que montar os buffers a partir da leitura é a identidade.

    Se `monta(decodifica(ler_bruto()))` não reproduz o que o mouse tem, o
    read-modify-write tem um buraco e qualquer escrita corromperia os
    perfis que não foram editados. Este comando não escreve nada.
    """
    with mouse_mod.Mouse() as m:
        bruto = m.ler_bruto()
        estado = m.decodifica(bruto)

        # O buffer1 de escrita tem 15 quadros de 16 bytes. O que o mouse
        # devolveu na leitura são só 6 quadros (perfil ativo + 5 LEDs), e
        # o resto é reconstruído a partir do template em dados.py. Então a
        # comparação honesta é sobre os campos que realmente persistem:
        # LED dos 5 perfis, scrollspeed e poll rate.
        esperado = {
            "led": [p["led"] for p in estado["perfis"]],
            "scrollspeed": [p["scrollspeed"] for p in estado["perfis"]],
            "pollrate": bruto["pollrate"],
        }

    # Re-decodifica a partir dos buffers montados: se a montagem é fiel,
    # o round-trip tem que devolver exatamente o mesmo estado.
    b1, b2, b3 = mouse_mod.Mouse.monta(estado)

    # Atenção ao brilho: na LEITURA o brilho vem dentro do registro de LED
    # (0x0449 com count=7, byte [6] dos dados), mas na ESCRITA ele tem um
    # quadro separado (0x044f, count=1, byte [8]). Não são o mesmo lugar,
    # então o brilho é conferido no quadro de escrita.
    led_montado = []
    for i in range(5):
        q_led = b1[(3 + 2 * i) * 16:(4 + 2 * i) * 16]     # 0x0449 + 8*i
        q_bri = b1[(4 + 2 * i) * 16:(5 + 2 * i) * 16]     # 0x044f + 8*i
        led_montado.append({
            "cor": codigo.bytes_para_cor(q_led[8], q_led[9], q_led[10]),
            "modo": codigo.bytes_para_modo(q_led[11], q_led[13]),
            "velocidade": q_led[12],
            "brilho": q_bri[8],
        })
    scroll_montado = [b2[8 + 2 * i] for i in range(5)]

    # O DPI é montado com um mapeamento (perfil, nível) -> quadro que o
    # upstream erra, então precisa da checagem explícita: o registro de
    # (perfil, nível) tem que virar o mesmo par (lo, hi) que foi lido.
    problemas: list[str] = []
    for p in range(5):
        for j in range(5):
            idx = mouse_mod._ENDERECO_DPI[p][j]
            q = b3[idx * 16:(idx + 1) * 16]
            lido = bruto["b2"][p]
            b = 2 + 6 * j
            esperado_dpi = (lido[b], lido[b + 1], lido[b + 2], lido[b + 3])
            obtido = (q[8], q[9], q[10], q[11])
            if esperado_dpi != obtido:
                problemas.append(
                    f"perfil {p + 1} nível {j + 1}: DPI gravado {obtido} "
                    f"!= lido {esperado_dpi}")

    for i in range(5):
        if led_montado[i] != esperado["led"][i]:
            problemas.append(
                f"perfil {i + 1}: LED {esperado['led'][i]} != {led_montado[i]}")
    if scroll_montado != esperado["scrollspeed"]:
        problemas.append(
            f"scrollspeed {esperado['scrollspeed']} != {scroll_montado}")

    # Keymap: cada (perfil, botão) tem que virar o mesmo keycode lido.
    for p in range(5):
        for j in range(10):
            idx = mouse_mod._ENDERECO_KEYMAP_ESCRITA[p][j]
            q = b3[idx * 16:(idx + 1) * 16]
            esperado_botao = tuple(bruto["keymap"][p][j])
            obtido = tuple(q[8:12])
            if esperado_botao != obtido:
                problemas.append(
                    f"perfil {p + 1} botão {codigo.NOMES_BOTAO[j]}: gravado "
                    f"{bytes(obtido).hex(' ')} != "
                    f"lido {bytes(esperado_botao).hex(' ')}")

    if problemas:
        print(_vermelho(f"FALHOU: {len(problemas)} divergência(s) "
                        f"no read-modify-write:"))
        for p in problemas:
            print(f"  - {p}")
        print()
        print(_vermelho("NÃO escreva nada no mouse até isso ser resolvido."))
        return 1

    print(_verde("OK: montar os buffers a partir da leitura é a identidade."))
    print("     O read-modify-write preserva os 5 perfis e todos os campos.")
    print()
    for i in range(5):
        led = esperado["led"][i]
        print(f"  perfil {i + 1}: cor={led['cor']} modo={led['modo']} "
              f"brilho={led['brilho']} vel={led['velocidade']} "
              f"scroll={esperado['scrollspeed'][i]}")
    return 0


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def _fazer_backup(estado: dict[str, Any], bruto: dict[str, Any],
                  caminho: Path | None = None) -> Path:
    """Salva o estado + os buffers crus. Retorna o caminho usado."""
    if caminho is None:
        DIR_BACKUP.mkdir(parents=True, exist_ok=True)
        caminho = DIR_BACKUP / f"backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
    else:
        caminho.parent.mkdir(parents=True, exist_ok=True)

    def hexlist(lst: list[bytes]) -> list[str]:
        return [b.hex() for b in lst]

    dados = {
        "data": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "modelo": "m711",
        "estado": estado,
        "bruto": {
            "b1": hexlist(bruto["b1"]),
            "b2": hexlist(bruto["b2"]),
            "keymap": [hexlist(k) for k in bruto["keymap"]],
            "scrollspeed": bruto["scrollspeed"],
            "pollrate": bruto["pollrate"],
            "macros": bruto["macros"],
        },
    }
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    return caminho


def cmd_backup(args: argparse.Namespace) -> int:
    with mouse_mod.Mouse() as m:
        bruto = m.ler_bruto()
        estado = m.decodifica(bruto)
    caminho = Path(args.arquivo) if args.arquivo else None
    destino = _fazer_backup(estado, bruto, caminho)
    print(f"{_verde('backup salvo')} em {destino}")
    return 0


# ---------------------------------------------------------------------------
# Escrita: LED
# ---------------------------------------------------------------------------

def cmd_led(args: argparse.Namespace) -> int:
    campos = {k: v for k, v in (
        ("cor", args.cor),
        ("modo", args.modo),
        ("brilho", args.brilho),
        ("velocidade", args.velocidade),
    ) if v is not None}

    if not campos:
        return _erro("informe pelo menos um campo: --cor, --modo, --brilho "
                     "ou --velocidade")

    if not 1 <= args.perfil <= 5:
        return _erro(f"perfil deve ser 1..5 (veio {args.perfil})")

    # Valida ANTES de abrir o mouse: erro de digitação não deve exigir que
    # o mouse esteja presente, nem deixar nada pela metade.
    try:
        if "cor" in campos:
            campos["cor"] = codigo.bytes_para_cor(*codigo.cor_para_bytes(campos["cor"]))
        if "modo" in campos:
            campos["modo"] = codigo.normaliza_modo(campos["modo"])
            # A velocidade só faz sentido para os modos que a usam.
            vel_modo = codigo.VELOCIDADES.get(campos["modo"], (0,))
            if "velocidade" in campos and campos["velocidade"] not in vel_modo:
                return _erro(
                    f"velocidade {campos['velocidade']} inválida para o modo "
                    f"'{campos['modo']}' (aceita {list(vel_modo)})")
        if "brilho" in campos:
            lo, hi = codigo.dados.LIMITS["brilho"]
            if not lo <= campos["brilho"] <= hi:
                return _erro(f"brilho deve ser {lo}..{hi} (veio {campos['brilho']})")
        if "velocidade" in campos:
            lo, hi = codigo.dados.LIMITS["velocidade"]
            if not lo <= campos["velocidade"] <= hi:
                return _erro(f"velocidade deve ser {lo}..{hi} "
                             f"(veio {campos['velocidade']})")
    except codigo.ErroCodigo as e:
        return _erro(str(e))

    alterar = {"perfis": {args.perfil: {"led": campos}}}

    print(_amarelo("MODO SIMULAÇÃO — nada será escrito."))
    print(f"  {_negrito('perfil')} {args.perfil}  {_negrito('mudanças')}: "
          + ", ".join(f"{k}={v}" for k, v in campos.items()))
    print()

    with mouse_mod.Mouse() as m:
        resultado = m.aplicar(alterar)
        antes = resultado["antes"]["perfis"][args.perfil - 1]
        depois = resultado["depois"]["perfis"][args.perfil - 1]

        print(_negrito("Antes"))
        print(f"  {antes['led']}")
        print(_negrito("Depois"))
        print(f"  {depois['led']}")
        print()
        print(_negrito("Bytes que mudam no buffer de escrita"))
        mostra_diff(resultado["diff"])

        # Trava: escrever a cor enquanto o perfil está em um modo que não usa
        # cor trava o LED (ver MODOS_SEM_COR). Só vale bloquear se o modo
        # final também ficar sem cor — se a operação inclui trocar para um
        # modo que usa cor, é seguro.
        if "cor" in campos and "modo" not in campos:
            modo_final = depois["led"]["modo"]
            if modo_final in MODOS_SEM_COR:
                print()
                print(_vermelho(
                    f"BLOQUEADO: escrever a cor no modo '{modo_final}' trava "
                    f"o LED."))
                print(_vermelho(
                    "  Modos que não usam cor: "
                    + ", ".join(MODOS_SEM_COR)))
                print()
                print("  Para trocar de cor, mude o modo na mesma operação:")
                print(_ciano(f"    m711 led --perfil {args.perfil} "
                             f"--modo estatico --cor {campos['cor']} --yes"))
                print("  Se o LED já estiver travado, isso costuma resolver:")
                print(_ciano(f"    m711 led --perfil {args.perfil} "
                             f"--modo estatico --cor ffffff --yes"))
                return 1

        # Backup automático antes de qualquer escrita.
        bruto = m.ler_bruto()
        destino = _fazer_backup(resultado["antes"], bruto)
        print()
        print(f"  backup automático: {destino}")

        if not args.yes:
            print()
            print(_amarelo("NADA FOI ESCRITO."))
            print(f"Para aplicar de verdade, rode o mesmo comando com {_ciano('--yes')}.")
            return 0

        print()
        print(_amarelo(f"ESCREVENDO no perfil {args.perfil}..."))
        m.enviar(resultado["b1"], resultado["b2"], resultado["b3"])
        print(_verde("escrito."))

        # Relê e compara: se algo que não devia ter mudado mudou, aparece.
        depois_lido = m.ler()

    print()
    print(_negrito("Confirmação (relendo o mouse)"))
    led_lido = depois_lido["perfis"][args.perfil - 1]["led"]
    mudou_certo = led_lido == depois["led"]
    if mudou_certo:
        print(f"  {_verde('OK')}  perfil {args.perfil}: {led_lido}")
    else:
        print(f"  {_vermelho('DIVERGE')}  li {led_lido}, esperava {depois['led']}")
        for campo in depois["led"]:
            if campo in led_lido and led_lido[campo] != depois["led"][campo]:
                print(f"       {campo}: esperava {depois['led'][campo]!r}, "
                      f"mouse tem {led_lido[campo]!r}")
        # Caso conhecido: trocar o modo sem tocar na velocidade deixa o byte
        # de velocidade obsoleto no firmware. Não trava o LED, mas convém
        # dizer, porque o valor lido vai parecer errado.
        if (led_lido["modo"] == depois["led"]["modo"]
                and led_lido["velocidade"] != depois["led"]["velocidade"]):
            modo = led_lido["modo"]
            vel = codigo.VELOCIDADES.get(modo, (0,))
            print(f"       (o modo '{modo}' não usa velocidade "
                  f"{led_lido['velocidade']}; aceita {list(vel)})")

    # Confere que os outros 4 perfis não foram tocados.
    intactos = all(
        depois_lido["perfis"][i] == resultado["antes"]["perfis"][i]
        for i in range(5) if i != args.perfil - 1
    )
    if intactos:
        print(f"  {_verde('OK')}  os outros 4 perfis ficaram intactos")
    else:
        print(f"  {_vermelho('ATENÇÃO')}  algum outro perfil mudou:")
        for i in range(5):
            if i == args.perfil - 1:
                continue
            a = resultado["antes"]["perfis"][i]
            d = depois_lido["perfis"][i]
            if a != d:
                for campo in a:
                    if a[campo] != d[campo]:
                        print(f"       perfil {i + 1}.{campo}: {a[campo]} -> {d[campo]}")

    return 0 if (mudou_certo and intactos) else 1


# ---------------------------------------------------------------------------
# Restaurar
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Escrita: DPI
# ---------------------------------------------------------------------------

def _parse_lista_dpi(texto: str) -> list[int | None]:
    """Converte '400,800,1600,3200,6400' em [400, 800, 1600, 3200, 6400].

    Aceita 'off' ou '-' num item para deixar aquele nível como está.
    """
    itens = [t.strip() for t in texto.split(",") if t.strip()]
    if len(itens) != mouse_mod.Mouse.NIVEIS_DPI:
        raise ValueError(
            f"esperava {mouse_mod.Mouse.NIVEIS_DPI} valores separados por "
            f"vírgula (veio {len(itens)})")
    saida: list[int | None] = []
    for item in itens:
        if item.lower() in ("off", "-", "x"):
            saida.append(None)
            continue
        if "x" in item.lower():  # 800x400 = X e Y diferentes
            px, _, py = item.lower().partition("x")
            saida.append((int(px), int(py)))  # type: ignore[arg-type]
            continue
        saida.append(int(item))
    return saida


def cmd_dpi(args: argparse.Namespace) -> int:
    if not 1 <= args.perfil <= 5:
        return _erro(f"perfil deve ser 1..5 (veio {args.perfil})")

    # Monta o patch de DPI a partir dos argumentos
    if args.valores:
        try:
            valores = _parse_lista_dpi(args.valores)
        except ValueError as e:
            return _erro(str(e))
        patch: dict[str, Any] = {"valores": valores}
        ativos = None
        if args.ativos:
            partes = [p.strip() for p in args.ativos.split(",")]
            if len(partes) != mouse_mod.Mouse.NIVEIS_DPI:
                return _erro(f"--ativos precisa de {mouse_mod.Mouse.NIVEIS_DPI} "
                             f"itens (veio {len(partes)})")
            ativos = [p.lower() in ("1", "true", "s", "sim", "on") for p in partes]
            patch["ativos"] = ativos
    else:
        if args.nivel is None:
            return _erro("informe --nivel 1..5 (com --valor) ou --valores "
                         "'400,800,1600,3200,6400'")
        if args.valor is None and args.desativar is None and args.ativar is None:
            return _erro("informe --valor N (e opcionalmente --x/--y), ou "
                         "--desativar/--ativar")
        patch = {"nivel": args.nivel}
        if args.valor is not None:
            patch["valor"] = args.valor
        if args.x is not None:
            patch["x"] = args.x
        if args.y is not None:
            patch["y"] = args.y
        if args.desativar:
            patch["ativo"] = False
        elif args.ativar:
            patch["ativo"] = True
        # O M711 não tem DPI assimétrico (o firmware descarta o eixo Y).
        # Recusa aqui, antes de abrir o mouse, para não descobrir na
        # releitura que o Y foi zerado.
        if args.x is not None and args.y is not None and args.x != args.y:
            return _erro(
                f"o M711 não tem DPI assimétrico: pediu X={args.x} Y={args.y}, "
                f"mas o firmware descarta o Y (fica 0). Use --valor N para os "
                f"dois eixos, ou --x N --y N com o mesmo valor.")

    alterar = {"perfis": {args.perfil: {"dpi": patch}}}

    print(_amarelo("MODO SIMULAÇÃO — nada será escrito."))
    desc = []
    if "valores" in patch:
        desc.append(f"valores={args.valores}")
    else:
        desc.append(f"nível {args.nivel}")
        if "valor" in patch:
            desc.append(f"→ {patch['valor']}")
        if "x" in patch:
            desc.append(f"X{patch['x']}")
        if "y" in patch:
            desc.append(f"Y{patch['y']}")
        if "ativo" in patch:
            desc.append("ativar" if patch["ativo"] else "desativar")
    print(f"  {_negrito('perfil')} {args.perfil}  "
          f"{_negrito('DPI')}: {', '.join(desc)}")
    print()

    with mouse_mod.Mouse() as m:
        resultado = m.aplicar(alterar)
        antes = resultado["antes"]["perfis"][args.perfil - 1]
        depois = resultado["depois"]["perfis"][args.perfil - 1]

        print(_negrito("Antes"))
        mostra_dpi(antes["dpi"])
        print(_negrito("Depois"))
        mostra_dpi(depois["dpi"])
        print()

        # Avisa da quantização do firmware: o valor gravado pode não ser
        # exatamente o pedido porque o firmware só tem degraus de ~22.
        # O que o mouse vai LER de volta é codigo.bytes_para_dpi do par
        # gravado, não o valor pedido.
        avisos = []
        for j, (a, d) in enumerate(zip(antes["dpi"], depois["dpi"])):
            if a["x"] == d["x"] and a["y"] == d["y"]:
                continue
            # Descobre o que o firmware realmente vai devolver
            for eixo in ("x", "y"):
                pedido = d[eixo]
                lo, hi = codigo.dpi_para_bytes(pedido)
                real = codigo.bytes_para_dpi(lo, hi)
                if real != pedido:
                    avisos.append(
                        f"nível {j + 1} eixo {eixo.upper()}: pediu {pedido}, "
                        f"o mouse vai ler {real} "
                        f"(diferença {abs(pedido - real)})")
        if avisos:
            print(_amarelo("O firmware quantiza o DPI em degraus de ~22:"))
            for w in avisos:
                print(f"  {w}")
            print()

        print(_negrito("Bytes que mudam no buffer de escrita"))
        mostra_diff(resultado["diff"])

        bruto = m.ler_bruto()
        destino = _fazer_backup(resultado["antes"], bruto)
        print()
        print(f"  backup automático: {destino}")

        if not args.yes:
            print()
            print(_amarelo("NADA FOI ESCRITO."))
            print(f"Para aplicar de verdade, acrescente {_ciano('--yes')}.")
            return 0

        print()
        print(_amarelo(f"ESCREVENDO a DPI do perfil {args.perfil}..."))
        m.enviar(resultado["b1"], resultado["b2"], resultado["b3"])
        print(_verde("escrito."))
        depois_lido = m.ler()

    print()
    print(_negrito("Confirmação (relendo o mouse)"))
    dpi_lido = depois_lido["perfis"][args.perfil - 1]["dpi"]

    # Compara com o que o firmware PODE devolver, não com o que foi pedido:
    # o DPI é quantizado em degraus de ~22, então pedir 500 e ler 489 é o
    # comportamento correto, não uma divergência.
    def _esperado(d: dict[str, Any]) -> dict[str, Any]:
        saida = {"ativo": d["ativo"]}
        for eixo in ("x", "y"):
            lo, hi = codigo.dpi_para_bytes(d[eixo])
            saida[eixo] = codigo.bytes_para_dpi(lo, hi)
        return saida

    esperado = [_esperado(d) for d in depois["dpi"]]
    if dpi_lido == esperado:
        if dpi_lido == depois["dpi"]:
            print(f"  {_verde('OK')}  perfil {args.perfil} DPI confere")
        else:
            print(f"  {_verde('OK')}  perfil {args.perfil} DPI confere "
                  f"(com a quantização do firmware)")
        mostra_dpi(dpi_lido)
    else:
        print(f"  {_vermelho('DIVERGE')}  perfil {args.perfil}:")
        for j, (d, l, es) in enumerate(zip(depois["dpi"], dpi_lido, esperado)):
            if l != es:
                print(f"       nível {j + 1}: esperava x={es['x']} y={es['y']} "
                      f"(pedido x={d['x']} y={d['y']}), "
                      f"mouse tem x={l['x']} y={l['y']} ativo={l['ativo']}")
        print(_lembre_recuperacao())
        return 1

    intactos = all(
        depois_lido["perfis"][i] == resultado["antes"]["perfis"][i]
        for i in range(5) if i != args.perfil - 1
    )
    if intactos:
        print(f"  {_verde('OK')}  os outros 4 perfis ficaram intactos")
    else:
        print(f"  {_vermelho('ATENÇÃO')}  algum outro perfil mudou:")
        for i in range(5):
            if i == args.perfil - 1:
                continue
            a = resultado["antes"]["perfis"][i]
            dd = depois_lido["perfis"][i]
            for campo in a:
                if a[campo] != dd[campo]:
                    print(f"       perfil {i + 1}.{campo}: {a[campo]} -> {dd[campo]}")
    return 0


def cmd_restaurar(args: argparse.Namespace) -> int:
    caminho = Path(args.arquivo)
    if not caminho.exists():
        return _erro(f"arquivo não encontrado: {caminho}")
    try:
        doc = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _erro(f"{caminho} não é um JSON válido: {e}")

    if "estado" not in doc:
        return _erro(f"{caminho} não parece um backup do m711 (falta 'estado')")

    estado = doc["estado"]
    print(_amarelo(f"Restaurando de {caminho} ({doc.get('data', '?')})"))
    print()
    mostra_estado(estado)
    print()

    if not args.yes:
        print(_amarelo("NADA FOI ESCRITO."))
        print(f"Para gravar de verdade, acrescente {_ciano('--yes')}.")
        return 0

    b1, b2, b3 = mouse_mod.Mouse.monta(estado)
    with mouse_mod.Mouse() as m:
        bruto = m.ler_bruto()
        _fazer_backup(m.decodifica(bruto), bruto)  # backup do estado atual antes
        print(_amarelo("ESCREVENDO os 3 buffers..."))
        m.enviar(b1, b2, b3)
        print(_verde("escrito."))
        relido = m.ler()

    iguais = relido == estado
    if iguais:
        print(f"  {_verde('OK')}  o estado relido é idêntico ao do backup")
        return 0
    print(f"  {_vermelho('DIVERGE')}  o estado relido não bate com o backup:")
    for i in range(5):
        a, d = estado["perfis"][i], relido["perfis"][i]
        for campo in a:
            if a[campo] != d[campo]:
                print(f"       perfil {i + 1}.{campo}: {a[campo]} -> {d[campo]}")
    print(_lembre_recuperacao())
    return 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _constroi_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m711",
        description="Port do software Redragon para Linux (CLI). "
                    "Por padrão só lê; escrita exige --yes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""exemplos:
  m711 ler                          mostra os 5 perfis
  m711 led --perfil 1 --cor 00ff00           simula (não escreve)
  m711 led --perfil 1 --cor 00ff00 --yes     escreve de verdade
  m711 led --perfil 2 --modo arcoiris --velocidade 5 --yes
  m711 dpi --perfil 1 --nivel 3 --valor 1600            simula
  m711 dpi --perfil 1 --nivel 3 --valor 1600 --yes      escreve
  m711 dpi --perfil 1 --valores 400,800,1600,3200,6400 --yes
  m711 dpi --perfil 1 --nivel 5 --desativar --yes     desliga um nível
  m711 exportar -o meu.ini         salva a configuração
  m711 diagnostico                 checa permissões e libusb
  m711 verificar                   confere o read-modify-write (só lê)

Modos de LED: """ + ", ".join(sorted(codigo.MODOS)) + """
Cores: nome (vermelho, azul, ...) ou hex RRGGBB
""")
    p.add_argument("--json", action="store_true",
                   help="saída em JSON (para 'ler')")
    sub = p.add_subparsers(dest="comando", metavar="COMANDO")

    s = sub.add_parser("ler", help="estado dos 5 perfis")
    s.set_defaults(func=cmd_ler)

    s = sub.add_parser("bruto", help="hexdump dos buffers crus")
    s.add_argument("perfil", nargs="?", type=int, metavar="perfil",
                   help="opcional: 1..5 para detalhar um perfil")
    s.set_defaults(func=cmd_bruto)

    s = sub.add_parser("exportar", help="exporta a configuração para .ini")
    s.add_argument("-o", "--saida", metavar="ARQ", help="arquivo de destino")
    s.set_defaults(func=cmd_exportar)

    s = sub.add_parser("led", help="altera cor/modo/brilho/velocidade do LED")
    s.add_argument("--perfil", type=int, required=True, metavar="N",
                   help="perfil 1..5")
    s.add_argument("--cor", metavar="RRGGBB", help="cor (hex ou nome)")
    s.add_argument("--modo", metavar="MODO", help="modo de luz")
    s.add_argument("--brilho", type=int, metavar="1-3", help="brilho")
    s.add_argument("--velocidade", type=int, metavar="1-8", help="velocidade")
    s.add_argument("--yes", action="store_true",
                   help="efetiva a escrita (sem isso é só simulação)")
    s.set_defaults(func=cmd_led)

    s = sub.add_parser("dpi", help="altera os níveis de DPI de um perfil")
    s.add_argument("--perfil", type=int, required=True, metavar="N",
                   help="perfil 1..5")
    s.add_argument("--nivel", type=int, metavar="1-5",
                   help="um nível só (1..5)")
    s.add_argument("--valor", type=int, metavar="DPI",
                   help="DPI do nível (X=Y)")
    s.add_argument("--x", type=int, metavar="DPI",
                   help="DPI do eixo X (para DPI assimétrico)")
    s.add_argument("--y", type=int, metavar="DPI",
                   help="DPI do eixo Y")
    s.add_argument("--desativar", action="store_true",
                   help="desativa o nível (não pode ser o último ativo)")
    s.add_argument("--ativar", action="store_true",
                   help="reativa o nível")
    s.add_argument("--valores", metavar="LISTA",
                   help="os 5 de uma vez: '400,800,1600,3200,6400' "
                        "(use 'off' num item para deixar como está)")
    s.add_argument("--ativos", metavar="LISTA",
                   help="quais níveis ficam ativos: '1,1,1,0,1'")
    s.add_argument("--yes", action="store_true",
                   help="efetiva a escrita (sem isso é só simulação)")
    s.set_defaults(func=cmd_dpi)

    s = sub.add_parser("backup", help="salva o estado + os buffers crus")
    s.add_argument("arquivo", nargs="?", metavar="ARQ",
                   help="opcional: destino (padrão ~/.local/state/m711/)")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restaurar", help="reescreve tudo a partir de um backup")
    s.add_argument("arquivo", metavar="ARQ", help="backup.json")
    s.add_argument("--yes", action="store_true",
                   help="efetiva a escrita (sem isso é só simulação)")
    s.set_defaults(func=cmd_restaurar)

    s = sub.add_parser("diagnostico", help="iface, permissões, libusb, mouse")
    s.set_defaults(func=cmd_diagnostico)

    s = sub.add_parser("verificar",
                       help="confere o read-modify-write (só leitura)")
    s.set_defaults(func=cmd_verificar)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _constroi_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except usb.ErroUsb as e:
        return _erro(e.mensagem, recuperacao=("escrev" in args.comando))
    except mouse_mod.ErroMouse as e:
        return _erro(str(e))
    except codigo.ErroCodigo as e:
        return _erro(str(e))
    except ini_mod.ErroIni as e:
        return _erro(str(e))
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130


if __name__ == "__main__":
    # Permite rodar o arquivo direto: `python3 cli.py ler`. Sem isso o
    # arquivo só define main() e sai em silêncio, sem imprimir nada.
    sys.exit(main())
