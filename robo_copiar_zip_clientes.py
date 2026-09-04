from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


VERSAO = "1.1.0"
PADRAO_COMPETENCIA = re.compile(r"^(0[1-9]|1[0-2])\.(\d{4})$")
PADRAO_CODIGO = re.compile(r"^\s*(\d+)\s*[- ]+\s*(.+?)\s*$")
PADRAO_CNPJ = re.compile(r"(\d{14})")
NOME_ARQUIVO_LOG = "log_execucao.txt"
EXTENSOES_PERMITIDAS = {".xlsx", ".xls"}

# Os caminhos nao tem valor embutido: cada escritorio monta a rede de um jeito,
# e um padrao chumbado no codigo so serve para quem escreveu. Defina
# ROBO_ISS_ORIGEM e ROBO_ISS_DESTINO no ambiente, ou passe --origem e --destino
# na linha de comando (que tem prioridade). Sem nenhum dos dois, o robo para
# com mensagem clara em vez de procurar numa pasta que nao existe.
# Ficam como str, e nao como Path: Path("") vira ".", que passaria pela
# checagem de "informou?" e faria o robo varrer a pasta atual sem avisar.
ORIGEM_PADRAO = os.getenv("ROBO_ISS_ORIGEM", "")
DESTINO_PADRAO = os.getenv("ROBO_ISS_DESTINO", "")

logger = logging.getLogger("robo_copia_iss_clientes")


class ErroConfiguracao(RuntimeError):
    pass


@dataclass(frozen=True)
class Competencia:
    exibicao: str
    compacta: str
    ano: str
    mes: str

    @property
    def candidatos(self) -> tuple[str, ...]:
        return (
            self.exibicao,
            self.compacta,
            f"{self.mes}-{self.ano}",
            f"{self.mes}_{self.ano}",
        )


@dataclass(frozen=True)
class PastaCliente:
    codigo: str
    nome: str
    caminho: Path


@dataclass(frozen=True)
class ArquivoOrigem:
    codigo: str
    nome_cliente_origem: str
    pasta_cliente_origem: Path
    pasta_competencia_origem: Path
    arquivo_origem: Path
    cnpj: str


@dataclass(frozen=True)
class ResultadoCopia:
    codigo: str
    cliente_origem: str
    cnpj: str
    arquivo: str
    status: str
    detalhe: str
    origem: str
    destino: str


def normalizar_texto(valor: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    return sem_acentos.casefold()


def interpretar_competencia(valor: str) -> Competencia:
    texto = valor.strip()
    correspondencia = PADRAO_COMPETENCIA.fullmatch(texto)
    if not correspondencia:
        raise ErroConfiguracao(
            f"Competencia invalida: '{valor}'. Use MM.AAAA, por exemplo 07.2026."
        )
    mes, ano = correspondencia.groups()
    return Competencia(exibicao=texto, compacta=f"{mes}{ano}", ano=ano, mes=mes)


def extrair_codigo(nome_pasta: str) -> tuple[str, str] | None:
    correspondencia = PADRAO_CODIGO.match(nome_pasta)
    if not correspondencia:
        return None
    return correspondencia.group(1).lstrip("0") or "0", correspondencia.group(2).strip()


def extrair_cnpj(nome_arquivo: str) -> str:
    encontrados = PADRAO_CNPJ.findall(nome_arquivo)
    if not encontrados:
        return ""
    return encontrados[-1]


def sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def garantir_pasta(caminho: Path) -> None:
    caminho.mkdir(parents=True, exist_ok=True)


def localizar_pasta_competencia(base_ano: Path, competencia: Competencia) -> Path:
    for nome in competencia.candidatos:
        candidata = base_ano / nome
        if candidata.is_dir():
            return candidata
    return base_ano / competencia.exibicao


def localizar_pasta_fiscal(pasta_cliente: Path) -> Path | None:
    candidatos: list[Path] = []
    for item in pasta_cliente.iterdir():
        if not item.is_dir():
            continue
        if "fiscal" in normalizar_texto(item.name):
            candidatos.append(item)

    if not candidatos:
        return None
    if len(candidatos) == 1:
        return candidatos[0]

    def prioridade(caminho: Path) -> tuple[int, int, str]:
        nome = normalizar_texto(caminho.name)
        if nome.strip() == "depto fiscal":
            nivel = 0
        elif "depto fiscal" in nome:
            nivel = 1
        elif nome.endswith("fiscal"):
            nivel = 2
        else:
            nivel = 3
        return (nivel, len(caminho.name), caminho.name)

    ordenados = sorted(candidatos, key=prioridade)
    melhor = ordenados[0]
    if len(ordenados) >= 2 and prioridade(ordenados[0]) == prioridade(ordenados[1]):
        return None
    return melhor


def mapear_clientes_destino(raiz_destino: Path) -> tuple[dict[str, PastaCliente], list[ResultadoCopia]]:
    mapa: dict[str, PastaCliente] = {}
    pendencias: list[ResultadoCopia] = []
    duplicados: set[str] = set()

    for item in sorted(raiz_destino.iterdir(), key=lambda p: p.name):
        if not item.is_dir():
            continue
        extraido = extrair_codigo(item.name)
        if not extraido:
            continue
        codigo, nome = extraido
        if codigo in mapa:
            duplicados.add(codigo)
            continue
        mapa[codigo] = PastaCliente(codigo=codigo, nome=nome, caminho=item)

    for codigo in duplicados:
        antigo = mapa.pop(codigo, None)
        detalhe = "Mais de uma pasta de cliente com o mesmo codigo no destino."
        pendencias.append(
            ResultadoCopia(
                codigo=codigo,
                cliente_origem=antigo.nome if antigo else "",
                cnpj="",
                arquivo="",
                status="PENDENCIA_DESTINO_DUPLICADO",
                detalhe=detalhe,
                origem="",
                destino=str(raiz_destino),
            )
        )
    return mapa, pendencias


def coletar_arquivos_origem(
    raiz_origem: Path, competencia: Competencia
) -> tuple[list[ArquivoOrigem], list[ResultadoCopia]]:
    arquivos: list[ArquivoOrigem] = []
    resultados: list[ResultadoCopia] = []

    for pasta_cliente in sorted(raiz_origem.iterdir(), key=lambda p: p.name):
        if not pasta_cliente.is_dir():
            continue
        extraido = extrair_codigo(pasta_cliente.name)
        if not extraido:
            continue
        codigo, nome = extraido
        pasta_competencia = pasta_cliente / competencia.compacta
        if not pasta_competencia.is_dir():
            resultados.append(
                ResultadoCopia(
                    codigo=codigo,
                    cliente_origem=nome,
                    cnpj="",
                    arquivo="",
                    status="SEM_PASTA_COMPETENCIA",
                    detalhe=f"Cliente sem pasta {competencia.compacta} na origem.",
                    origem=str(pasta_cliente),
                    destino="",
                )
            )
            continue

        arquivos_encontrados = sorted(
            arquivo
            for arquivo in pasta_competencia.rglob("*")
            if arquivo.is_file() and arquivo.suffix.lower() in EXTENSOES_PERMITIDAS
        )
        if not arquivos_encontrados:
            resultados.append(
                ResultadoCopia(
                    codigo=codigo,
                    cliente_origem=nome,
                    cnpj="",
                    arquivo="",
                    status="SEM_ARQUIVO_EXCEL_NA_ORIGEM",
                    detalhe="A pasta da competencia existe, mas nao contem arquivos Excel nem em subpastas.",
                    origem=str(pasta_competencia),
                    destino="",
                )
            )
            continue

        for arquivo_origem in arquivos_encontrados:
            cnpj = extrair_cnpj(arquivo_origem.name)
            if not cnpj:
                resultados.append(
                    ResultadoCopia(
                        codigo=codigo,
                        cliente_origem=nome,
                        cnpj="",
                        arquivo=arquivo_origem.name,
                        status="PENDENCIA_SEM_CNPJ_NO_ARQUIVO",
                        detalhe="Nao foi encontrado CNPJ de 14 digitos no nome do arquivo.",
                        origem=str(arquivo_origem),
                        destino="",
                    )
                )
                continue

            arquivos.append(
                ArquivoOrigem(
                    codigo=codigo,
                    nome_cliente_origem=nome,
                    pasta_cliente_origem=pasta_cliente,
                    pasta_competencia_origem=pasta_competencia,
                    arquivo_origem=arquivo_origem,
                    cnpj=cnpj,
                )
            )

    return arquivos, resultados


def copiar_um_arquivo(
    origem: ArquivoOrigem,
    cliente_destino: PastaCliente,
    competencia: Competencia,
    simular: bool,
) -> ResultadoCopia:
    try:
        pasta_fiscal = localizar_pasta_fiscal(cliente_destino.caminho)
    except OSError as erro:
        return ResultadoCopia(
            codigo=origem.codigo,
            cliente_origem=origem.nome_cliente_origem,
            cnpj=origem.cnpj,
            arquivo=origem.arquivo_origem.name,
            status="PENDENCIA_ERRO_LENDO_CLIENTE",
            detalhe=str(erro),
            origem=str(origem.arquivo_origem),
            destino=str(cliente_destino.caminho),
        )

    if pasta_fiscal is None:
        return ResultadoCopia(
            codigo=origem.codigo,
            cliente_origem=origem.nome_cliente_origem,
            cnpj=origem.cnpj,
            arquivo=origem.arquivo_origem.name,
            status="PENDENCIA_SEM_PASTA_FISCAL",
            detalhe="Nenhuma pasta contendo a palavra 'fiscal' foi encontrada com seguranca.",
            origem=str(origem.arquivo_origem),
            destino=str(cliente_destino.caminho),
        )

    pasta_ano = pasta_fiscal / competencia.ano
    pasta_competencia = localizar_pasta_competencia(pasta_ano, competencia)
    arquivo_destino = pasta_competencia / origem.arquivo_origem.name

    if arquivo_destino.exists():
        try:
            if (
                origem.arquivo_origem.stat().st_size == arquivo_destino.stat().st_size
                and sha256_arquivo(origem.arquivo_origem) == sha256_arquivo(arquivo_destino)
            ):
                return ResultadoCopia(
                    codigo=origem.codigo,
                    cliente_origem=origem.nome_cliente_origem,
                    cnpj=origem.cnpj,
                    arquivo=origem.arquivo_origem.name,
                    status="JA_EXISTENTE",
                    detalhe="Arquivo identico ja existia no destino.",
                    origem=str(origem.arquivo_origem),
                    destino=str(arquivo_destino),
                )
        except OSError as erro:
            return ResultadoCopia(
                codigo=origem.codigo,
                cliente_origem=origem.nome_cliente_origem,
                cnpj=origem.cnpj,
                arquivo=origem.arquivo_origem.name,
                status="PENDENCIA_ERRO_COMPARANDO",
                detalhe=str(erro),
                origem=str(origem.arquivo_origem),
                destino=str(arquivo_destino),
            )
        return ResultadoCopia(
            codigo=origem.codigo,
            cliente_origem=origem.nome_cliente_origem,
            cnpj=origem.cnpj,
            arquivo=origem.arquivo_origem.name,
            status="PENDENCIA_CONFLITO_NOME",
            detalhe="Ja existe arquivo com o mesmo nome, mas com conteudo diferente.",
            origem=str(origem.arquivo_origem),
            destino=str(arquivo_destino),
        )

    if simular:
        return ResultadoCopia(
            codigo=origem.codigo,
            cliente_origem=origem.nome_cliente_origem,
            cnpj=origem.cnpj,
            arquivo=origem.arquivo_origem.name,
            status="SIMULADO",
            detalhe="Arquivo pronto para copia.",
            origem=str(origem.arquivo_origem),
            destino=str(arquivo_destino),
        )

    try:
        garantir_pasta(pasta_competencia)
        arquivo_destino.write_bytes(origem.arquivo_origem.read_bytes())
    except OSError as erro:
        return ResultadoCopia(
            codigo=origem.codigo,
            cliente_origem=origem.nome_cliente_origem,
            cnpj=origem.cnpj,
            arquivo=origem.arquivo_origem.name,
            status="PENDENCIA_ERRO_COPIA",
            detalhe=str(erro),
            origem=str(origem.arquivo_origem),
            destino=str(arquivo_destino),
        )

    return ResultadoCopia(
        codigo=origem.codigo,
        cliente_origem=origem.nome_cliente_origem,
        cnpj=origem.cnpj,
        arquivo=origem.arquivo_origem.name,
        status="COPIADO",
        detalhe="Arquivo copiado com sucesso.",
        origem=str(origem.arquivo_origem),
        destino=str(arquivo_destino),
    )


def salvar_csv(caminho: Path, resultados: Iterable[ResultadoCopia]) -> None:
    garantir_pasta(caminho.parent)
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(
            [
                "codigo",
                "cliente_origem",
                "cnpj",
                "arquivo",
                "status",
                "detalhe",
                "origem",
                "destino",
            ]
        )
        for item in resultados:
            escritor.writerow(
                [
                    item.codigo,
                    item.cliente_origem,
                    item.cnpj,
                    item.arquivo,
                    item.status,
                    item.detalhe,
                    item.origem,
                    item.destino,
                ]
            )


def configurar_log(pasta_relatorios: Path) -> None:
    garantir_pasta(pasta_relatorios)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    arquivo = logging.FileHandler(pasta_relatorios / NOME_ARQUIVO_LOG, encoding="utf-8")
    arquivo.setFormatter(formatter)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    logger.addHandler(arquivo)
    logger.addHandler(console)


def resumir(resultados: list[ResultadoCopia]) -> str:
    contagem: dict[str, int] = {}
    for item in resultados:
        contagem[item.status] = contagem.get(item.status, 0) + 1
    linhas = [f"{status}: {contagem[status]}" for status in sorted(contagem)]
    return "\n".join(linhas)


def executar(
    raiz_origem: Path,
    raiz_destino: Path,
    competencia: Competencia,
    simular: bool,
) -> tuple[list[ResultadoCopia], Path]:
    if not raiz_origem.is_dir():
        raise ErroConfiguracao(f"Pasta de origem nao encontrada: {raiz_origem}")
    if not raiz_destino.is_dir():
        raise ErroConfiguracao(f"Pasta de destino nao encontrada: {raiz_destino}")

    pasta_relatorios = Path.cwd() / f"relatorios_copia_iss_{competencia.compacta}"
    configurar_log(pasta_relatorios)
    logger.info("Versao: %s", VERSAO)
    logger.info("Origem: %s", raiz_origem)
    logger.info("Destino: %s", raiz_destino)
    logger.info("Competencia: %s", competencia.exibicao)
    logger.info("Modo simulacao: %s", "SIM" if simular else "NAO")

    mapa_destino, pre_resultados = mapear_clientes_destino(raiz_destino)
    arquivos_origem, resultados_origem = coletar_arquivos_origem(raiz_origem, competencia)

    resultados: list[ResultadoCopia] = []
    resultados.extend(pre_resultados)
    resultados.extend(resultados_origem)

    logger.info("Pastas de destino validas encontradas: %s", len(mapa_destino))
    logger.info("Arquivos Excel aptos encontrados na origem: %s", len(arquivos_origem))

    for arquivo in arquivos_origem:
        cliente_destino = mapa_destino.get(arquivo.codigo)
        if cliente_destino is None:
            resultados.append(
                ResultadoCopia(
                    codigo=arquivo.codigo,
                    cliente_origem=arquivo.nome_cliente_origem,
                    cnpj=arquivo.cnpj,
                    arquivo=arquivo.arquivo_origem.name,
                    status="PENDENCIA_CLIENTE_NAO_ENCONTRADO",
                    detalhe="Nao foi localizada pasta de cliente com o mesmo codigo no destino.",
                    origem=str(arquivo.arquivo_origem),
                    destino=str(raiz_destino),
                )
            )
            continue

        resultado = copiar_um_arquivo(arquivo, cliente_destino, competencia, simular)
        resultados.append(resultado)
        logger.info("%s | %s | %s", resultado.status, resultado.codigo, resultado.arquivo)

    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    relatorio = pasta_relatorios / f"relatorio_execucao_{competencia.compacta}_{carimbo}.csv"
    pendencias = pasta_relatorios / f"pendencias_{competencia.compacta}_{carimbo}.csv"
    salvar_csv(relatorio, resultados)
    salvar_csv(
        pendencias,
        [item for item in resultados if item.status.startswith("PENDENCIA")],
    )
    logger.info("Relatorio salvo em: %s", relatorio)
    logger.info("Pendencias salvas em: %s", pendencias)
    logger.info("Resumo:\n%s", resumir(resultados))
    return resultados, pasta_relatorios


def ler_competencia_interativa() -> Competencia:
    valor = input("Informe a competencia no formato MM.AAAA: ").strip()
    return interpretar_competencia(valor)


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copia arquivos Excel da pasta de relatorios ISS para a pasta oficial dos clientes."
    )
    parser.add_argument("--competencia", help="Competencia no formato MM.AAAA.")
    parser.add_argument("--origem", default=ORIGEM_PADRAO, help="Pasta raiz de origem.")
    parser.add_argument("--destino", default=DESTINO_PADRAO, help="Pasta raiz de destino.")
    parser.add_argument(
        "--simular",
        action="store_true",
        help="Somente analisa e gera relatorio, sem copiar arquivos.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)

    try:
        for rotulo, valor, variavel, opcao in (
            ("origem", args.origem, "ROBO_ISS_ORIGEM", "--origem"),
            ("destino", args.destino, "ROBO_ISS_DESTINO", "--destino"),
        ):
            if not valor.strip():
                raise ErroConfiguracao(
                    f"a pasta de {rotulo} nao foi informada. Defina a variavel de "
                    f"ambiente {variavel} ou passe {opcao} na linha de comando."
                )
        competencia = (
            interpretar_competencia(args.competencia)
            if args.competencia
            else ler_competencia_interativa()
        )
        resultados, pasta_relatorios = executar(
            raiz_origem=Path(args.origem),
            raiz_destino=Path(args.destino),
            competencia=competencia,
            simular=args.simular,
        )
    except ErroConfiguracao as erro:
        print(f"ERRO: {erro}")
        return 1

    pendencias = sum(1 for item in resultados if item.status.startswith("PENDENCIA"))
    copiados = sum(1 for item in resultados if item.status == "COPIADO")
    existentes = sum(1 for item in resultados if item.status == "JA_EXISTENTE")
    simulados = sum(1 for item in resultados if item.status == "SIMULADO")

    print()
    print("Resumo final")
    print(f"Competencia: {competencia.exibicao}")
    print(f"Copiados: {copiados}")
    print(f"Ja existentes: {existentes}")
    print(f"Simulados: {simulados}")
    print(f"Pendencias: {pendencias}")
    print(f"Relatorios: {pasta_relatorios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
