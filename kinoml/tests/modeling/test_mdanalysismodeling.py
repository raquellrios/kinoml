"""
Test OEModeling functionalities of `kinoml.modeling`
"""
from contextlib import contextmanager
from importlib import resources
import pytest

@contextmanager
def does_not_raise():
    yield


@pytest.mark.parametrize(
    "package, resource, expectation, n_atoms",
    [
        (  # unsupported file format
            "kinoml.data.molecules",
            "chloroform_acetamide.sdf",
            pytest.raises(ValueError),
            14,
        ),
        (  # multi-molecule pdb
            "kinoml.data.molecules",
            "chloroform_acetamide.pdb",
            pytest.raises(IndexError),
            14,
        ),
        (  # correct pdb
            "kinoml.data.proteins",
            "4f8o.pdb",
            does_not_raise(),
            2475,
        ),
    ],
)
def test_read_molecule(package, resource, expectation, n_atoms):
    """Compare results to expected number of atoms."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule

    with resources.path(package, resource) as path:
        with expectation:
            molecule = read_molecule(str(path))
            assert len(molecule.atoms) == n_atoms


@pytest.mark.parametrize(
    "package, resource, chain_id, expectation, n_atoms",
    [
        ("kinoml.data.proteins", "4f8o.pdb", "A", does_not_raise(), 2430),
        ("kinoml.data.proteins", "4f8o.pdb", "B", does_not_raise(), 45),
        ("kinoml.data.proteins", "4f8o.pdb", "1", pytest.raises(ValueError), 0),
    ],
)
def test_select_chain(package, resource, chain_id, expectation, n_atoms):
    """Compare results to expected number of atoms."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule, select_chain

    with resources.path(package, resource) as path:
        molecule = read_molecule(str(path))
        with expectation:
            selection = select_chain(molecule, chain_id)
            assert len(selection.atoms) == n_atoms


@pytest.mark.parametrize(
    "package, resource, alternate_location, expectation, n_atoms",
    [
        ("kinoml.data.proteins", "4f8o.pdb", "A", does_not_raise(), 2458),
        ("kinoml.data.proteins", "4f8o.pdb", "B", does_not_raise(), 2458),
        ("kinoml.data.proteins", "4f8o.pdb", "C", pytest.raises(ValueError), 2458),
    ],
)
def test_select_altloc(package, resource, alternate_location, expectation, n_atoms):
    """Compare results to expected number of atoms."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule, select_altloc

    with resources.path(package, resource) as path:
        molecule = read_molecule(str(path))
        with expectation:
            selection = select_altloc(molecule, alternate_location)
            assert len(selection.atoms) == n_atoms


@pytest.mark.parametrize(
    "package, resource, exceptions, remove_water, n_atoms",
    [
        ("kinoml.data.proteins", "4f8o.pdb", [], True, 2104),
        ("kinoml.data.proteins", "4f8o.pdb", [], False, 2393),
        ("kinoml.data.proteins", "4f8o.pdb", ["AES"], True, 2122),
    ],
)
def test_remove_non_protein(package, resource, exceptions, remove_water, n_atoms):
    """Compare results to expected number of atoms."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule, remove_non_protein

    with resources.path(package, resource) as path:
        molecule = read_molecule(str(path))
    selection = remove_non_protein(molecule, exceptions=exceptions, remove_water=remove_water)
    assert len(selection.atoms) == n_atoms


@pytest.mark.parametrize(
    "package, resource, n_atoms",
    [
        ("kinoml.data.proteins", "4f8o.pdb", 2455),
    ],
)
def test_delete_expression_tags(package, resource, n_atoms):
    """Compare results to expected number of expression tags."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule, delete_expression_tags

    with resources.path(package, resource) as path:
        molecule = read_molecule(str(path))
        molecule = delete_expression_tags(molecule, path)
        assert len(molecule.atoms) == n_atoms


@pytest.mark.parametrize(
    "package, resource, sequence",
    [
        (
            "kinoml.data.proteins",
            "4f8o.pdb",
            "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGLXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        ),
        (
            "kinoml.data.molecules",
            "chloroform.pdb",
            "X",
        ),
    ],
)
def test_get_sequence(package, resource, sequence):
    """Compare results to expected sequence."""
    from kinoml.modeling.MDAnalysisModeling import read_molecule, get_sequence

    with resources.path(package, resource) as path:
        structure = read_molecule(str(path))
        assert get_sequence(structure) == sequence


@pytest.mark.parametrize(
    "package, resource, sequence, expected_alignment",
    [
        (  # mutation (middle and end)
            "kinoml.data.proteins",
            "4f8o.pdb",
            "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLFSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGV",
            [
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLFSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGV",
            ],
        ),
        (  # insertions (missing D82 could be placed at two positions, only "D-" is correct)
            "kinoml.data.proteins",
            "4f8o_edit.pdb",
            "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGVVVGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGLEHHHHHH",
            [
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKG---GYMISADGDYVGLYSYMMSWVGIDNNWYIND-SPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTV-QGL-------",
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGVVVGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGLEHHHHHH",
            ],
        ),
        (  # deletions (start and middle)
            "kinoml.data.proteins",
            "4f8o.pdb",
            "FHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
            [
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
                "---FHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGD---LYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
            ],
        ),
        (  # all together
            "kinoml.data.proteins",
            "4f8o_edit.pdb",
            "FHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGVVVGYMISADGDLFSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGVEHHHHHH",
            [
                "MNTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKG---GYMISADGDYVGLYSYMMSWVGIDNNWYIND-SPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTV-QGL-------",
                "---FHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGVVVGYMISADGD---LFSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGVEHHHHHH",
            ],
        ),
    ],
)
def test_get_structure_sequence_alignment(package, resource, sequence, expected_alignment):
    """Compare results to expected sequence alignment."""
    from kinoml.modeling.MDAnalysisModeling import (
        read_molecule,
        remove_non_protein,
        get_structure_sequence_alignment
    )
    with resources.path(package, resource) as path:
        structure = read_molecule(str(path))
        structure = remove_non_protein(structure, remove_water=True)
        alignment = get_structure_sequence_alignment(structure, sequence)
        for sequence1, sequence2 in zip(alignment, expected_alignment):
            assert sequence1 == sequence2


@pytest.mark.parametrize(
    "package, resource, sequence, expected_sequence",
    [
        (  # delete insertion at residue 4
            "kinoml.data.proteins",
            "4f8o.pdb",
            "MNTHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
            "MNTHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
        ),
        (  # delete mutation at residue 1
            "kinoml.data.proteins",
            "4f8o.pdb",
            "ANTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
            "NTFHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
        ),
        (  # delete insertion at residue 1 and mutation at residue 4
            "kinoml.data.proteins",
            "4f8o.pdb",
            "NTWHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
            "NTHVDFAPNTGEIFAGKQPGDVTMFTLTMGDTAPHGGWRLIPTGDSKGGYMISADGDYVGLYSYMMSWVGIDNNWYINDDSPKDIKDHLYVKAGTVLKPTTYKFTGRVEEYVFDNKQSTVINSKDVSGEVTVKQGL",
        ),
    ],
)
def test_delete_alterations(package, resource, sequence, expected_sequence):
    """Compare results to expected sequence."""
    from kinoml.modeling.MDAnalysisModeling import (
        read_molecule,
        remove_non_protein,
        delete_alterations,
        get_sequence
    )
    with resources.path(package, resource) as path:
        structure = read_molecule(str(path))
        structure = remove_non_protein(structure, remove_water=True)
        structure_with_deletions = delete_alterations(structure, sequence)
        new_sequence = get_sequence(structure_with_deletions)
        assert new_sequence == expected_sequence
