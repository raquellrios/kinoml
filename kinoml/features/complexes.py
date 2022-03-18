"""
Featurizers that can only get applied to ProteinLigandComplexes or
subclasses thereof
"""
import logging
from pathlib import Path
from typing import Union, List, Tuple

from .core import OEBaseModelingFeaturizer, ParallelBaseFeaturizer
from ..core.ligands import Ligand
from ..core.proteins import Protein, KLIFSKinase
from ..core.systems import ProteinLigandComplex


logger = logging.getLogger(__name__)


class SingleLigandProteinComplexFeaturizer(ParallelBaseFeaturizer):
    """
    Provides a minimally useful ``._supports()`` method for all
    ProteinLigandComplex-like featurizers.
    """

    _COMPATIBLE_PROTEIN_TYPES = (Protein, KLIFSKinase)
    _COMPATIBLE_LIGAND_TYPES = (Ligand,)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _supports(self, system: Union[ProteinLigandComplex]) -> bool:
        """
        Check that exactly one protein and one ligand is present in the System
        """
        super_checks = super()._supports(system)
        proteins = [c for c in system.components if isinstance(c, self._COMPATIBLE_PROTEIN_TYPES)]
        ligands = [c for c in system.components if isinstance(c, self._COMPATIBLE_LIGAND_TYPES)]
        return all([super_checks, len(proteins) == 1]) and all([super_checks, len(ligands) == 1])


class MostSimilarPDBLigandFeaturizer(SingleLigandProteinComplexFeaturizer):
    """
    Find the most similar co-crystallized ligand in the PDB according to a
    given SMILES and UniProt ID.

    The protein component of each system must be a `core.proteins.Protein` or
    a subclass thereof, and must be initialized with a `uniprot_id` parameter.

    The ligand component of each system must be a `core.ligands.Ligand` or a
    subclass thereof and give access to the molecular structure, e.g. via a
    SMILES.

    Parameters
    ----------
    similarity_metric: str, default="fingerprint"
        The similarity metric to use to detect the structure with the most
        similar ligand ["fingerprint", "mcs", "openeye_shape",
        "schrodinger_shape"].
    cache_dir: str, Path or None, default=None
        Path to directory used for saving intermediate files. If None, default
        location provided by `appdirs.user_cache_dir()` will be used.
    use_multiprocessing : bool, default=True
        If multiprocessing to use.
    n_processes : int or None, default=None
        How many processes to use in case of multiprocessing. Defaults to
        number of available CPUs.

    Note
    ----
    The toolkit ['MDAnalysis' or 'OpenEye'] specified in the protein object
    initialization should fit the required toolkit when subsequently applying
    the OEDockingFeaturizer or SCHRODINGERDockingFeaturizer.
    """

    import pandas as pd

    _SUPPORTED_TYPES = (ProteinLigandComplex,)
    _SUPPORTED_SIMILARITY_METRICS = ("fingerprint", "mcs", "openeye_shape", "schrodinger_shape")

    def __init__(
        self,
        similarity_metric: str = "fingerprint",
        cache_dir: Union[str, Path, None] = None,
        **kwargs,
    ):
        from appdirs import user_cache_dir

        super().__init__(**kwargs)
        if similarity_metric not in self._SUPPORTED_SIMILARITY_METRICS:
            raise ValueError(
                f"Only {self._SUPPORTED_SIMILARITY_METRICS} are allowed as "
                f"similarity metric! You provided '{similarity_metric}'."
            )
        self.similarity_metric = similarity_metric
        self.cache_dir = Path(user_cache_dir())
        if cache_dir:
            self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _pre_featurize(self, systems: List[ProteinLigandComplex]) -> None:
        """
        Check that SCHRODINGER variable exists.
        """
        import os

        if self.similarity_metric == "schrodinger_shape":
            try:
                self.schrodinger = os.environ["SCHRODINGER"]
            except KeyError:
                raise KeyError("Cannot find the SCHRODINGER variable!")
        return

    def _featurize_one(self, system: ProteinLigandComplex) -> Union[ProteinLigandComplex, None]:
        """
        Find a PDB entry with a protein of the given UniProt ID and with the most similar
        co-crystallized ligand.

        Parameters
        ----------
        system: ProteinLigandComplex
            A system object holding a protein and a ligand component.

        Returns
        -------
        : ProteinLigandComplex or None
            The same system, but with additional protein attributes, i.e. pdb_id, chain_id and
            expo_id. None if no suitable PDB entry was found.
        """

        logger.debug("Getting available ligand entities from PDB...")
        pdb_ligand_entities = self._get_pdb_ligand_entities(system.protein.uniprot_id)
        if pdb_ligand_entities is None or len(pdb_ligand_entities) == 0:
            return None

        logger.debug("Getting most similar PDB ligand entity ...")
        pdb_id, chain_id, expo_id = self._get_most_similar_pdb_ligand_entity(
            pdb_ligand_entities, system.ligand.molecule.to_smiles(explicit_hydrogens=False)
        )

        logger.debug("Adding results to new protein object ...")
        new_protein = system.protein.__class__(
            pdb_id=pdb_id,
            uniprot_id=system.protein.uniprot_id,
            name=system.protein.name,
            toolkit=system.protein.toolkit,
        )
        new_protein.chain_id = chain_id
        new_protein.expo_id = expo_id
        system.components = [new_protein, system.ligand]

        return system

    def _post_featurize(
        self,
        systems: List[ProteinLigandComplex],
        features: List[ProteinLigandComplex],
        keep: bool = True,
    ) -> List[ProteinLigandComplex]:
        """
        Run after featurizing all systems. Original systems will be replaced with systems
        returned by the featurizer. Systems that were not successfully featurized will be
        removed.

        Parameters
        ----------
        systems: list of ProteinLigandComplex
            The systems being featurized.
        features: list of ProteinLigandComplex
            The features returned by ``self._featurize``, i.e. new systems.
        keep: bool, optional=True
            Whether to store the current featurizer in the ``system.featurizations``
            dictionary with its own key (``self.name``), in addition to ``last``.

        Returns
        -------
        : list of ProteinLigandComplex
            The new systems with ``.featurizations`` extended with the calculated features in two
            entries: the featurizer name and ``last``.
        """
        systems = [feature for feature in features if feature]
        for system in systems:
            feature = (system.protein.pdb_id, system.protein.chain_id, system.protein.expo_id)
            system.featurizations["last"] = feature
            if keep:
                system.featurizations[self.name] = feature
        return systems

    def _get_pdb_ligand_entities(self, uniprot_id: str) -> Union[pd.DataFrame, None]:
        """
        Get PDB ligand entities bound to protein structures of the given UniProt ID. Only X-ray
        structures will be considered. If a ligand is co-crystallized with multiple PDB structures
        the ligand entity with the lowest resolution will be returned.

        Parameters
        ----------
        uniprot_id: str
            The UniProt ID of the protein of interest.

        Returns
        -------
        : pd.DataFrame or None
            A DataFrame with columns `ligand_entity`, `pdb_id`, `non_polymer_id`, `chain_id`,
            `expo_id` and `resolution`. None if no suitable ligand entities were found.
        """
        from biotite.database import rcsb
        import pandas as pd

        logger.debug("Querying PDB by UniProt ID for ligand entities ...")
        query_by_uniprot = rcsb.FieldQuery(
            "rcsb_polymer_entity_container_identifiers."
            "reference_sequence_identifiers.database_name",
            exact_match="UniProt",
        )
        query_by_uniprot_id = rcsb.FieldQuery(
            "rcsb_polymer_entity_container_identifiers."
            "reference_sequence_identifiers.database_accession",
            exact_match=uniprot_id,
        )
        query_by_experimental_method = rcsb.FieldQuery(
            "exptl.method", exact_match="X-RAY DIFFRACTION"  # allows later sorting for resolution
        )
        results = rcsb.search(
            rcsb.CompositeQuery(
                [
                    query_by_uniprot,
                    query_by_uniprot_id,
                    query_by_experimental_method,
                ],
                operator="and",
            ),
            return_type="non_polymer_entity",
        )
        pdb_ligand_entities = []
        for pdb_ligand_entity in results:
            pdb_id, non_polymer_id = pdb_ligand_entity.split("_")
            pdb_ligand_entities.append(
                {
                    "ligand_entity": pdb_ligand_entity,
                    "pdb_id": pdb_id,
                    "non_polymer_id": non_polymer_id,
                }
            )
        if len(pdb_ligand_entities) == 0:
            logger.debug(f"No ligand entities found for UniProt ID {uniprot_id}, returning None!")
            return None

        logger.debug("Adding chain and expo IDs for each ligand entity ...")
        pdb_ligand_entities = pd.DataFrame(pdb_ligand_entities)
        pdb_ligand_entities = self._add_ligand_entity_info(pdb_ligand_entities)

        logger.debug("Adding resolution to each ligand entity ...")
        pdb_ligand_entities = self._add_pdb_resolution(pdb_ligand_entities)

        logger.debug("Picking highest quality entity per ligand ...")
        pdb_ligand_entities.sort_values(by="resolution", inplace=True)
        pdb_ligand_entities = pdb_ligand_entities.groupby("expo_id").head(1)

        return pdb_ligand_entities

    @staticmethod
    def _add_ligand_entity_info(pdb_ligand_entities: pd.DataFrame) -> pd.DataFrame:
        """
        Add chain and expo ID information to the PDB ligand entities dataframe.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with a column named `ligand_entity`. This column
            must contain strings in the format '4YNE_3', i.e. the third non polymer entity of
            PDB entry 4YNE.

        Returns
        -------
        : pd.DataFrame
            The same PDB ligand entities dataframe but with additional columns named `chain_id`
            and `expo_id`. PDB ligand entities without such information are removed.
        """
        import json
        import math
        import requests
        import urllib

        base_url = "https://data.rcsb.org/graphql?query="
        ligand_entity_ids = pdb_ligand_entities["ligand_entity"].to_list()
        chain_ids_dict = {}
        expo_ids_dict = {}
        n_batches = math.ceil(len(ligand_entity_ids) / 50)  # request maximal 50 entries at a time
        for i in range(n_batches):
            ligand_entity_ids_batch = ligand_entity_ids[i * 50 : (i * 50) + 50]
            logger.debug(f"Batch {i}\n{ligand_entity_ids_batch}")
            query = (
                "{nonpolymer_entities(entity_ids:["
                + ",".join(
                    [
                        '"' + ligand_entity_id + '"'
                        for ligand_entity_id in set(ligand_entity_ids_batch)
                    ]
                )
                + "]){rcsb_nonpolymer_entity_container_identifiers"
                "{auth_asym_ids,nonpolymer_comp_id,rcsb_id}}}"
            )
            response = requests.get(base_url + urllib.parse.quote(query))
            for ligand_identity_info in json.loads(response.text)["data"]["nonpolymer_entities"]:
                identifiers = ligand_identity_info["rcsb_nonpolymer_entity_container_identifiers"]
                expo_ids_dict[identifiers["rcsb_id"]] = identifiers["nonpolymer_comp_id"]
                chain_ids_dict[identifiers["rcsb_id"]] = identifiers["auth_asym_ids"][0]

        pdb_ligand_entities["chain_id"] = pdb_ligand_entities["ligand_entity"].map(chain_ids_dict)
        pdb_ligand_entities["expo_id"] = pdb_ligand_entities["ligand_entity"].map(expo_ids_dict)

        pdb_ligand_entities = pdb_ligand_entities[
            (pdb_ligand_entities["chain_id"].notnull())
            & (pdb_ligand_entities["expo_id"].notnull())
        ]

        return pdb_ligand_entities

    @staticmethod
    def _add_pdb_resolution(pdb_ligand_entities: pd.DataFrame) -> pd.DataFrame:
        """
        Add resolution information to the PDB ligand entities dataframe.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with a column named `pdb_id`. This column must
            contain strings in the format '4YNE', i.e. PDB entry 4YNE.

        Returns
        -------
        : pd.DataFrame
            The same PDB ligand entities dataframe but with an additional column named
            `resolution`. PDB ligand entities without such information will get a dummy
            resolution of 99.9.
        """
        import json
        import math
        import requests
        import urllib

        base_url = "https://data.rcsb.org/graphql?query="
        pdb_ids = list(pdb_ligand_entities["pdb_id"].unique())
        resolution_dict = {}
        n_batches = math.ceil(len(pdb_ids) / 50)  # request maximal 50 entries at a time
        for i in range(n_batches):
            pdb_ids_batch = pdb_ids[i * 50 : (i * 50) + 50]
            logger.debug(f"Batch {i}\n{pdb_ids_batch}")
            query = (
                "{entries(entry_ids:["
                + ",".join(['"' + pdb_id + '"' for pdb_id in pdb_ids_batch])
                + "]){rcsb_id,pdbx_vrpt_summary{PDB_resolution}}}"
            )
            response = requests.get(base_url + urllib.parse.quote(query))
            for entry_info in json.loads(response.text)["data"]["entries"]:
                try:
                    resolution_dict[entry_info["rcsb_id"]] = float(
                        entry_info["pdbx_vrpt_summary"]["PDB_resolution"]
                    )
                except ValueError:
                    # add high dummy resolution
                    resolution_dict[entry_info["rcsb_id"]] = 99.9

        pdb_ligand_entities["resolution"] = pdb_ligand_entities["pdb_id"].map(resolution_dict)
        pdb_ligand_entities = pdb_ligand_entities[pdb_ligand_entities["resolution"].notnull()]

        return pdb_ligand_entities

    def _get_most_similar_pdb_ligand_entity(
        self, pdb_ligand_entities: pd.DataFrame, smiles: str
    ) -> Tuple[str, str, str]:
        """
        Get the PDB ligand that is most similar to the given SMILES.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with columns named `pdb_id`, `chain_id` and
            `expo_id`.

        Returns
        -------
        : tuple of str
            The PDB, chain and expo ID of the most similar ligand.
        """
        from ..databases.pdb import smiles_from_pdb

        logger.debug(f"Retrieving SMILES for {pdb_ligand_entities['expo_id']}")
        smiles_dict = smiles_from_pdb(pdb_ligand_entities["expo_id"])
        pdb_ligand_entities["smiles"] = pdb_ligand_entities["expo_id"].map(smiles_dict)

        if self.similarity_metric == "fingerprint":
            logger.debug("Retrieving most similar ligand entity by fingerprint ...")
            pdb_id, chain_id, expo_id = self._by_fingerprint(pdb_ligand_entities, smiles)
        elif self.similarity_metric == "mcs":
            logger.debug(
                "Retrieving most similar ligand entity by maximum common substructure ..."
            )
            pdb_id, chain_id, expo_id = self._by_mcs(pdb_ligand_entities, smiles)
        elif self.similarity_metric == "openeye_shape":
            logger.debug("Retrieving most similar ligand entity by OpenEye shape ...")
            pdb_id, chain_id, expo_id = self._by_openeye_shape(pdb_ligand_entities, smiles)
        elif self.similarity_metric == "schrodinger_shape":
            logger.debug("Retrieving most similar ligand entity by SCHRODINGER shape ...")
            pdb_id, chain_id, expo_id = self._by_schrodinger_shape(pdb_ligand_entities, smiles)
        else:
            raise ValueError(f"Similarity metric '{self.similarity_metric}' unknown!")

        return pdb_id, chain_id, expo_id

    @staticmethod
    def _by_fingerprint(pdb_ligand_entities: pd.DataFrame, smiles: str) -> Tuple[str, str, str]:
        """
        Get the PDB ligand that is most similar to the given SMILES according to Morgan
        Fingerprints.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with columns named `pdb_id`, `chain_id`, `expo_id`
            and `smiles`.
        smiles: str
            The SMILES representation of the molecule to search for similar PDB ligands.

        Returns
        -------
        : tuple of str
            The PDB, chain and expo ID of the most similar ligand.
        """
        import pandas as pd

        from rdkit import Chem, RDLogger
        from rdkit.Chem import AllChem, DataStructs

        if logger.level != logging.DEBUG:
            RDLogger.DisableLog("rdApp.*")  # disable RDKit logging

        logger.debug("Generating fingerprint for reference molecule ...")
        reference = Chem.MolFromSmiles(smiles)
        reference_fingerprint = AllChem.GetMorganFingerprint(reference, 2, useFeatures=True)

        logger.debug("Generating fingerprints for PDB ligand entities ...")
        pdb_ligands = [Chem.MolFromSmiles(smiles) for smiles in pdb_ligand_entities["smiles"]]
        pdb_ligand_entities["rdkit_molecules"] = pdb_ligands
        pdb_ligand_entities = pdb_ligand_entities[pdb_ligand_entities["rdkit_molecules"].notnull()]
        pd.options.mode.chained_assignment = None  # otherwise next line would raise a warning
        pdb_ligand_entities["rdkit_fingerprint"] = [
            AllChem.GetMorganFingerprint(rdkit_molecule, 2, useFeatures=True)
            for rdkit_molecule in pdb_ligand_entities["rdkit_molecules"]
        ]

        logger.debug("Calculating fingerprint similarity ...")
        pdb_ligand_entities["fingerprint_similarity"] = [
            DataStructs.DiceSimilarity(reference_fingerprint, fingerprint)
            for fingerprint in pdb_ligand_entities["rdkit_fingerprint"]
        ]

        pdb_ligand_entities.sort_values(by="fingerprint_similarity", inplace=True, ascending=False)
        logger.debug(f"Fingerprint similarites:\n{pdb_ligand_entities}")

        picked_ligand_entity = pdb_ligand_entities.iloc[0]

        return (
            picked_ligand_entity["pdb_id"],
            picked_ligand_entity["chain_id"],
            picked_ligand_entity["expo_id"],
        )

    @staticmethod
    def _by_mcs(pdb_ligand_entities: pd.DataFrame, smiles: str) -> Tuple[str, str, str]:
        """
        Get the PDB ligand that is most similar to the given SMILES according to Morgan
        Fingerprints.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with columns named `pdb_id`, `chain_id`, `expo_id`
            and `smiles`.
        smiles: str
            The SMILES representation of the molecule to search for similar PDB ligands.

        Returns
        -------
        : tuple of str
            The PDB, chain and expo ID of the most similar ligand.
        """
        from rdkit import Chem, RDLogger
        from rdkit.Chem import rdFMCS

        if logger.level != logging.DEBUG:
            RDLogger.DisableLog("rdApp.*")  # disable RDKit logging

        logger.debug("Loading reference molecule ...")
        reference_molecule = Chem.MolFromSmiles(smiles)

        logger.debug("Loading PDB ligands ...")
        pdb_ligands = [Chem.MolFromSmiles(smiles) for smiles in pdb_ligand_entities["smiles"]]
        pdb_ligand_entities["rdkit_molecules"] = pdb_ligands
        pdb_ligand_entities = pdb_ligand_entities[pdb_ligand_entities["rdkit_molecules"].notnull()]

        logger.debug("Finding maximum common structure and counting bonds ...")
        mcs_bonds = [
            rdFMCS.FindMCS(
                [reference_molecule, pdb_ligand_molecule], ringMatchesRingOnly=True, timeout=60
            ).numBonds
            for pdb_ligand_molecule in pdb_ligand_entities["rdkit_molecules"]
        ]
        pdb_ligand_entities["mcs_bonds"] = mcs_bonds

        pdb_ligand_entities.sort_values(by="mcs_bonds", inplace=True, ascending=False)
        logger.debug(f"MCS bonds:\n{pdb_ligand_entities}")

        picked_ligand_entity = pdb_ligand_entities.iloc[0]

        return (
            picked_ligand_entity["pdb_id"],
            picked_ligand_entity["chain_id"],
            picked_ligand_entity["expo_id"],
        )

    def _by_schrodinger_shape(
        self, pdb_ligand_entities: pd.DataFrame, smiles: str
    ) -> Tuple[str, str, str]:
        """
        Get the PDB ligand that is most similar to the given SMILES according to SCHRODINGER
        shape_screen.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with columns named `pdb_id`, `chain_id`, `expo_id`
            and `smiles`.
        smiles: str
            The SMILES representation of the molecule to search for similar PDB ligands.

        Returns
        -------
        : tuple of str
            The PDB, chain and expo ID of the most similar ligand.
        """
        from tempfile import NamedTemporaryFile

        from rdkit import Chem
        from rdkit.Chem import AllChem

        from ..databases.pdb import download_pdb_ligand
        from ..modeling.SCHRODINGERModeling import shape_screen

        logger.debug("Downloading PDB ligands ...")
        queries = []
        for _, pdb_ligand_entity in pdb_ligand_entities.iterrows():
            query_path = download_pdb_ligand(
                pdb_id=pdb_ligand_entity["pdb_id"],
                chain_id=pdb_ligand_entity["chain_id"],
                expo_id=pdb_ligand_entity["expo_id"],
                directory=self.cache_dir,
            )
            if query_path:
                pdb_ligand_entity["path"] = query_path
                queries.append(pdb_ligand_entity)

        with NamedTemporaryFile(mode="w", suffix=".sdf") as query_sdf_path, NamedTemporaryFile(
            mode="w", suffix=".sdf"
        ) as ligand_sdf_path, NamedTemporaryFile(mode="w", suffix=".sdf") as result_sdf_path:
            logger.debug("Merging PDB ligands to query SDF file ...")
            with Chem.SDWriter(query_sdf_path.name) as writer:
                for query in queries:
                    mol = next(Chem.SDMolSupplier(str(query["path"]), removeHs=False))
                    writer.write(mol)

            logger.debug("Creating SDF file for given smiles ...")
            mol = Chem.MolFromSmiles(smiles)
            mol = Chem.AddHs(mol)
            AllChem.EmbedMolecule(mol)
            with Chem.SDWriter(ligand_sdf_path.name) as writer:
                writer.write(mol)

            logger.debug("Running shape_screen ...")
            shape_screen(
                schrodinger_directory=self.schrodinger,
                query_path=query_sdf_path.name,
                library_path=ligand_sdf_path.name,
                output_sdf_path=result_sdf_path.name,
                flexible=True,
                thorough_sampling=True,
                keep_best_match_only=True,
            )

            logger.debug("Getting best query ...")
            mol = next(Chem.SDMolSupplier(str(result_sdf_path.name), removeHs=False))
            best_query_index = int(mol.GetProp("i_phase_Shape_Query")) - 1
            picked_ligand_entity = queries[best_query_index]

        return (
            picked_ligand_entity["pdb_id"],
            picked_ligand_entity["chain_id"],
            picked_ligand_entity["expo_id"],
        )

    def _by_openeye_shape(
        self, pdb_ligand_entities: pd.DataFrame, smiles: str
    ) -> Tuple[str, str, str]:
        """
        Get the PDB ligand that is most similar to the given SMILES according to OpenEye's
        TanimotoCombo score.

        Parameters
        ----------
        pdb_ligand_entities: pd.DataFrame
            The PDB ligand entities dataframe with columns named `pdb_id`, `chain_id`, `expo_id`
            and `smiles`.
        smiles: str
            The SMILES representation of the molecule to search for similar PDB ligands.

        Returns
        -------
        : tuple of str
            The PDB, chain and expo ID of the most similar ligand.
        """
        from ..databases.pdb import download_pdb_ligand
        from ..modeling.OEModeling import (
            read_molecules,
            read_smiles,
            generate_reasonable_conformations,
            overlay_molecules,
        )

        logger.debug("Downloading PDB ligands ...")
        queries = []
        for _, pdb_ligand_entity in pdb_ligand_entities.iterrows():
            query_path = download_pdb_ligand(
                pdb_id=pdb_ligand_entity["pdb_id"],
                chain_id=pdb_ligand_entity["chain_id"],
                expo_id=pdb_ligand_entity["expo_id"],
                directory=self.cache_dir,
            )
            if query_path:
                pdb_ligand_entity["path"] = query_path
                queries.append(pdb_ligand_entity)

        logger.debug("Reading downloaded PDB ligands ...")
        pdb_ligand_molecules = [read_molecules(query["path"])[0] for query in queries]

        logger.debug("Generating reasonable conformations of ligand of interest ...")
        conformations_ensemble = generate_reasonable_conformations(read_smiles(smiles))

        logger.debug("Overlaying molecules ...")
        overlay_scores = []
        for conformations in conformations_ensemble:
            overlay_scores += [
                [i, overlay_molecules(pdb_ligand_molecule, conformations)[0]]
                for i, pdb_ligand_molecule in enumerate(pdb_ligand_molecules)
            ]

        overlay_scores = sorted(overlay_scores, key=lambda x: x[1], reverse=True)
        picked_ligand_entity = queries[overlay_scores[0][0]]

        return (
            picked_ligand_entity["pdb_id"],
            picked_ligand_entity["chain_id"],
            picked_ligand_entity["expo_id"],
        )


class OEComplexFeaturizer(OEBaseModelingFeaturizer, SingleLigandProteinComplexFeaturizer):
    """
    Given systems with exactly one protein and one ligand, prepare the complex
    structure by:

     - modeling missing loops
     - building missing side chains
     - mutations, if `uniprot_id` or `sequence` attribute is provided for the
       protein component (see below)
     - removing everything but protein, water and ligand of interest
     - protonation at pH 7.4

    The protein component of each system must be a `core.proteins.Protein` or
    a subclass thereof, must be initialized with toolkit='OpenEye' and give
    access to the molecular structure, e.g. via a pdb_id. Additionally, the
    protein component can have the following optional attributes to customize
    the protein modeling:

     - `name`: A string specifying the name of the protein, will be used for
       generating the output file name.
     - `chain_id`: A string specifying which chain should be used.
     - `alternate_location`: A string specifying which alternate location
       should be used.
     - `expo_id`: A string specifying the ligand of interest. This is
       especially useful if multiple ligands are present in a PDB structure.
     - `uniprot_id`: A string specifying the UniProt ID that will be used to
       fetch the amino acid sequence from UniProt, which will be used for
       modeling the protein. This will supersede the sequence information
       given in the PDB header.
     - `sequence`: A  string specifying the amino acid sequence in
       one-letter-codes that should be used during modeling the protein. This
       will supersede a given `uniprot_id` and the sequence information given
       in the PDB header.

    The ligand component of each system must be a `core.components.BaseLigand`
    or a subclass thereof. The ligand component can have the following
    optional attributes:

     - `name`: A string specifying the name of the ligand, will be used for
       generating the output file name.

    Parameters
    ----------
    loop_db: str
        The path to the loop database used by OESpruce to model missing loops.
    cache_dir: str, Path or None, default=None
        Path to directory used for saving intermediate files. If None, default
        location provided by `appdirs.user_cache_dir()` will be used.
    output_dir: str, Path or None, default=None
        Path to directory used for saving output files. If None, output
        structures will not be saved.
    use_multiprocessing : bool, default=True
        If multiprocessing to use.
    n_processes : int or None, default=None
        How many processes to use in case of multiprocessing. Defaults to
        number of available CPUs.

    Note
    ----
    If the ligand of interest is covalently bonded to the protein, the
    covalent bond will be broken. This may lead to the transformation of the
    ligand into a radical.
    """

    from MDAnalysis.core.universe import Universe

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    _SUPPORTED_TYPES = (ProteinLigandComplex,)

    def _featurize_one(self, system: ProteinLigandComplex) -> Union[Universe, None]:
        """
        Prepare a protein structure.

        Parameters
        ----------
        system: ProteinLigandComplex
            A system object holding a protein and a ligand component.

        Returns
        -------
        : Universe or None
            An MDAnalysis universe of the featurized system. None if no design unit was found.
        """
        from pathlib import Path

        import MDAnalysis as mda

        structure = self._read_protein_structure(system.protein)
        if structure is None:
            logger.warning(
                f"Could not read protein structure for {system.protein}, returning None!"
            )
            return None

        logger.debug("Preparing protein ligand complex ...")
        design_unit = self._get_design_unit(
            structure=structure,
            chain_id=system.protein.chain_id if hasattr(system.protein, "chain_id") else None,
            alternate_location=system.protein.alternate_location
            if hasattr(system.protein, "alternate_location")
            else None,
            has_ligand=True,
            ligand_name=system.protein.expo_id if hasattr(system.protein, "expo_id") else None,
            model_loops_and_caps=False if system.protein.sequence else True,
        )  # if sequence is given model loops and caps separately later
        if not design_unit:
            logger.debug("No design unit found, returning None!")
            return None

        logger.debug("Extracting design unit components ...")
        protein, solvent, ligand = self._get_components(
            design_unit=design_unit,
            chain_id=system.protein.chain_id if hasattr(system.protein, "chain_id") else None,
        )

        if system.protein.sequence:
            first_id = 1
            if "construct_range" in system.protein.metadata.keys():
                first_id = int(system.protein.metadata["construct_range"].split("-")[0])
            protein = self._process_protein(
                protein_structure=protein,
                amino_acid_sequence=system.protein.sequence,
                first_id=first_id,
                ligand=ligand,
            )

        logger.debug("Assembling components ...")
        protein_ligand_complex = self._assemble_components(protein, solvent, ligand)

        logger.debug("Updating pdb header ...")
        protein_ligand_complex = self._update_pdb_header(
            protein_ligand_complex,
            protein_name=system.protein.name,
            ligand_name=system.ligand.name,
        )

        logger.debug("Writing results ...")
        file_path = self._write_results(
            protein_ligand_complex,
            "_".join(
                [
                    info
                    for info in [
                        system.protein.name,
                        system.protein.pdb_id
                        if system.protein.pdb_id
                        else Path(system.protein.metadata["file_path"]).stem,
                        f"chain{system.protein.chain_id}"
                        if hasattr(system.protein, "chain_id")
                        else None,
                        f"altloc{system.protein.alternate_location}"
                        if hasattr(system.protein, "alternate_location")
                        else None,
                    ]
                    if info
                ]
            ),
            system.ligand.name,
        )

        logger.debug("Generating new MDAnalysis universe ...")
        structure = mda.Universe(file_path, in_memory=True)

        if not self.output_dir:
            logger.debug("Removing structure file ...")
            file_path.unlink()

        return structure


class OEDockingFeaturizer(OEBaseModelingFeaturizer, SingleLigandProteinComplexFeaturizer):
    """
    Given systems with exactly one protein and one ligand, prepare the
    structure and dock the ligand into the prepared protein structure with
    one of OpenEye's docking algorithms:

     - modeling missing loops
     - building missing side chains
     - mutations, if `uniprot_id` or `sequence` attribute is provided for the
       protein component (see below)
     - removing everything but protein, water and ligand of interest
     - protonation at pH 7.4
     - perform docking

    The protein component of each system must be a `core.proteins.Protein` or
    a subclass thereof, must be initialized with toolkit='OpenEye' and give
    access to the molecular structure, e.g. via a pdb_id. Additionally, the
    protein component can have the following optional attributes to customize
    the protein modeling:

     - `name`: A string specifying the name of the protein, will be used for
       generating the output file name.
     - `chain_id`: A string specifying which chain should be used.
     - `alternate_location`: A string specifying which alternate location
       should be used.
     - `expo_id`: A string specifying a ligand bound to the protein of
       interest. This is especially useful if multiple proteins are found in
       one PDB structure.
     - `uniprot_id`: A string specifying the UniProt ID that will be used to
       fetch the amino acid sequence from UniProt, which will be used for
       modeling the protein. This will supersede the sequence information
       given in the PDB header.
     - `sequence`: A  string specifying the amino acid sequence in
       one-letter-codes that should be used during modeling the protein. This
       will supersede a given `uniprot_id` and the sequence information given
       in the PDB header.
     - `pocket_resids`: List of integers specifying the residues in the
       binding pocket of interest. This attribute is required if docking with
       Fred into an apo structure.

    The ligand component of each system must be a `core.ligands.Ligand` or a
    subclass thereof and give access to the molecular structure, e.g. via a
    SMILES. Additionally, the ligand component can have the following optional
    attributes:

     - `name`: A string specifying the name of the ligand, will be used for
       generating the output file name.

    Parameters
    ----------
    method: str, default="Posit"
        The docking method to use ["Fred", "Hybrid", "Posit"].
    loop_db: str
        The path to the loop database used by OESpruce to model missing loops.
    cache_dir: str, Path or None, default=None
        Path to directory used for saving intermediate files. If None, default
        location provided by `appdirs.user_cache_dir()` will be used.
    output_dir: str, Path or None, default=None
        Path to directory used for saving output files. If None, output
        structures will not be saved.
    use_multiprocessing : bool, default=True
        If multiprocessing to use.
    n_processes : int or None, default=None
        How many processes to use in case of multiprocessing. Defaults to
        number of available CPUs.
    pKa_norm: bool, default=True
        Assign the predominant ionization state of the molecules to dock at pH
        ~7.4. If False, the ionization state of the input molecules will be
        conserved.
    """

    from MDAnalysis.core.universe import Universe

    def __init__(self, method: str = "Posit", pKa_norm: bool = True, **kwargs):
        super().__init__(**kwargs)
        if method not in ["Fred", "Hybrid", "Posit"]:
            raise ValueError(
                f"Docking method '{method}' is invalid, only 'Fred', 'Hybrid' and 'Posit' are "
                f"supported."
            )
        self.method = method
        self.pKa_norm = pKa_norm

    _SUPPORTED_TYPES = (ProteinLigandComplex,)

    def _featurize_one(self, system: ProteinLigandComplex) -> Union[Universe, None]:
        """
        Prepare a protein structure and dock a ligand using OpenEye's Fred method.

        Parameters
        ----------
        system: ProteinLigandComplex
            A system object holding a protein and a ligand component.

        Returns
        -------
        : Universe or None
            An MDAnalysis universe of the featurized system. None if no design unit or docking
            pose was found.
        """
        from pathlib import Path

        import MDAnalysis as mda
        from openeye import oechem, oedocking

        from ..docking.OEDocking import (
            fred_docking,
            hybrid_docking,
            pose_molecules,
            resids_to_box_molecule,
        )

        structure = self._read_protein_structure(system.protein)
        if structure is None:
            logger.warning(
                f"Could not read protein structure for {system.protein}, returning None!"
            )
            return None

        logger.debug("Preparing protein ligand complex ...")
        design_unit = self._get_design_unit(
            structure=structure,
            chain_id=system.protein.chain_id if hasattr(system.protein, "chain_id") else None,
            alternate_location=system.protein.alternate_location
            if hasattr(system.protein, "alternate_location")
            else None,
            has_ligand=hasattr(system.protein, "expo_id") or self.method in ["Hybrid", "Posit"],
            ligand_name=system.protein.expo_id if hasattr(system.protein, "expo_id") else None,
            model_loops_and_caps=False if system.protein.sequence else True,
        )  # if sequence is given model loops and caps separately later
        if not design_unit:
            logger.debug("No design unit found, returning None!")
            return None

        logger.debug("Extracting design unit components ...")
        protein, solvent, ligand = self._get_components(
            design_unit=design_unit,
            chain_id=system.protein.chain_id if hasattr(system.protein, "chain_id") else None,
        )

        if system.protein.sequence:
            first_id = 1
            if "construct_range" in system.protein.metadata.keys():
                first_id = int(system.protein.metadata["construct_range"].split("-")[0])
            protein = self._process_protein(
                protein_structure=protein,
                amino_acid_sequence=system.protein.sequence,
                first_id=first_id,
                ligand=ligand if ligand.NumAtoms() > 0 else None,
            )
            if not oechem.OEUpdateDesignUnit(
                design_unit, protein, oechem.OEDesignUnitComponents_Protein
            ):  # does not work if no ligand was present, e.g. Fred docking in apo structure
                # create new design unit with dummy site residue
                hierview = oechem.OEHierView(protein)
                first_residue = list(hierview.GetResidues())[0]
                design_unit = oechem.OEDesignUnit(
                    protein,
                    [
                        f"{first_residue.GetResidueName()}:{first_residue.GetResidueNumber()}: :"
                        f"{first_residue.GetOEResidue().GetChainID()}"
                    ],
                    solvent,
                )

        if self.method == "Fred":
            if hasattr(system.protein, "pocket_resids"):
                logger.debug("Defining binding site ...")
                box_molecule = resids_to_box_molecule(protein, system.protein.pocket_resids)
                receptor_options = oedocking.OEMakeReceptorOptions()
                receptor_options.SetBoxMol(box_molecule)
                logger.debug("Preparing receptor for docking ...")
                oedocking.OEMakeReceptor(design_unit, receptor_options)

        if not design_unit.HasReceptor():
            logger.debug("Preparing receptor for docking ...")
            oedocking.OEMakeReceptor(design_unit)

        logger.debug("Performing docking ...")
        if self.method == "Fred":
            docking_poses = fred_docking(
                design_unit, [system.ligand.molecule.to_openeye()], pKa_norm=self.pKa_norm
            )
        elif self.method == "Hybrid":
            docking_poses = hybrid_docking(
                design_unit, [system.ligand.molecule.to_openeye()], pKa_norm=self.pKa_norm
            )
        else:
            docking_poses = pose_molecules(
                design_unit,
                [system.ligand.molecule.to_openeye()],
                pKa_norm=self.pKa_norm,
                score_pose=True,
            )
        if not docking_poses:
            logger.debug("No docking pose found, returning None!")
            return None
        else:
            docking_pose = docking_poses[0]
        # generate residue information
        oechem.OEPerceiveResidues(docking_pose, oechem.OEPreserveResInfo_None)

        logger.debug("Assembling components ...")
        protein_ligand_complex = self._assemble_components(protein, solvent, docking_pose)

        logger.debug("Updating pdb header ...")
        protein_ligand_complex = self._update_pdb_header(
            protein_ligand_complex,
            protein_name=system.protein.name,
            ligand_name=system.ligand.name,
        )

        logger.debug("Writing results ...")
        file_path = self._write_results(
            protein_ligand_complex,
            "_".join(
                [
                    info
                    for info in [
                        system.protein.name,
                        system.protein.pdb_id
                        if system.protein.pdb_id
                        else Path(system.protein.metadata["file_path"]).stem,
                        f"chain{system.protein.chain_id}"
                        if hasattr(system.protein, "chain_id")
                        else None,
                        f"altloc{system.protein.alternate_location}"
                        if hasattr(system.protein, "alternate_location")
                        else None,
                    ]
                    if info
                ]
            ),
            system.ligand.name,
        )

        logger.debug("Generating new MDAnalysis universe ...")
        structure = mda.Universe(file_path, in_memory=True)

        if not self.output_dir:
            logger.debug("Removing structure file ...")
            file_path.unlink()

        return structure


class SCHRODINGERComplexFeaturizer(SingleLigandProteinComplexFeaturizer):
    """
    Given systems with exactly one protein and one ligand, prepare the complex
    structure by:

     - modeling missing loops
     - building missing side chains
     - mutations, if `uniprot_id` or `sequence` attribute is provided for the
       protein component
       (see below)
     - removing everything but protein, water and ligand of interest
     - protonation at pH 7.4

    The protein component of each system must have a `pdb_id` or a `path`
    attribute specifying the complex structure to prepare.

     - `pdb_id`: A string specifying the PDB entry of interest, required if
       `path` not given.
     - `path`: The path to the structure file, required if `pdb_id` not given.

    Additionally, the protein component can have the following optional
    attributes to customize the protein modeling:
     - `name`: A string specifying the name of the protein, will be used for
       generating the output file name.
     - `chain_id`: A string specifying which chain should be used.
     - `alternate_location`: A string specifying which alternate location
       should be used.
     - `expo_id`: A string specifying the ligand of interest. This is
       especially useful if multiple ligands are present in a PDB structure.
     - `uniprot_id`: A string specifying the UniProt ID that will be used to
       fetch the amino acid sequence from UniProt, which will be used for
       modeling the protein. This will supersede the sequence information
       given in the PDB header.
     - `sequence`: A string specifying the amino acid sequence in single
       letter codes to be used during loop modeling and for mutations.

    The ligand component can be a BaseLigand without any further attributes.
    Additionally, the ligand component can have the following optional
    attributes:

     - `name`: A string specifying the name of the ligand, will be used for
       generating the output file name.

    Parameters
    ----------
    cache_dir: str, Path or None, default=None
        Path to directory used for saving intermediate files. If None, default
        location provided by `appdirs.user_cache_dir()` will be used.
    output_dir: str, Path or None, default=None
        Path to directory used for saving output files. If None, output
        structures will not be saved.
    max_retry: int, default=3
        The maximal number of attempts to try running the prepwizard step.
    """

    from MDAnalysis.core.universe import Universe

    def __init__(
        self,
        cache_dir: Union[str, Path, None] = None,
        output_dir: Union[str, Path, None] = None,
        max_retry: int = 3,
        **kwargs,
    ):
        from appdirs import user_cache_dir

        super().__init__(**kwargs)
        self.cache_dir = Path(user_cache_dir())
        if cache_dir:
            self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if output_dir:
            self.output_dir = Path(output_dir).expanduser().resolve()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.save_output = True
        else:
            self.output_dir = Path(user_cache_dir())
            self.save_output = False
        self.max_retry = max_retry

    _SUPPORTED_TYPES = (ProteinLigandComplex,)

    def _pre_featurize(self, systems: List[ProteinLigandComplex]) -> None:
        """
        Check that SCHRODINGER variable exists.
        """
        import os

        try:
            self.schrodinger = os.environ["SCHRODINGER"]
        except KeyError:
            raise KeyError("Cannot find the SCHRODINGER variable!")
        return

    def _featurize_one(self, system: ProteinLigandComplex) -> Union[Universe, None]:
        """
        Prepare a protein structure.

        Parameters
        ----------
        system: ProteinLigandComplex
            A system object holding a protein and a ligand component.

        Returns
        -------
        : Universe or None
            An MDAnalysis universe of the featurized system or None if not successful.
        """
        from ..modeling.MDAnalysisModeling import read_molecule, write_molecule
        from ..utils import LocalFileStorage

        logger.debug("Interpreting system ...")
        system_dict = self._interpret_system(system)

        if system_dict["protein_sequence"]:
            system_dict["protein_path"] = self._preprocess_structure(
                pdb_path=system_dict["protein_path"],
                chain_id=system_dict["protein_chain_id"],
                alternate_location=system_dict["protein_alternate_location"],
                expo_id=system_dict["protein_expo_id"],
                sequence=system_dict["protein_sequence"],
            )

        prepared_structure_path = self._prepare_structure(
            system_dict["protein_path"], system_dict["protein_sequence"]
        )
        if not prepared_structure_path:
            return None

        prepared_structure = read_molecule(prepared_structure_path)
        prepared_structure = self._postprocess_structure(
            prepared_structure=prepared_structure,
            chain_id=system_dict["protein_chain_id"],
            alternate_location=system_dict["protein_alternate_location"],
            expo_id=system_dict["protein_expo_id"],
            sequence=system_dict["protein_sequence"],
        )

        if self.save_output:
            logging.debug("Saving results ...")
            complex_path = LocalFileStorage.featurizer_result(
                self.__class__.__name__,
                f"{system_dict['protein_name']}_{system_dict['ligand_name']}_complex",
                "pdb",
                self.output_dir,
            )
            write_molecule(prepared_structure.atoms, complex_path)

        return prepared_structure

    def _interpret_system(self, system: ProteinLigandComplex) -> dict:
        """
        Interpret the attributes of the given system components and store them in a dictionary.

        Parameters
        ----------
        system: ProteinSystem or ProteinLigandComplex
            The system to interpret.

        Returns
        -------
        : dict
            A dictionary containing the content of the system components.
        """
        from ..databases.pdb import download_pdb_structure
        from ..core.sequences import AminoAcidSequence

        system_dict = {
            "protein_name": None,
            "protein_pdb_id": None,
            "protein_path": None,
            "protein_sequence": None,
            "protein_uniprot_id": None,
            "protein_chain_id": None,
            "protein_alternate_location": None,
            "protein_expo_id": None,
            "ligand_name": None,
            "ligand_smiles": None,
            "ligand_macrocycle": False,
        }

        logger.debug("Interpreting protein component ...")
        if hasattr(system.protein, "name"):
            system_dict["protein_name"] = system.protein.name

        if hasattr(system.protein, "pdb_id"):
            system_dict["protein_path"] = download_pdb_structure(
                system.protein.pdb_id, self.cache_dir
            )
            if not system_dict["protein_path"]:
                raise ValueError(
                    f"Could not download structure for PDB entry {system.protein.pdb_id}."
                )
        elif hasattr(system.protein, "path"):
            system_dict["protein_path"] = Path(system.protein.path).expanduser().resolve()
        else:
            raise AttributeError(
                f"The {self.__class__.__name__} requires systems with protein components having a"
                f" `pdb_id` or `path` attribute."
            )
        if not hasattr(system.protein, "sequence"):
            if hasattr(system.protein, "uniprot_id"):
                logger.debug(
                    f"Retrieving amino acid sequence details for UniProt entry "
                    f"{system.protein.uniprot_id} ..."
                )
                system_dict["protein_sequence"] = AminoAcidSequence.from_uniprot(
                    system.protein.uniprot_id
                )

        if hasattr(system.protein, "chain_id"):
            system_dict["protein_chain_id"] = system.protein.chain_id

        if hasattr(system.protein, "alternate_location"):
            system_dict["protein_alternate_location"] = system.protein.alternate_location

        if hasattr(system.protein, "expo_id"):
            system_dict["protein_expo_id"] = system.protein.expo_id

        logger.debug("Interpreting ligand component ...")
        if hasattr(system.ligand, "name"):
            system_dict["ligand_name"] = system.ligand.name

        if hasattr(system.ligand, "smiles"):
            system_dict["ligand_smiles"] = system.ligand.smiles

        if hasattr(system.ligand, "macrocycle"):
            system_dict["ligand_macrocycle"] = system.ligand.macrocycle

        return system_dict

    def _preprocess_structure(
        self,
        pdb_path: Union[str, Path],
        chain_id: Union[str, None],
        alternate_location: Union[str, None],
        expo_id: Union[str, None],
        sequence: str,
    ) -> Path:
        """
        Pre-process a structure for SCHRODINGER's prepwizard with the following steps:
         - select chain of interest
         - select alternate location of interest
         - remove all ligands but ligand of interest
         - remove expression tags
         - delete protein alterations differing from given sequence
         - renumber protein residues according to the given sequence

        Parameters
        ----------
        pdb_path: str or Path
            Path to the structure file in PDB format.
        chain_id: str or None
            The chain ID of interest.
        alternate_location: str or None
            The alternate location of interest.
        expo_id: str or None
            The resname of the ligand of interest.
        sequence: str
            The amino acid sequence of the protein.

        Returns
        -------
        : Path
            The path to the cleaned structure.
        """
        from MDAnalysis.core.universe import Merge

        from ..modeling.MDAnalysisModeling import (
            read_molecule,
            select_chain,
            select_altloc,
            remove_non_protein,
            delete_expression_tags,
            delete_short_protein_segments,
            delete_alterations,
            renumber_protein_residues,
            write_molecule,
        )
        from ..utils import LocalFileStorage, sha256_objects

        clean_structure_path = LocalFileStorage.featurizer_result(
            self.__class__.__name__,
            sha256_objects([pdb_path, chain_id, alternate_location, expo_id, sequence]),
            "pdb",
            self.cache_dir,
        )

        if not clean_structure_path.is_file():
            logger.debug("Cleaning structure ...")

            logger.debug("Reading structure from PDB file ...")
            structure = read_molecule(pdb_path)

            if chain_id:
                logger.debug(f"Selecting chain {chain_id} ...")
                structure = select_chain(structure, chain_id)

            if alternate_location:
                logger.debug(f"Selecting alternate location {alternate_location} ...")
                structure = select_altloc(structure, alternate_location)
            else:
                try:  # try to select altloc A, since the prepwizard will not handle altlocs
                    structure = select_altloc(structure, "A")
                    logger.debug(f"Selected default alternate location A.")
                except ValueError:
                    pass

            if expo_id:
                logger.debug(f"Selecting ligand {expo_id} ...")
                structure = remove_non_protein(structure, exceptions=[expo_id])

            logger.debug("Deleting expression tags ...")
            structure = delete_expression_tags(structure, pdb_path)

            logger.debug("Splitting protein and non-protein ...")
            protein = structure.select_atoms("protein")
            not_protein = structure.select_atoms("not protein")

            logger.debug("Deleting short protein segments ...")
            protein = delete_short_protein_segments(protein)

            logger.debug("Deleting alterations in protein ...")
            protein = delete_alterations(protein, sequence)

            logger.debug("Deleting short protein segments 2 ...")
            protein = delete_short_protein_segments(protein)

            logger.debug("Renumbering protein residues ...")
            protein = renumber_protein_residues(protein, sequence)

            logger.debug("Merging cleaned protein and non-protein ...")
            structure = Merge(protein.atoms, not_protein.atoms)

            logger.debug("Writing cleaned structure ...")
            write_molecule(structure.atoms, clean_structure_path)
        else:
            logger.debug("Found cached cleaned structure ...")

        return clean_structure_path

    def _prepare_structure(
        self, input_file: Path, sequence: Union[str, None]
    ) -> Union[Path, None]:
        """
        Prepare the structure with SCHRODINGER's prepwizard.

        Parameters
        ----------
        input_file: Path
            The path to the input structure file in PDB format.
        sequence: str or None
            The amino acid sequence of the protein. If not given, relevant information will be
            used from the PDB header.

        Returns
        -------
        : Path or None
            The path to the prepared structure if successful.
        """

        from ..modeling.SCHRODINGERModeling import run_prepwizard, mae_to_pdb
        from ..utils import LocalFileStorage, sha256_objects

        prepared_structure_path = LocalFileStorage.featurizer_result(
            self.__class__.__name__,
            sha256_objects([input_file, sequence]),
            "pdb",
            self.cache_dir,
        )

        if not prepared_structure_path.is_file():

            for i in range(self.max_retry):
                logger.debug(f"Running prepwizard trial {i + 1}...")
                mae_file_path = (
                    prepared_structure_path.parent / f"{prepared_structure_path.stem}.mae"
                )
                run_prepwizard(
                    schrodinger_directory=self.schrodinger,
                    input_file=input_file,
                    output_file=mae_file_path,
                    cap_termini=True,
                    build_loops=True,
                    sequence=sequence,
                    protein_pH="neutral",
                    propka_pH=7.4,
                    epik_pH=7.4,
                    force_field="3",
                )
                if mae_file_path.is_file():
                    mae_to_pdb(self.schrodinger, mae_file_path, prepared_structure_path)
                    break
        else:
            logger.debug("Found cached prepared structure ...")

        if not prepared_structure_path.is_file():
            logger.debug("Running prepwizard was not successful, returning None ...")
            return None

        return prepared_structure_path

    @staticmethod
    def _postprocess_structure(
        prepared_structure: Universe,
        chain_id: [str, None],
        alternate_location: [str, None],
        expo_id: [str, None],
        sequence: [str, None],
    ):
        """
        Post-process a structure prepared with SCHRODINGER's prepwizard with the following steps:
         - select the chain of interest
         - select the alternate location of interest
         - remove all ligands but the ligands of interest
         - update residue identifiers, e.g. atom indices, chain ID, residue IDs of non-protein

        Parameters
        ----------
        prepared_structure: Universe
           The structure prepared by SCHRODINGER's prepwizard.
        chain_id: str or None
            The chain ID of interest. Will only be used if `sequence` is None.
        alternate_location: str or None
            The alternate location of interest. Will only be used if `sequence` is None.
        expo_id: str or None
            The resname of the ligand of interest. Will only be used if `sequence` is None.
        sequence: str or None
            The amino acid sequence of the protein.

        Returns
        -------
        : Universe
            The post-processed structure.
        """

        from ..modeling.MDAnalysisModeling import (
            select_chain,
            select_altloc,
            remove_non_protein,
            update_residue_identifiers,
        )

        if not sequence:
            if chain_id:
                logger.debug(f"Selecting chain {chain_id} ...")
                prepared_structure = select_chain(prepared_structure, chain_id)
            if alternate_location:
                logger.debug(f"Selecting alternate location {alternate_location} ...")
                prepared_structure = select_altloc(prepared_structure, alternate_location)
            else:
                try:  # try to select altloc A, since the prepwizard will not handle altlocs
                    prepared_structure = select_altloc(prepared_structure, "A")
                    logger.debug(f"Selected default alternate location A.")
                except ValueError:
                    pass
            if expo_id:
                logger.debug(f"Selecting ligand {expo_id} ...")
                prepared_structure = remove_non_protein(prepared_structure, exceptions=[expo_id])

        logger.debug("Updating residue identifiers ...")
        prepared_structure = update_residue_identifiers(prepared_structure)

        return prepared_structure


class SCHRODINGERDockingFeaturizer(SCHRODINGERComplexFeaturizer):
    """
    Given systems with exactly one protein and one ligand dock the ligand into
    the protein structure. The protein structure needs to have a
    co-crystallized ligand to identify the pocket for docking. The following
    steps will be performed.

     - modeling missing loops
     - building missing side chains
     - mutations, if `uniprot_id` or `sequence` attribute is provided for the
       protein component (see below)
     - removing everything but protein, water and ligand of interest
     - protonation at pH 7.4
     - docking a ligand

    The protein component of each system must have a `pdb_id` or a `path`
    attribute specifying the complex structure to prepare.

     - `pdb_id`: A string specifying the PDB entry of interest, required if
       `path` not given.
     - `path`: The path to the structure file, required if `pdb_id` not given.

    Additionally, the protein component can have the following optional
    attributes to customize the protein modeling:
     - `name`: A string specifying the name of the protein, will be used for
       generating the output file name.
     - `chain_id`: A string specifying which chain should be used.
     - `alternate_location`: A string specifying which alternate location
       should be used.
     - `expo_id`: A string specifying the ligand of interest. This is
       especially useful if multiple ligands are present in a PDB structure.
     - `uniprot_id`: A string specifying the UniProt ID that will be used to
       fetch the amino acid sequence from UniProt, which will be used for
       modeling the protein. This will supersede the sequence information
       given in the PDB header.
     - `sequence`: A string specifying the amino acid sequence in single
       letter codes to be used during loop modeling and for mutations.

    The ligand component must be a BaseLigand with smiles attribute:
     - `smiles`: A SMILES representation of the molecule to dock.

    Additionally, the ligand component can have the following optional
    attributes to customize the docking:
     - `name`: A string specifying the name of the ligand, will be used for
       generating the output file name and as molecule title in the docking
       pose SDF file.
     - `macrocycle`: A bool specifying if the ligand shell be sampled as a
       macrocycle during docking. Docking will fail, if SCHRDODINGER does not
       consider the ligand a macrocycle.

    Parameters
    ----------
    cache_dir: str, Path or None, default=None
        Path to directory used for saving intermediate files. If None, default
        location provided by `appdirs.user_cache_dir()` will be used.
    output_dir: str, Path or None, default=None
        Path to directory used for saving output files. If None, output
        structures will not be saved.
    shape_restrain: bool, default=True
        If the docking shell be performed with shape restrain based on the
        co-crystallized ligand.
    max_retry: int, default=3
        The maximal number of attempts to try running the prepwizard and
        docking steps.
    """

    from MDAnalysis.core.universe import Universe

    def __init__(
        self,
        cache_dir: Union[str, Path, None] = None,
        output_dir: Union[str, Path, None] = None,
        max_retry: int = 3,
        shape_restrain: bool = True,
        **kwargs,
    ):
        super().__init__(cache_dir=cache_dir, output_dir=output_dir, max_retry=max_retry, **kwargs)
        self.shape_restrain = shape_restrain

    _SUPPORTED_TYPES = (ProteinLigandComplex,)

    def _featurize_one(self, system: ProteinLigandComplex) -> Union[Universe, None]:
        """
        Prepare a protein structure and dock a ligand.

        Parameters
        ----------
        system: ProteinLigandComplex
            A system object holding a protein and a ligand component.

        Returns
        -------
        : Universe or None
            An MDAnalysis universe of the featurized system or None if not successful.
        """
        from ..modeling.MDAnalysisModeling import write_molecule
        from ..utils import LocalFileStorage

        logger.debug("Interpreting system ...")
        system_dict = self._interpret_system(system)

        if not system_dict["protein_expo_id"]:
            logger.debug("No expo_id given in Protein object needed for docking, returning None!")
            return None

        if system_dict["protein_sequence"]:
            system_dict["protein_path"] = self._preprocess_structure(
                pdb_path=system_dict["protein_path"],
                chain_id=system_dict["protein_chain_id"],
                alternate_location=system_dict["protein_alternate_location"],
                expo_id=system_dict["protein_expo_id"],
                sequence=system_dict["protein_sequence"],
            )

        prepared_structure_path = self._prepare_structure(
            system_dict["protein_path"], system_dict["protein_sequence"]
        )
        if not prepared_structure_path.is_file():
            return None

        docking_pose_path = LocalFileStorage.featurizer_result(
            self.__class__.__name__,
            f"{system_dict['protein_name']}_{system_dict['ligand_name']}_ligand",
            "sdf",
            self.output_dir,
        )
        mae_file_path = prepared_structure_path.parent / f"{prepared_structure_path.stem}.mae"
        if not self._dock_molecule(
            mae_file=mae_file_path,
            output_file_sdf=docking_pose_path,
            ligand_resname=system_dict["protein_expo_id"],
            smiles=system_dict["ligand_smiles"],
            macrocycle=system_dict["ligand_macrocycle"],
        ):
            logger.debug("Failed to generate docking pose ...")
            return None

        prepared_structure = self._replace_ligand(
            pdb_path=prepared_structure_path,
            resname_replace=system_dict["protein_expo_id"],
            docking_pose_sdf_path=docking_pose_path,
        )

        prepared_structure = self._postprocess_structure(
            prepared_structure=prepared_structure,
            chain_id=system_dict["protein_chain_id"],
            alternate_location=system_dict["protein_alternate_location"],
            expo_id="LIG",
            sequence=system_dict["protein_sequence"],
        )

        if self.save_output:
            logging.debug("Saving results ...")
            complex_path = LocalFileStorage.featurizer_result(
                self.__class__.__name__,
                f"{system_dict['protein_name']}_{system_dict['ligand_name']}_complex",
                "pdb",
                self.output_dir,
            )
            write_molecule(prepared_structure.atoms, complex_path)

        return prepared_structure

    def _dock_molecule(
        self,
        mae_file: Path,
        output_file_sdf: Path,
        ligand_resname: str,
        smiles: str,
        macrocycle: bool,
    ) -> bool:
        """
        Dock the molecule into the protein with SCHRODINGER's Glide.

        Parameters
        ----------
        mae_file: Path
            Path to the prepared structure for docking.
        output_file_sdf: Path
            Path to the output docking pose in SDF format.
        ligand_resname: str
            The resname of the ligand defining the binding pocket.
        smiles: str
            The molecule to dock as SMILES representation.
        macrocycle: bool
            If molecule to dock shell be treated as macrocycle during docking.

        Returns
        -------
        : bool
            True if successful, else False.
        """
        from ..docking.SCHRODINGERDocking import run_glide

        for i in range(self.max_retry):
            logger.debug(f"Running docking trial {i + 1}...")
            run_glide(
                schrodinger_directory=self.schrodinger,
                input_file_mae=mae_file,
                output_file_sdf=output_file_sdf,
                ligand_resname=ligand_resname,
                mols_smiles=[smiles],
                mols_names=["LIG"],
                n_poses=1,
                shape_restrain=self.shape_restrain,
                macrocyles=macrocycle,
                precision="XP",
                cache_dir=self.cache_dir,
            )
            if output_file_sdf.is_file():
                return True

        return False

    @staticmethod
    def _replace_ligand(
        pdb_path: Path, resname_replace: str, docking_pose_sdf_path: Path
    ) -> Universe:
        """
        Replace the ligand in a PDB file with a ligand in an SDF file.

        Parameters
        ----------
        pdb_path: Path
            Path to the PDB file of the protein ligand complex.
        resname_replace: str
            The resname of the ligand that shell be removed from the structure.
        docking_pose_sdf_path: Path
            Path to the molecule in SDF format that shell be added to the structure.

        Returns
        -------
        : Universe
            The structure with replaced ligand.
        """
        from tempfile import NamedTemporaryFile

        from MDAnalysis.core.universe import Merge
        from rdkit import Chem

        from ..modeling.MDAnalysisModeling import read_molecule, delete_residues

        logger.debug("Removing co-crystallized ligand ...")
        prepared_structure = read_molecule(pdb_path)
        chain_id = prepared_structure.select_atoms(f"resname {resname_replace}").residues[0].segid
        prepared_structure = prepared_structure.select_atoms(f"not resname {resname_replace}")

        with NamedTemporaryFile(mode="w", suffix=".pdb") as docking_pose_pdb_path:
            logger.debug("Converting docking pose SDF to PDB ...")
            mol = next(Chem.SDMolSupplier(str(docking_pose_sdf_path), removeHs=False))
            Chem.MolToPDBFile(mol, docking_pose_pdb_path.name)

            logger.debug("Readind docking pose and renaming residue ...")
            docking_pose = read_molecule(docking_pose_pdb_path.name)
            for atom in docking_pose.atoms:
                atom.residue.resname = "LIG"
                atom.segment.segid = chain_id

            logger.debug("Adding docking pose to structure ...")
            prepared_structure = Merge(prepared_structure, docking_pose.atoms)

            logger.debug("Deleting water clashing with docking pose ...")
            clashing_water = prepared_structure.select_atoms(
                "(resname HOH and element O) and around 1.5 resname LIG"
            )
            if len(clashing_water) > 0:
                prepared_structure = delete_residues(prepared_structure, clashing_water)

        return prepared_structure
