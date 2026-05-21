#!/usr/bin/env python3
"""Full test suite for the jewelry stone shape transformation pipeline.

All 7 tests use synthetic .3dm files -- no real jewelry files required.
Tests pass on rhino3dm==8.17.0.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional

import rhino3dm

from jewelry_transform import (
    parse_file,
    fingerprint_object,
    build_library,
    transform,
)

SHAPES = ["RD", "OV", "PE", "EM", "PR", "MQ"]   # stone abbreviations
FAMILY = "test_family"
N_STATIC = 4
N_MUTABLE = 7


def _sphere_brep(x: float, y: float, z: float, radius: float = 1.0) -> rhino3dm.Brep:
    brep = rhino3dm.Brep.CreateFromSphere(
        rhino3dm.Sphere(rhino3dm.Point3d(x, y, z), radius)
    )
    assert brep is not None, f"Failed to create sphere Brep at ({x},{y},{z})"
    return brep


def _hash_brep(brep: rhino3dm.Brep) -> Optional[str]:
    """Return sig_hash for a raw Brep by wrapping it in a temporary model."""
    tmp = rhino3dm.File3dm()
    tmp.Objects.AddBrep(brep)
    fp = fingerprint_object(list(tmp.Objects)[0])
    return fp["sig_hash"] if fp else None


def _static_breps() -> list[rhino3dm.Brep]:
    """4 spheres at fixed world positions -- identical fingerprint in every file."""
    return [_sphere_brep(float(i * 10), 0.0, 0.0) for i in range(N_STATIC)]


def _mutable_breps(shape_idx: int) -> list[rhino3dm.Brep]:
    """7 spheres at positions unique to this shape variant (y offset by shape_idx*100)."""
    return [
        _sphere_brep(float(j * 2), float(shape_idx * 100 + j), 5.0)
        for j in range(N_MUTABLE)
    ]


def _make_synthetic_file(shape: str, path: Path) -> None:
    shape_idx = SHAPES.index(shape)
    model = rhino3dm.File3dm()
    for brep in _static_breps():
        model.Objects.AddBrep(brep)
    for brep in _mutable_breps(shape_idx):
        model.Objects.AddBrep(brep)
    model.Write(str(path), 7)


def _count_objects(path: Path) -> int:
    """Count fingerprint-valid objects in a .3dm file without requiring a shape code."""
    model = rhino3dm.File3dm.Read(str(path))
    return sum(1 for obj in model.Objects if fingerprint_object(obj) is not None)


class TestJewelryPipeline(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        # Dataset layout: tmpdir/test_family/ER1_Halo_{shape}.3dm
        self.fam_dir = self.tmpdir / FAMILY
        self.fam_dir.mkdir()
        self.files = {
            shape: self.fam_dir / f"ER1_Halo_{shape}.3dm"
            for shape in SHAPES
        }
        self.lib_dir = self.tmpdir / "shape_library"

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_synthetic_file_creation(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        for shape, path in self.files.items():
            with self.subTest(shape=shape):
                self.assertTrue(path.exists(), f"File not created: {path}")

    def test_02_parsing_object_count(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        for shape, path in self.files.items():
            with self.subTest(shape=shape):
                pf = parse_file(str(path))
                self.assertEqual(len(pf.fingerprints), N_STATIC + N_MUTABLE)

    def test_03_classification_static_vs_mutable(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        static_hashes_expected = {_hash_brep(b) for b in _static_breps()}
        for shape, path in self.files.items():
            hashes = {fp["sig_hash"] for fp in parse_file(str(path)).fingerprints}
            with self.subTest(shape=shape):
                self.assertEqual(len(hashes & static_hashes_expected), N_STATIC)
                self.assertEqual(len(hashes - static_hashes_expected), N_MUTABLE)

    def test_04_library_build_writes_json(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        build_library(dataset_root=self.tmpdir, library_dir=self.lib_dir)

        fam_lib = self.lib_dir / FAMILY
        classification = json.loads((fam_lib / "classification.json").read_text())
        shape_index = json.loads((fam_lib / "shape_index.json").read_text())

        self.assertEqual(len(classification["combined_static_hashes"]), N_STATIC)
        self.assertEqual(set(shape_index.keys()), set(SHAPES))
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertEqual(shape_index[shape]["mutable_object_count"], N_MUTABLE)

    def test_05_transform_round_to_pear_creates_output(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        build_library(dataset_root=self.tmpdir, library_dir=self.lib_dir)

        output = self.tmpdir / "ER1_Halo_PE_out.3dm"
        transform(
            source_path=str(self.files["RD"]),
            target_shape="PE",
            output_path=str(output),
            library_dir=self.lib_dir,
            family=FAMILY,
        )
        self.assertTrue(output.exists())

    def test_06_geometry_verification_static_preserved_mutable_replaced(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        build_library(dataset_root=self.tmpdir, library_dir=self.lib_dir)

        output = self.tmpdir / "ER1_Halo_PE_verify.3dm"
        transform(
            source_path=str(self.files["RD"]),
            target_shape="PE",
            output_path=str(output),
            library_dir=self.lib_dir,
            family=FAMILY,
        )

        result_hashes = {fp["sig_hash"] for fp in parse_file(str(output)).fingerprints}
        static_hashes = {_hash_brep(b) for b in _static_breps()}
        pear_mutable  = {_hash_brep(b) for b in _mutable_breps(SHAPES.index("PE"))}
        round_mutable = {_hash_brep(b) for b in _mutable_breps(SHAPES.index("RD"))}

        self.assertTrue(static_hashes.issubset(result_hashes), "Static objects not preserved")
        self.assertTrue(pear_mutable.issubset(result_hashes), "PE mutable not inserted")
        self.assertFalse(result_hashes & round_mutable, "RD mutable should not be in output")
        self.assertEqual(_count_objects(output), N_STATIC + N_MUTABLE)

    def test_07_round_trip_all_shapes(self):
        for shape, path in self.files.items():
            _make_synthetic_file(shape, path)
        build_library(dataset_root=self.tmpdir, library_dir=self.lib_dir)

        for target in SHAPES:
            with self.subTest(target=target):
                output = self.tmpdir / f"ER1_Halo_{target}_rt.3dm"
                transform(
                    source_path=str(self.files["RD"]),
                    target_shape=target,
                    output_path=str(output),
                    library_dir=self.lib_dir,
                    family=FAMILY,
                )
                self.assertTrue(output.exists())
                self.assertEqual(_count_objects(output), N_STATIC + N_MUTABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
