"""Transporte USB do Redragon M711: a libusb-1.0 via ctypes.

Por que ctypes e nao hidraw: o M711 **nao expoe HID Feature Reports** — o
descritor de relatorio dele so tem Input e Output, entao nao existe ioctl de
feature report para mandar (e' por isso que a tentativa via hidraw, prova-
transporte.py, morreu em EINVAL). O M711 tem tres interfaces: 1.0 e 1.1 sao
boot/protocol, e a **1.2** e' a de vendor. Toda a conversa de configuracao
passa por control transfers vendor na interface 2 da libusb (`0x02` e' o indice
de interface, e o mesmo numero vai no wIndex).

O protocolo e' pedido/resposta: para cada bloco de dado a gente manda um SET
com o quadro de leitura (`0x21, 0x09, wValue = reportID | (reportType << 8)`,
`wIndex = 2`) e so entao le o dado no GET (`0xA1, 0x01`, mesmo wValue/wIndex).

**Quirk medido no M711**: numa leitura o mouse responde com `8 + count` bytes,
e nao com o `wLength` pedido — quem manda o `count` e' o quadro de SET, nao a
reserva do buffer. O `get()` devolve exatamente o que veio (sem completar com
zero, como o driver C++ faz) e o `get_dados()` recorta os `count` bytes uteis
depois de conferir o cabecalho. Sem root da para enumerar (`descobrir()`); para
abrir o no e preciso de permissao de escrita, que o `../fix-usb.sh` instala.
"""
import contextlib
import ctypes
import threading

CTRL_SET, CTRL_GET = 0x21, 0xA1       # bmRequestType: vendor, device->host
REQ_SET, REQ_GET = 0x09, 0x01         # bRequest
WINDEX = 0x0002                       # interface vendor do firmware (hidraw :1.2)
TIMEOUT_MS = 1000

REPORT_2, REPORT_3 = 0x0302, 0x0303   # wValue = reportID | (reportType << 8)

TAMANHO_CABECALHO = 8   # cabecalho que o mouse manda antes dos dados
CODIGO_CABECALHO = 0x08  # byte [1] do cabecalho: marca resposta de leitura

# codigos de erro do libusb que a gente trata pelo nome
ERRO_ACCESS = -3   # LIBUSB_ERROR_ACCESS: sem permissao no no /dev/bus/usb/...
ERRO_BUSY = -6     # LIBUSB_ERROR_BUSY: interface em uso
ERRO_NOT_FOUND = -5  # LIBUSB_ERROR_NOT_FOUND

_ERROS_NOME = {
    ERRO_ACCESS: "sem permissao no no do USB (rode ./fix-usb.sh)",
    ERRO_BUSY: "interface ocupada",
    ERRO_NOT_FOUND: "nao encontrado",
}


class ErroUsb(Exception):
    """Erro de transporte. `.codigo` e' o codigo do libusb (ou None)."""

    def __init__(self, mensagem: str, codigo: int | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


# --------------------------------------------------------------------------
# ctypes: structs e assinaturas da libusb
# --------------------------------------------------------------------------
class _Descritor(ctypes.Structure):
    """libusb_device_descriptor — os 18 bytes do descritor de dispositivo."""
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8),
    ]


class _Versao(ctypes.Structure):
    """libusb_version, so os tres numeros que a gente mostra."""
    _fields_ = [
        ("major", ctypes.c_uint16),
        ("minor", ctypes.c_uint16),
        ("micro", ctypes.c_uint16),
        ("nano", ctypes.c_uint16),
        ("rc", ctypes.c_char_p),
        ("describe", ctypes.c_int),
    ]


def _declara(lib: ctypes.CDLL) -> None:
    """Declara argtypes/restype. Sem isso o ctypes truncaria ponteiro em 32 bits."""
    vazio = ctypes.c_void_p
    lista = ctypes.POINTER(ctypes.c_void_p)

    lib.libusb_get_version.argtypes = []
    lib.libusb_get_version.restype = ctypes.POINTER(_Versao)

    lib.libusb_init.argtypes = [vazio]
    lib.libusb_init.restype = ctypes.c_int

    lib.libusb_get_device_list.argtypes = [vazio, ctypes.POINTER(lista)]
    lib.libusb_get_device_list.restype = ctypes.c_ssize_t
    lib.libusb_free_device_list.argtypes = [lista, ctypes.c_int]

    lib.libusb_get_device_descriptor.argtypes = [vazio, ctypes.POINTER(_Descritor)]
    lib.libusb_get_bus_number.argtypes = [vazio]
    lib.libusb_get_bus_number.restype = ctypes.c_uint8
    lib.libusb_get_device_address.argtypes = [vazio]
    lib.libusb_get_device_address.restype = ctypes.c_uint8

    lib.libusb_open.argtypes = [vazio, ctypes.POINTER(vazio)]
    lib.libusb_close.argtypes = [vazio]
    lib.libusb_claim_interface.argtypes = [vazio, ctypes.c_int]
    lib.libusb_release_interface.argtypes = [vazio, ctypes.c_int]
    lib.libusb_kernel_driver_active.argtypes = [vazio, ctypes.c_int]
    lib.libusb_kernel_driver_active.restype = ctypes.c_int
    lib.libusb_detach_kernel_driver.argtypes = [vazio, ctypes.c_int]
    lib.libusb_attach_kernel_driver.argtypes = [vazio, ctypes.c_int]

    lib.libusb_control_transfer.argtypes = [
        vazio,                       # device_handle
        ctypes.c_uint8,              # bmRequestType
        ctypes.c_uint8,              # bRequest
        ctypes.c_uint16,             # wValue
        ctypes.c_uint16,             # wIndex
        vazio,                       # dados
        ctypes.c_uint16,             # wLength
        ctypes.c_uint,               # timeout (ms)
    ]
    lib.libusb_control_transfer.restype = ctypes.c_int

    lib.libusb_error_name.argtypes = [ctypes.c_int]
    lib.libusb_error_name.restype = ctypes.c_char_p


class _Contexto:
    """Estado global da libusb no processo: a lib carregada e o ctx do init."""

    def __init__(self) -> None:
        self.trava = threading.Lock()
        self.lib: ctypes.CDLL | None = None
        self.iniciado = False


_CTX = _Contexto()


def _abre() -> ctypes.CDLL | None:
    """Carrega a libusb. Devolve None se ela nao estiver instalada."""
    for nome in ("libusb-1.0.so.0", "libusb-1.0.so"):
        try:
            lib = ctypes.CDLL(nome)
        except OSError:
            continue
        _declara(lib)
        return lib
    return None


def _libusb() -> ctypes.CDLL:
    """Devolve a lib, carregando e fazendo o libusb_init uma vez so.

    Nunca chamamos libusb_exit: dois `Dispositivo` podem conviver no mesmo
    processo e o processo vive curto.
    """
    with _CTX.trava:
        if _CTX.lib is None:
            _CTX.lib = _abre()
        if _CTX.lib is None:
            raise ErroUsb(
                "libusb-1.0 nao encontrada. Instale o pacote dela:\n"
                "  Debian/Ubuntu: sudo apt install libusb-1.0-0\n"
                "  Fedora/Arch:   sudo dnf install libusb  |  sudo pacman -S libusb")
        if not _CTX.iniciado:
            if _CTX.lib.libusb_init(None) < 0:
                raise ErroUsb("libusb_init falhou")
            _CTX.iniciado = True
        return _CTX.lib


# --------------------------------------------------------------------------
# mensagens de erro
# --------------------------------------------------------------------------
def _hex(quadro: bytes) -> str:
    """'02 08 2c 00 01 00 fa fa' — para mensagem de erro."""
    return " ".join(f"{b:02x}" for b in quadro)


def _nome_erro(lib: ctypes.CDLL, codigo: int) -> str:
    """'LIBUSB_ERROR_ACCESS' mais um palpite em portugues."""
    bruto = lib.libusb_error_name(codigo)
    nome = bruto.decode() if bruto else f"erro {codigo}"
    return f"{nome} ({codigo}) — {_ERROS_NOME.get(codigo, 'veja a libusb')}"


def _falha(acao: str, codigo: int, extra: str = "") -> ErroUsb:
    """ErroUsb de uma chamada que devolveu codigo negativo."""
    return ErroUsb(f"{_nome_erro(_CTX.lib, codigo)}: {acao}{extra}", codigo)


# --------------------------------------------------------------------------
# enumeracao
# --------------------------------------------------------------------------
def _casados(lib: ctypes.CDLL, vid: int, pid: int):
    """Gera (id, caminho, device*) dos dispositivos que casam com vid/pid.

    A lista de dispositivos vive enquanto o gerador vive: use sempre dentro de
    `with contextlib.closing(_casados(...))`, que libera a lista ao fechar. O
    device* morre com a lista, mas o `libusb_open` referencia o device, entao da
    para abrir e usar o handle depois.
    """
    lista = ctypes.POINTER(ctypes.c_void_p)()
    try:
        total = lib.libusb_get_device_list(None, ctypes.byref(lista))
        if total < 0:
            raise _falha("libusb_get_device_list", total)
        for i in range(total):
            desc = _Descritor()
            if lib.libusb_get_device_descriptor(lista[i], ctypes.byref(desc)) != 0:
                continue
            if desc.idVendor != vid or desc.idProduct != pid:
                continue
            bus = lib.libusb_get_bus_number(lista[i])
            end = lib.libusb_get_device_address(lista[i])
            yield (vid, pid, bus, end), f"/dev/bus/usb/{bus:03d}/{end:03d}", lista[i]
    finally:
        lib.libusb_free_device_list(lista, 1)


def descobrir(vid: int, pid: int) -> list[tuple[tuple[int, int, int, int], str]]:
    """Todos os (vid,pid) na USB: `[(id, caminho), ...]`, em ordem de bus/addr.

    So' enumera — nao abre nada, entao funciona sem permissao de root.
    """
    lib = _libusb()
    with contextlib.closing(_casados(lib, vid, pid)) as casados:
        achados = [(ident, caminho) for ident, caminho, _ in casados]
    achados.sort(key=lambda par: (par[0][2], par[0][3]))
    return achados


# ARQUITETURA.md secao 3 escreve `discovering`; o nome certo do projeto e' este.
discovering = descobrir


def versao_libusb() -> str | None:
    """'1.0.27' se a lib carregou, None se ela nao esta instalada."""
    with _CTX.trava:
        if _CTX.lib is None:
            _CTX.lib = _abre()
        lib = _CTX.lib
    if lib is None:
        return None
    v = lib.libusb_get_version().contents
    return f"{v.major}.{v.minor}.{v.micro}"


# --------------------------------------------------------------------------
# o dispositivo
# --------------------------------------------------------------------------
class Dispositivo:
    """Um M711 aberto na interface vendor. Use como context manager."""

    def __init__(self, vid: int = 0x04D9, pid: int = 0xFC30, interface: int = 2) -> None:
        self._vid, self._pid, self._interface = vid, pid, interface
        self._id: tuple[int, int, int, int] | None = None
        self._caminho: str | None = None
        self._handle = ctypes.c_void_p()
        self._soltou_driver = False

    # -- identificacao -------------------------------------------------
    @property
    def id(self) -> tuple[int, int, int, int] | None:
        """(vid, pid, bus, address) — preenchido por `abrir()`."""
        return self._id

    @property
    def caminho(self) -> str | None:
        """'/dev/bus/usb/001/003' — preenchido por `abrir()`."""
        return self._caminho

    # -- abrir / fechar ------------------------------------------------
    def abrir(self) -> None:
        """Acha o primeiro dispositivo que casa, abre e reivindica a interface.

        Levanta ErroUsb em qualquer falha. Mesmo depois de uma falha o objeto
        fica limpo: pode chamar `abrir()` de novo ou `fechar()` sem efeito.
        """
        if self._handle:
            return
        lib = _libusb()  # levanta ErroUsb com instrucao de instalacao
        with contextlib.closing(_casados(lib, self._vid, self._pid)) as casados:
            for ident, caminho, device in casados:
                rc = lib.libusb_open(device, ctypes.byref(self._handle))
                if rc == 0:
                    self._id, self._caminho = ident, caminho
                    break
                extra = ""
                if rc == ERRO_ACCESS:
                    extra = (f"\nO libusb abre {caminho} em O_RDWR e o seu usuario"
                             " nao tem permissao de escrita nesse no.\n"
                             "Rode o script ./fix-usb.sh na raiz do projeto: ele"
                             " instala uma\nregra udev que da MODE=0660 + uaccess"
                             " para o M711 (e' preciso de sudo).")
                raise _falha(f"libusb_open({caminho})", rc, extra)
            else:
                raise ErroUsb(
                    f"nenhum dispositivo {self._vid:04x}:{self._pid:04x} na USB — "
                    f"o M711 esta plugado?")

        rc = lib.libusb_claim_interface(self._handle, self._interface)
        if rc == ERRO_BUSY:
            if not lib.libusb_kernel_driver_active(self._handle, self._interface):
                self.fechar()
                raise _falha(
                    f"a interface {self._interface} esta ocupada por outro processo "
                    f"(hidraw aberto no M711)", rc)
            rc = lib.libusb_detach_kernel_driver(self._handle, self._interface)
            if rc != 0:
                self.fechar()
                raise _falha(
                    f"libusb_detach_kernel_driver({self._interface})", rc)
            self._soltou_driver = True   # `fechar()` reanexa
            rc = lib.libusb_claim_interface(self._handle, self._interface)
        if rc != 0:
            self.fechar()
            raise _falha(f"libusb_claim_interface({self._interface})", rc)

    def fechar(self) -> None:
        """Reanexa o driver do kernel, libera a interface e fecha.

        Idempotente e seguro mesmo se `abrir()` falhou no meio do caminho.
        """
        if not self._handle:
            return
        handle, self._handle = self._handle, ctypes.c_void_p()
        lib = _libusb()
        if self._soltou_driver:
            self._soltou_driver = False
            lib.libusb_release_interface(handle, self._interface)
            lib.libusb_attach_kernel_driver(handle, self._interface)
        else:
            lib.libusb_release_interface(handle, self._interface)
        lib.libusb_close(handle)

    def __enter__(self) -> "Dispositivo":
        self.abrir()
        return self

    def __exit__(self, *_) -> None:
        self.fechar()

    # -- transporte bruto ------------------------------------------------
    def _exige_aberto(self) -> ctypes.CDLL:
        if not self._handle:
            raise ErroUsb(f"dispositivo {self._caminho or '04d9:fc30'} nao esta "
                          "aberto (chame abrir())")
        return _libusb()

    def set(self, quadro: bytes, wvalue: int, tamanho: int,
            timeout: int = TIMEOUT_MS) -> int:
        """Manda um SET vendor. Devolve quantos bytes o mouse aceitou."""
        lib = self._exige_aberto()
        dados = bytes(quadro)
        if tamanho != len(dados):
            raise ErroUsb(f"SET wValue=0x{wvalue:04x}: o quadro tem {len(dados)}"
                          f" bytes mas o tamanho pedido e' {tamanho}")
        # create_string_buffer, e nao bytes direto: o quadro tem zero no meio
        buf = ctypes.create_string_buffer(dados, tamanho)
        rc = lib.libusb_control_transfer(
            self._handle, CTRL_SET, REQ_SET, wvalue, WINDEX, buf, tamanho, timeout)
        if rc < 0:
            raise _falha(f"SET wValue=0x{wvalue:04x}", rc)
        return rc

    def get(self, wvalue: int, tamanho: int,
            timeout: int = TIMEOUT_MS) -> bytes:
        """Faz um GET vendor e devolve **exatamente** o que o mouse devolveu.

        O M711 responde com `8 + count` bytes, nao com o `tamanho` pedido
        (quem manda o count e' o quadro de SET), entao aqui nao ha padding:
        quem chamou com 16 e' so' que o SET pedia 1 byte vai receber 9.
        """
        lib = self._exige_aberto()
        buf = ctypes.create_string_buffer(tamanho)
        rc = lib.libusb_control_transfer(
            self._handle, CTRL_GET, REQ_GET, wvalue, WINDEX, buf, tamanho, timeout)
        if rc < 0:
            raise _falha(f"GET wValue=0x{wvalue:04x} (pedindo {tamanho} bytes)", rc)
        return buf.raw[:rc]

    def get_dados(self, wvalue: int, count: int,
                  timeout: int = TIMEOUT_MS) -> bytes:
        """GET de leitura: valida o cabecalho e devolve so' os `count` bytes.

        O cabecalho esperado e' `02|03 08 <addr_lo> <addr_hi> <count> 00
        fa fa` e os dados comecam no byte 8. Medido no M711: o byte alto
        do endereco vem 0x60 (o firmware soma 0x6000), entao a conferencia
        e' so' da marca 0x08, do byte baixo do endereco e do count.
        """
        bruto = self.get(wvalue, TAMANHO_CABECALHO + count, timeout)
        if len(bruto) < TAMANHO_CABECALHO or bruto[1] != CODIGO_CABECALHO:
            raise ErroUsb(
                f"GET wValue=0x{wvalue:04x}: cabecalho invalido (esperado "
                f"{CODIGO_CABECALHO:02x} em [1]), o mouse devolveu "
                f"{len(bruto)} bytes: {_hex(bruto)}")
        if bruto[4] != count:
            raise ErroUsb(
                f"GET wValue=0x{wvalue:04x}: o cabecalho anuncia count="
                f"{bruto[4]} mas o pedido e' de {count} bytes: {_hex(bruto)}")
        dados = bruto[TAMANHO_CABECALHO:TAMANHO_CABECALHO + count]
        if len(dados) < count:
            raise ErroUsb(
                f"GET wValue=0x{wvalue:04x}: o cabecalho anuncia count="
                f"{bruto[4]} (endereco 0x{bruto[2] | (bruto[3] << 8):04x}) mas "
                f"pedi {count} bytes e vieram {len(bruto)}: {_hex(bruto)}")
        return dados
