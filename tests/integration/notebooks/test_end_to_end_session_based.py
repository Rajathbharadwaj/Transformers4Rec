import os

import pytest
from merlin.core.dispatch import HAS_GPU
from testbook import testbook

from tests.conftest import REPO_ROOT

pytest.importorskip("transformers")

# flake8: noqa


@pytest.mark.notebook
@pytest.mark.skipif(not HAS_GPU, reason="No GPU available")
def test_func():
    with testbook(
        REPO_ROOT / "examples" / "end-to-end-session-based" / "01-ETL-with-NVTabular.ipynb",
        execute=False,
    ) as tb1:
        tb1.inject(
            """
            import os
            os.environ["DATA_FOLDER"] = "/tmp/data/"
            os.environ["USE_SYNTHETIC"] = "True"
            os.environ["START_DATE"] = "2014/4/1"
            os.environ["END_DATE"] = "2014/4/5"
            os.environ["THRESHOLD_DAY_INDEX"] = "1"
            """
        )
        tb1.execute()
        assert os.path.isdir("/tmp/data/processed_nvt")
        assert os.path.isdir("/tmp/data/preproc_sessions_by_day")
        assert os.path.isdir("/tmp/data/workflow_etl")

    with testbook(
        REPO_ROOT
        / "examples"
        / "end-to-end-session-based"
        / "02-End-to-end-session-based-with-Yoochoose-PyT.ipynb",
        timeout=720,
        execute=False,
    ) as tb2:
        tb2.inject(
            """
            import os
            os.environ["INPUT_DATA_DIR"] = "/tmp/data/"
            os.environ["OUTPUT_DIR"] = "/tmp/data/preproc_sessions_by_day"
            os.environ["START_TIME_INDEX"] = "1"
            os.environ["END_TIME_INDEX"] = "3"
            os.environ["BATCH_SIZE_TRAIN"] = "64"
            os.environ["BATCH_SIZE_VALID"] = "32"
            """
        )
        NUM_OF_CELLS = len(tb2.cells)
        tb2.execute_cell(list(range(0, NUM_OF_CELLS - 20)))
        assert os.path.isdir("/tmp/data/models")
        assert os.listdir("/tmp/data/models")
