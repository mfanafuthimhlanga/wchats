"""Pytest plugin that makes `docling` importable, so the gate's NOT-skipping half is observable.

docling ships only in the optional `pipeline` extra and is not installed on the
development machine (CLAUDE.md, "Environment constraints"). Without this plugin the
only observable behaviour of the `pytest.importorskip` gate on
tests/integration/test_ingestion_chain.py would be "skipped", and a guard seen to fire
in exactly one direction is indistinguishable from a guard that always fires.

Loaded with `-p tests.unit._docling_present_plugin` by
tests/unit/test_ingestion_chain_docling_gate.py, which asserts the gated module
collects its four tests under it. Deliberately NOT a conftest: it must apply only to
the subprocess that opts in, never to the suite that loads it.

The names registered here are exactly the ones app/ imports at call time:
    docling.chunking.HybridChunker                      chunking_service.py:64
    docling_core.types.doc.TableItem                    chunking_service.py:65
    docling.document_converter.DocumentConverter        docling_service.py:41
    docling.datamodel.base_models.ConversionStatus      docling_service.py:61
    docling.datamodel.base_models.DocumentStream        docling_service.py:99

They are stand-ins for import resolution only. Nothing here models docling's
behaviour, and this plugin is never evidence that a test which uses it PASSES for a
docling-shaped reason — only that the gate let it run.
"""

import sys
import types


def _register(name: str, is_package: bool = False) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"<stand-in for {name}>"
    if is_package:
        module.__path__ = []
    sys.modules[name] = module
    return module


class HybridChunker:
    """Stand-in for docling.chunking.HybridChunker.

    Every test that reaches chunk_document patches this name, so construction here
    means the patch did not take — fail loudly rather than silently chunk nothing.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "the stand-in HybridChunker was constructed — patch('docling.chunking."
            "HybridChunker') did not take, so this test is measuring the stand-in"
        )


class TableItem:
    """Stand-in for docling_core.types.doc.TableItem.

    chunk_document calls isinstance(item, TableItem) directly, so this must be a real
    class; a MagicMock would raise TypeError as the second isinstance argument.
    """


class DocumentStream:
    def __init__(self, *args, **kwargs):
        pass


class DocumentConverter:
    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "the stand-in DocumentConverter was constructed — docling_service._converter "
            "was not patched, so this test is measuring the stand-in"
        )


class ConversionStatus:
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"


_docling = _register("docling", is_package=True)

_chunking = _register("docling.chunking")
_chunking.HybridChunker = HybridChunker
_docling.chunking = _chunking

_converter = _register("docling.document_converter")
_converter.DocumentConverter = DocumentConverter
_docling.document_converter = _converter

_datamodel = _register("docling.datamodel", is_package=True)
_docling.datamodel = _datamodel
_base_models = _register("docling.datamodel.base_models")
_base_models.ConversionStatus = ConversionStatus
_base_models.DocumentStream = DocumentStream
_datamodel.base_models = _base_models

_core = _register("docling_core", is_package=True)
_core_types = _register("docling_core.types", is_package=True)
_core.types = _core_types
_core_doc = _register("docling_core.types.doc")
_core_doc.TableItem = TableItem
_core_types.doc = _core_doc
