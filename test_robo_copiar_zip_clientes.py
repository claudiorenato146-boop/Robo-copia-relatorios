from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

import robo_copiar_zip_clientes as robo  # noqa: E402


def escrever_arquivo(caminho: Path, conteudo: bytes) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(conteudo)
    return caminho


class RoboCopiarZipClientesTests(unittest.TestCase):
    def test_interpreta_competencia_e_candidatos(self) -> None:
        competencia = robo.interpretar_competencia("07.2026")
        self.assertEqual("072026", competencia.compacta)
        self.assertIn("07.2026", competencia.candidatos)
        self.assertIn("07-2026", competencia.candidatos)
        with self.assertRaises(robo.ErroConfiguracao):
            robo.interpretar_competencia("7.2026")

    def test_localiza_pasta_fiscal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            cliente = raiz / "5-VOGLER"
            (cliente / "1 - DOCUMENTOS").mkdir(parents=True)
            (cliente / "2 - DEPTO FISCAL").mkdir()
            encontrado = robo.localizar_pasta_fiscal(cliente)
            self.assertIsNotNone(encontrado)
            self.assertEqual("2 - DEPTO FISCAL", encontrado.name)

    def test_copia_para_pasta_existente_de_competencia(self) -> None:
        competencia = robo.interpretar_competencia("07.2026")
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "origem"
            destino = raiz / "destino"
            arquivo_origem = escrever_arquivo(
                origem / "5-CONSULTORIA PLACEHOLDER" / "072026" / "nfses_emitidas_99900006000182.xlsx",
                b"excel-a",
            )
            cliente_destino = destino / "5 - CONSULTORIA PLACEHOLDER"
            pasta_competencia = cliente_destino / "2 - DEPTO FISCAL" / "2026" / "072026"
            pasta_competencia.mkdir(parents=True)

            resultado = robo.copiar_um_arquivo(
                robo.ArquivoOrigem(
                    codigo="5",
                    nome_cliente_origem="CONSULTORIA PLACEHOLDER",
                    pasta_cliente_origem=origem / "5-CONSULTORIA PLACEHOLDER",
                    pasta_competencia_origem=origem / "5-CONSULTORIA PLACEHOLDER" / "072026",
                    arquivo_origem=arquivo_origem,
                    cnpj="99900006000182",
                ),
                robo.PastaCliente(
                    codigo="5",
                    nome="CONSULTORIA PLACEHOLDER",
                    caminho=cliente_destino,
                ),
                competencia,
                simular=False,
            )

            self.assertEqual("COPIADO", resultado.status)
            self.assertTrue((pasta_competencia / arquivo_origem.name).exists())

    def test_usa_formato_padrao_quando_competencia_nao_existe(self) -> None:
        competencia = robo.interpretar_competencia("07.2026")
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "origem"
            destino = raiz / "destino"
            arquivo_origem = escrever_arquivo(
                origem / "5-CONSULTORIA PLACEHOLDER" / "072026" / "nfses_recebidas_99900006000182.xlsx",
                b"excel-b",
            )
            cliente_destino = destino / "5 - CONSULTORIA PLACEHOLDER"
            (cliente_destino / "DEPTO FISCAL" / "2026").mkdir(parents=True)

            resultado = robo.copiar_um_arquivo(
                robo.ArquivoOrigem(
                    codigo="5",
                    nome_cliente_origem="CONSULTORIA PLACEHOLDER",
                    pasta_cliente_origem=origem / "5-CONSULTORIA PLACEHOLDER",
                    pasta_competencia_origem=origem / "5-CONSULTORIA PLACEHOLDER" / "072026",
                    arquivo_origem=arquivo_origem,
                    cnpj="99900006000182",
                ),
                robo.PastaCliente(
                    codigo="5",
                    nome="CONSULTORIA PLACEHOLDER",
                    caminho=cliente_destino,
                ),
                competencia,
                simular=False,
            )

            self.assertEqual("COPIADO", resultado.status)
            self.assertTrue(
                (cliente_destino / "DEPTO FISCAL" / "2026" / "07.2026" / arquivo_origem.name).exists()
            )

    def test_nao_sobrescreve_arquivo_com_nome_igual_e_conteudo_diferente(self) -> None:
        competencia = robo.interpretar_competencia("07.2026")
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "origem"
            destino = raiz / "destino"
            arquivo_origem = escrever_arquivo(
                origem / "5-CONSULTORIA PLACEHOLDER" / "072026" / "nfses_emitidas_99900006000182.xlsx",
                b"excel-a",
            )
            cliente_destino = destino / "5 - CONSULTORIA PLACEHOLDER"
            pasta_competencia = cliente_destino / "DEPTO FISCAL" / "2026" / "07.2026"
            pasta_competencia.mkdir(parents=True)
            escrever_arquivo(pasta_competencia / arquivo_origem.name, b"excel-b")

            resultado = robo.copiar_um_arquivo(
                robo.ArquivoOrigem(
                    codigo="5",
                    nome_cliente_origem="CONSULTORIA PLACEHOLDER",
                    pasta_cliente_origem=origem / "5-CONSULTORIA PLACEHOLDER",
                    pasta_competencia_origem=origem / "5-CONSULTORIA PLACEHOLDER" / "072026",
                    arquivo_origem=arquivo_origem,
                    cnpj="99900006000182",
                ),
                robo.PastaCliente(
                    codigo="5",
                    nome="CONSULTORIA PLACEHOLDER",
                    caminho=cliente_destino,
                ),
                competencia,
                simular=False,
            )

            self.assertEqual("PENDENCIA_CONFLITO_NOME", resultado.status)

    def test_coleta_excel_em_subpasta_da_competencia(self) -> None:
        competencia = robo.interpretar_competencia("07.2026")
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origem = raiz / "origem"
            escrever_arquivo(
                origem
                / "5-CONSULTORIA PLACEHOLDER"
                / "072026"
                / "subpasta"
                / "nfses_emitidas_99900006000182.xlsx",
                b"excel-a",
            )

            arquivos, resultados = robo.coletar_arquivos_origem(origem, competencia)

            self.assertEqual(1, len(arquivos))
            self.assertEqual("99900006000182", arquivos[0].cnpj)
            self.assertFalse(resultados)

class TesteCaminhosObrigatorios(unittest.TestCase):
    """Origem e destino nao tem valor embutido no codigo.

    Path("") vira ".", entao os padroes ficam como str: se virassem Path, a
    checagem passaria e o robo varreria a pasta atual sem avisar.
    """

    def test_sem_origem_o_robo_para_dizendo_o_que_falta(self) -> None:
        with mock.patch.object(robo, "ORIGEM_PADRAO", ""),              mock.patch.object(robo, "DESTINO_PADRAO", "/qualquer"):
            saida = io.StringIO()
            with contextlib.redirect_stdout(saida):
                codigo = robo.main(["--competencia", "07.2026"])
        self.assertEqual(1, codigo)
        self.assertIn("ROBO_ISS_ORIGEM", saida.getvalue())
        self.assertIn("--origem", saida.getvalue())

    def test_sem_destino_o_robo_para_dizendo_o_que_falta(self) -> None:
        with mock.patch.object(robo, "ORIGEM_PADRAO", "/qualquer"),              mock.patch.object(robo, "DESTINO_PADRAO", ""):
            saida = io.StringIO()
            with contextlib.redirect_stdout(saida):
                codigo = robo.main(["--competencia", "07.2026"])
        self.assertEqual(1, codigo)
        self.assertIn("ROBO_ISS_DESTINO", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
