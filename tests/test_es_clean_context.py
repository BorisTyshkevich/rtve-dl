import unittest
from unittest.mock import patch
from pathlib import Path

from rtve_dl.codex_batch import _build_prompt
from rtve_dl.codex_es_clean import clean_es_with_codex
from rtve_dl.rtve.catalog import list_assets_for_selector
from rtve_dl.workflows.download import _build_es_episode_context


class _FakeHttp:
    def get_text(self, _url: str) -> str:
        return '<html><a href="/api/programas/1573/videos"></a></html>'

    def get_json(self, _url: str) -> dict:
        item = {
            "type": {"name": "Completo"},
            "assetType": "video",
            "id": "385751",
            "htmlUrl": "https://www.rtve.es/v/385751/",
            "title": "El retorno del fugitivo",
            "temporadaOrden": 1,
            "episode": 1,
            "hasDRM": False,
            "shortDescription": "Resumen &ntilde; corto",
            "description": "<p>Texto <b>largo</b> con&nbsp;HTML.</p>",
        }
        return {"page": {"items": [item], "totalPages": 1}}


class CatalogContextTests(unittest.TestCase):
    def test_catalog_cleans_descriptions(self) -> None:
        assets = list_assets_for_selector(
            "https://www.rtve.es/play/videos/cuentame-como-paso/",
            "T1S1",
            http=_FakeHttp(),
            cache_dir=None,
        )
        self.assertEqual(len(assets), 1)
        a = assets[0]
        self.assertEqual(a.short_description, "Resumen ñ corto")
        self.assertEqual(a.description, "Texto largo con HTML.")

    def test_build_es_episode_context_prefers_full_description(self) -> None:
        assets = list_assets_for_selector(
            "https://www.rtve.es/play/videos/cuentame-como-paso/",
            "T1S1",
            http=_FakeHttp(),
            cache_dir=None,
        )
        a = assets[0]
        context = _build_es_episode_context(a)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("Título del episodio: El retorno del fugitivo", context)
        self.assertIn("Sinopsis: Texto largo con HTML.", context)


class PromptInjectionTests(unittest.TestCase):
    def test_build_prompt_without_context_keeps_payload_contract(self) -> None:
        prompt = _build_prompt(tsv_payload="id\ttexto\tprev\tnext\techo", prompt_mode="es_clean_light")
        self.assertIn("id\ttexto\tprev\tnext\techo", prompt)
        self.assertNotIn("{{EPISODE_CONTEXT}}", prompt)

    def test_build_prompt_with_context_injects_once(self) -> None:
        prompt = _build_prompt(
            tsv_payload="id\ttexto\tprev\tnext\techo",
            prompt_mode="es_clean_light",
            prompt_context="Título: Ep\nSinopsis: contexto",
        )
        self.assertEqual(prompt.count("Título: Ep"), 1)
        self.assertIn("Sinopsis: contexto", prompt)


class EsCleanForwardingTests(unittest.TestCase):
    def test_clean_es_with_codex_forwards_episode_context(self) -> None:
        captured: dict = {}

        def _fake_translate_es(**kwargs):
            captured.update(kwargs)
            return {"0": "hola"}

        with patch("rtve_dl.codex_es_clean.translate_es", side_effect=_fake_translate_es):
            out = clean_es_with_codex(
                cues=[("0", "hola")],
                base_path=Path("tmp/test"),
                chunk_size_cues=100,
                model="sonnet",
                fallback_model=None,
                resume=True,
                max_workers=1,
                context=None,
                backend="claude",
                no_chunk=None,
                episode_context="Sinopsis: prueba",
            )

        self.assertEqual(out["0"], "hola")
        self.assertEqual(captured.get("prompt_context"), "Sinopsis: prueba")


if __name__ == "__main__":
    unittest.main()
