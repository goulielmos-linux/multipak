import pytest

from multipak.environment_choices import default_environment, next_container_name
from multipak.models import Environment, ExistingEnvironment

BASE = "MultiPak-Fedora-45"


@pytest.mark.parametrize(
    "names,expected",
    [
        ([], BASE),
        ([BASE], "C2-" + BASE),
        ([BASE, "C2-" + BASE, "C3-" + BASE], "C4-" + BASE),
        ([BASE, "C3-" + BASE], "C2-" + BASE),
        (["C3-" + BASE], "C2-" + BASE),
        (["C10-" + BASE], "C2-" + BASE),
    ],
)
def test_new_container_names_fill_numbering_gaps(names, expected):
    assert next_container_name(BASE, names, new=True) == expected


def test_multipak_s_own_container_is_the_default():
    profile = Environment("fedora-45", "Fedora 45", "rpm", "quay.io/fedora/fedora-toolbox:45")
    secondary = ExistingEnvironment("C2-" + BASE, "second", "fedora", "45", managed_home=True)
    personal = ExistingEnvironment("Meu Fedora", "personal", "fedora", "45")
    default = ExistingEnvironment(BASE, "default", "fedora", "45", managed_home=True)
    foreign = ExistingEnvironment(BASE, "foreign", "fedora", "45", owned=False)
    # C2- is MultiPak's own when someone else had taken the default name.
    assert default_environment([personal, secondary], profile) == secondary
    assert default_environment([personal, secondary, default], profile) == default
    # A container with MultiPak's name that MultiPak did not create is never used.
    assert default_environment([foreign], profile) is None
    assert default_environment([foreign, secondary], profile) == secondary
    assert foreign.multipak_number is None
    assert secondary.multipak_number == 2
    assert (
        ExistingEnvironment(BASE, "custom", "fedora", "45", custom_named=True).multipak_number
        is None
    )
