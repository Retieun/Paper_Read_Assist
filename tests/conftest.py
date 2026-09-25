import json
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper"


@pytest.fixture(scope="session")
def sample_doc(tmp_path_factory):
    """The sample paper ingested once for the whole session."""
    from papassist.ingest.pipeline import ingest_folder

    work = tmp_path_factory.mktemp("paper")
    shutil.copytree(FIXTURE, work / "src")
    return ingest_folder(work / "src", "sample")


@pytest.fixture(scope="session")
def sample_glossary(sample_doc):
    from papassist.glossary.build import build_glossary

    return build_glossary(sample_doc)


@pytest.fixture(scope="session")
def sample_resolver(sample_doc, sample_glossary):
    from papassist.resolve.resolver import Resolver

    return Resolver(sample_doc, sample_glossary)


@pytest.fixture(scope="session")
def library_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("library")


@pytest.fixture(scope="session")
def client(library_dir):
    from fastapi.testclient import TestClient

    from papassist import app as appmod
    from papassist.library.store import Library

    appmod.library = Library(library_dir)
    appmod._resolvers.clear()
    appmod.settings.llm_enabled = False
    return TestClient(appmod.app)
