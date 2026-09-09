import ezdxf
import pytest


@pytest.fixture
def new_doc():
    return ezdxf.new("R2000")
