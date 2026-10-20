# -*- coding: utf-8 -*-
"""
test_classifier.py
------------------
Testes unitários para validação do reconhecimento de Zettlr e Xournal++,
fallbacks de processo e extração de títulos de leitura.
"""

import unittest

# Importação dos módulos do classificador e dos dashboards
from classifier import split_app_context, app_from_process, APP_DOMAIN
from dashboard import _xournal_document_name as xournal_doc_dashboard
from dashboard_live import _xournal_document_name as xournal_doc_dashboard_live


class TestClassifierAndReading(unittest.TestCase):
    """Conjunto de testes para classificador, processos e normalização de documentos."""

    def test_xournal_real_title_split(self):
        # Título real observado com '*' no início (alterações não salvas)
        real_title = (
            "*Fundamentos matemáticos para a ciência da computação "
            "Matemática Discreta e Suas Aplicações (Judith L. Gersting).pdf - Xournal++"
        )
        app, context = split_app_context(real_title)
        self.assertEqual(app, "Xournal++")
        self.assertEqual(context, real_title)

    def test_zettlr_split(self):
        # Validação do reconhecimento básico do Zettlr
        app, _ = split_app_context("Zettlr")
        self.assertEqual(app, "Zettlr")

    def test_existing_apps_remain_unchanged(self):
        # Títulos que já funcionavam continuam retornando os mesmos aplicativos
        app_yt, _ = split_app_context("Aula de Python - YouTube - Google Chrome")
        self.assertEqual(app_yt, "YouTube")

        app_val, _ = split_app_context("VALORANT")
        self.assertEqual(app_val, "Valorant")

        # Regra do rules.json mapeia 'google play livros' para 'Google Books'
        app_books, _ = split_app_context("O Programador Pragmático - Google Play Livros - Brave")
        self.assertEqual(app_books, "Google Books")

    def test_app_from_process(self):
        # Fallback de executável para Zettlr e Xournal++
        self.assertEqual(app_from_process("Zettlr.exe"), "Zettlr")
        self.assertEqual(app_from_process("zettlr.exe"), "Zettlr")
        self.assertEqual(app_from_process("xournalpp.exe"), "Xournal++")
        self.assertEqual(app_from_process("XOURNALPP.EXE"), "Xournal++")

        # Casos onde não deve retornar app (None ou app não mapeado)
        self.assertIsNone(app_from_process(None))
        self.assertIsNone(app_from_process(""))
        self.assertIsNone(app_from_process("chrome.exe"))

    def test_app_domain_mapping(self):
        # Garante que Xournal++ foi mapeado para a categoria Leitura
        self.assertEqual(APP_DOMAIN.get("Xournal++"), "Leitura")

    def test_xournal_document_name_normalization(self):
        # Testa a normalização solicitada: título real, versão sem *, versão .xopp e string vazia
        for fn in (xournal_doc_dashboard, xournal_doc_dashboard_live):
            real_title = (
                "*Fundamentos matemáticos para a ciência da computação "
                "Matemática Discreta e Suas Aplicações (Judith L. Gersting).pdf - Xournal++"
            )
            title_without_star = (
                "Fundamentos matemáticos para a ciência da computação "
                "Matemática Discreta e Suas Aplicações (Judith L. Gersting).pdf - Xournal++"
            )
            expected_name = (
                "Fundamentos matemáticos para a ciência da computação "
                "Matemática Discreta e Suas Aplicações (Judith L. Gersting)"
            )

            # Versão com '*' e sem '*' devem gerar exatamente o mesmo nome
            self.assertEqual(fn(real_title), expected_name)
            self.assertEqual(fn(title_without_star), expected_name)

            # Versão com extensão .xopp
            xopp_title = "Notas de Álgebra Linear.xopp - Xournal++"
            self.assertEqual(fn(xopp_title), "Notas de Álgebra Linear")

            # Versão com string vazia
            self.assertEqual(fn(""), "Documento sem título")
            self.assertEqual(fn("   "), "Documento sem título")

    def test_tracker_tick_fallback_process(self):
        # Testa a integração do fallback de processo no Tracker
        from unittest.mock import patch, MagicMock
        from datetime import datetime
        from tracker import Tracker

        mock_store = MagicMock()
        mock_sensors = MagicMock()
        mock_sensors.idle_seconds.return_value = 0.0
        mock_sensors.is_audio_app.return_value = False
        mock_sensors.active_audio_process.return_value = None

        tracker = Tracker(mock_store, mock_sensors)

        # Caso 1: Janela desconhecida ("Outros"), mas processo é xournalpp.exe
        mock_sensors.active_window.return_value = "Janela sem nome do app"
        mock_sensors.split.return_value = ("Outros", "Janela sem nome do app")

        with patch("tracker.get_active_process_name", return_value="xournalpp.exe"):
            tracker.tick(datetime(2026, 10, 19, 10, 0, 0))
            # O stream de tela deve ter registrado Xournal++
            self.assertEqual(tracker.screen.key, ("Xournal++", "Janela sem nome do app"))

        # Caso 2: Janela com app conhecido ("VS Code"), não deve ser sobrescrita pelo processo
        mock_sensors.active_window.return_value = "main.py - Visual Studio Code"
        mock_sensors.split.return_value = ("VS Code", "main.py - Visual Studio Code")

        with patch("tracker.get_active_process_name", return_value="xournalpp.exe"):
            tracker.tick(datetime(2026, 10, 19, 10, 0, 5))
            self.assertEqual(tracker.screen.key, ("VS Code", "main.py - Visual Studio Code"))

    def test_reading_aggregation_with_xournal(self):
        # Valida se fetch_reading_data do dashboard.py e dashboard_live.py calcula o tempo do livro Xournal++
        from dashboard import fetch_reading_data as fetch_reading_dash
        from dashboard_live import fetch_reading_data as fetch_reading_live

        rows = [
            ("screen", "Xournal++", "*Livro de Matematica.pdf - Xournal++", "2026-10-19T10:00:00"),
            ("screen", "Xournal++", "Livro de Matematica.pdf - Xournal++", "2026-10-19T10:05:00"),
            ("screen", "Outros", "Desktop", "2026-10-19T10:05:30"),
        ]

        # 1. Testando dashboard.py
        by_day, book_times, total = fetch_reading_dash(rows)
        self.assertIn("Livro de Matematica", book_times)
        self.assertEqual(book_times["Livro de Matematica"], 300.0)
        self.assertEqual(total, 300.0)
        self.assertEqual(by_day["2026-10-19"], 300.0)

        # 2. Testando dashboard_live.py
        live_by_day, live_book_times, live_total = fetch_reading_live(rows)
        self.assertIn("Livro de Matematica", live_book_times)
        self.assertEqual(live_book_times["Livro de Matematica"], 300.0)
        self.assertEqual(live_total, 300.0)
        self.assertEqual(live_by_day["2026-10-19"], 300.0)


if __name__ == "__main__":
    unittest.main()

