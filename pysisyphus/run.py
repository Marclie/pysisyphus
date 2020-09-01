#!/usr/bin/env python3

import argparse
from collections import namedtuple
import copy
import itertools
import os
from math import ceil, floor
from pathlib import Path
from pprint import pprint
import re
import shutil
import sys
import textwrap
import time

from distributed import Client
from natsort import natsorted
import numpy as np
import pytest
import yaml

from pysisyphus.calculators import *
from pysisyphus.cos import *
from pysisyphus.cos.GrowingChainOfStates import GrowingChainOfStates
from pysisyphus.color import bool_color
# from pysisyphus.overlaps.Overlapper import Overlapper
# from pysisyphus.overlaps.couplings import couplings
# from pysisyphus.overlaps.sorter import sort_by_overlaps
from pysisyphus.Geometry import Geometry
from pysisyphus.helpers import confirm_input, shake_coords, \
                               highlight_text, do_final_hessian, print_barrier, \
                               get_tangent_trj_str
from pysisyphus.irc import *
from pysisyphus.stocastic import *
from pysisyphus.init_logging import init_logging
from pysisyphus.intcoords.helpers import form_coordinate_union
from pysisyphus.intcoords.fragments import merge_fragments
from pysisyphus.intcoords.findbonds import get_bond_sets
from pysisyphus.optimizers import *
from pysisyphus.tsoptimizers import *
from pysisyphus.trj import get_geoms, dump_geoms
from pysisyphus.tsoptimizers.dimer import dimer_method
from pysisyphus._version import get_versions
from pysisyphus.xyzloader import write_geoms_to_trj


CALC_DICT = {
    "afir": AFIR,
    "dimer": Dimer,
    # "ext": ExternalPotential,
    "g09": Gaussian09.Gaussian09,
    "g16": Gaussian16,
    "mopac": MOPAC,
    "oniom": ONIOM,
    "openmolcas": OpenMolcas.OpenMolcas,
    "orca": ORCA,
    "psi4": Psi4,
    "turbomole": Turbomole,
    "xtb": XTB,
    # Analytical potentials
    # "anapot": AnaPot,
}

try:
    from pysisyphus.calculators.PySCF import PySCF
    CALC_DICT["pyscf"] = PySCF
except ImportError:
    pass

COS_DICT = {
    "neb": NEB.NEB,
    "aneb": AdaptiveNEB.AdaptiveNEB,
    "feneb": FreeEndNEB.FreeEndNEB,
    "szts": SimpleZTS.SimpleZTS,
    "gs": GrowingString.GrowingString,
    "fs": FreezingString.FreezingString,
}

OPT_DICT = {
    "cg": ConjugateGradient.ConjugateGradient,
    "bfgs": BFGS.BFGS,
    "fire": FIRE.FIRE,
    "lbfgs": LBFGS.LBFGS,
    "nc": NCOptimizer.NCOptimizer,
    "oniom": ONIOMOpt,
    "plbfgs": PreconLBFGS.PreconLBFGS,
    "psd": PreconSteepestDescent.PreconSteepestDescent,
    "qm": QuickMin.QuickMin,
    "rfo": RFOptimizer.RFOptimizer,
    "sd": SteepestDescent.SteepestDescent,
    "sqnm": StabilizedQNMethod.StabilizedQNMethod,
    "string": StringOptimizer.StringOptimizer,
}

TSOPT_DICT = {
    "dimer": dimer_method,
    "rsprfo": RSPRFOptimizer,
    "trim": TRIM,
    "rsirfo": RSIRFOptimizer,
}

IRC_DICT = {
    "dvv": DampedVelocityVerlet,
    "euler": Euler,
    "eulerpc": EulerPC,
    "gs": GonzalesSchlegel,
    "imk": IMKMod,
    "lqa": LQA,
    "modekill": ModeKill,
    "rk4": RK4,
}

STOCASTIC_DICT = {
    "frag": FragmentKick,
    "kick": Kick,
}


def parse_args(args):
    parser = argparse.ArgumentParser()

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("yaml", nargs="?",
        help="Start pysisyphus with input from a YAML file."
    )
    action_group.add_argument("--clean", action="store_true",
        help="Ask for confirmation before cleaning."
    )
    action_group.add_argument("--fclean", action="store_true",
        help="Force cleaning without prior confirmation."
    )
    action_group.add_argument("--bibtex", action="store_true",
        help="Print bibtex string for pysisyphus paper."
    )
    # action_group.add_argument("--couplings", type=int, nargs="+",
        # help="Create coupling elements."
    # )
    # action_group.add_argument("--sort-by-overlaps", nargs=2,
                              # metavar=("ENERGIES", "OVERLAPS"),
        # help="Sort precomputed energies of (excited) states by precomputed "
             # "overlaps."
    # )
    action_group.add_argument("-v", "--version", action="store_true",
        help="Print pysisyphus version."
    )

    run_type_group = parser.add_mutually_exclusive_group(required=False)
    run_type_group.add_argument("--dryrun", action="store_true",
        help="Only generate a sample input (if meaningful) for checking."
    )
    run_type_group.add_argument("--restart", action="store_true",
        help="Continue a previously crashed/aborted/... pysisphus run."
    )
    # run_type_group.add_argument("--overlaps", action="store_true",
        # help="Calculate overlaps between transition density matrices "
             # "(tden) or wavefunctions (wf)."
    # )
    run_type_group.add_argument("--cp", "--copy",
        help="Copy .yaml file and corresponding geometries to a "
             "new directory. Similar to TURBOMOLEs cpc command."
    )

    parser.add_argument("--scheduler", default=None,
        help="Address of the dask scheduler."
    )
    # parser.add_argument("--consider-first", type=int, default=None,
        # help="Consider the first N states for sorting according "
             # "to overlaps."
    # )

    return parser.parse_args()


def get_calc(index, base_name, calc_key, calc_kwargs, iter_dict=None):
    if iter_dict is None:
        iter_dict = dict()

    # Some calculators are just wrappers that modify the forces from
    # actual calculators, e.g. AFIR and Dimer. If we find the 'calc'
    # key we create the actual calculator and assign it to the 'calculator'
    # key in calc_kwargs.
    if "calc" in calc_kwargs:
        actual_kwargs = calc_kwargs.pop("calc")
        actual_key = actual_kwargs.pop("type")
        actual_calc = get_calc(index, base_name, actual_key, actual_kwargs)
        calc_kwargs["calculator"] = actual_calc

    kwargs_copy = calc_kwargs.copy()
    kwargs_copy["base_name"] = base_name
    kwargs_copy["calc_number"] = index
    for key, iter_ in iter_dict.items():
        kwargs_copy[key] = next(iter_)
    return CALC_DICT[calc_key](**kwargs_copy)


def get_calc_closure(base_name, calc_key, calc_kwargs):
    index = 0
    def calc_getter():
        nonlocal index
        # Expand values that contain the $IMAGE pattern over all images.
        # We have to use a copy of calc_kwargs to keep the $IMAGE pattern.
        # Otherwise it would be replace at it's first occurence and would
        # be gone in the following items.
        kwargs_copy = copy.deepcopy(calc_kwargs)
        for key, value in kwargs_copy.items():
            if not isinstance(value, str) or not ("$IMAGE" in value):
                continue
            kwargs_copy[key] = value.replace("$IMAGE", f"{index:03d}")
        kwargs_copy["base_name"] = base_name
        kwargs_copy["calc_number"] = index
        index += 1
        return CALC_DICT[calc_key](**kwargs_copy)
    return calc_getter


def run_tsopt_from_cos(cos, tsopt_key, tsopt_kwargs, calc_getter=None,
                       ovlp_thresh=.3):
    print(highlight_text(f"Running TS-optimization from COS"))

    first_cos_energy = cos.images[0].energy
    last_cos_energy = cos.images[-1].energy

    # Use plain HEI
    hei_index = cos.get_hei_index()
    print(f"Index of highest energy image (HEI) is {hei_index}.")
    print()
    hei_image = cos.images[hei_index]
    try:
        prim_indices = hei_image.internal.prim_indices
    except AttributeError:
        def get_int_geom(geom):
            return Geometry(geom.atoms, geom.cart_coords, coord_type="redund")
        internal_geom1 = get_int_geom(cos.images[0])
        internal_geom2 = get_int_geom(cos.images[-1])
        prim_indices = form_coordinate_union(internal_geom1, internal_geom2)
    ts_geom = Geometry(hei_image.atoms, hei_image.cart_coords,
                       coord_type="redund",
                       coord_kwargs={"prim_indices": prim_indices,},
    )

    hei_tangent = cos.get_tangent(hei_index)
    # Convert tangent to redundant internals
    if cos.coord_type == "dlc":
        redund_tangent = hei_image.internal.Ut_inv @ hei_tangent
    elif cos.coord_type == "cart":
        redund_tangent = ts_geom.internal.B_prim @ hei_tangent
    else:
        raise Exception("Unknown coord_type!")
    # The tangent is probably not normalized anymore
    redund_tangent /= np.linalg.norm(redund_tangent)

    # Also create a cartesian tangent
    atoms = cos.images[0].atoms
    cart_images = list()
    for image in cos.images:
        cart_image = Geometry(atoms, image.cart_coords)
        cart_image.energy = image.energy
        cart_images.append(cart_image)
    cart_cos = ChainOfStates.ChainOfStates(cart_images)
    cart_hei_index = cart_cos.get_hei_index()
    cart_hei_tangent = cart_cos.get_tangent(cart_hei_index)
    cart_hei_fn = "cart_hei_tangent"
    np.savetxt(cart_hei_fn, cart_hei_tangent)
    trj = get_tangent_trj_str(ts_geom.atoms, ts_geom.cart_coords,
                              cart_hei_tangent, points=10
    )
    trj_fn = cart_hei_fn + ".trj"
    with open(trj_fn, "w") as handle:
        handle.write(trj)
    print(f"Wrote animated HEI tangent to {trj_fn}\n")

    # Use splined HEI
    # hei_coords, hei_energy, hei_tangent = cos.get_splined_hei()
    # ts_geom = Geometry(cos.images[0].atoms, hei_coords, coord_type="redund")

    # Print HEI information (coords & tangent)
    print("Splined HEI (TS guess)")
    print(ts_geom.as_xyz())
    print()
    dummy = Geometry(atoms, cart_hei_tangent)
    print("Cartesian HEI tangent")
    print(dummy.as_xyz())
    print()

    # Write out HEI information (coords & tangent)
    hei_xyz_fn = "splined_hei.xyz"
    with open(hei_xyz_fn, "w") as handle:
        handle.write(ts_geom.as_xyz())
    print(f"Wrote splined HEI coordinates to '{hei_xyz_fn}'")
    hei_tangent_fn = "splined_hei_tangent"
    np.savetxt(hei_tangent_fn, hei_tangent)
    print(f"Wrote splined HEI tangent to '{hei_tangent_fn}'")

    ts_calc = calc_getter()
    ts_geom.set_calculator(ts_calc)
    ts_optimizer = TSOPT_DICT[tsopt_key]
    dimer_kwargs = tsopt_kwargs.pop("dimer_kwargs", {})
    do_hess = tsopt_kwargs.pop("do_hess", False)

    if tsopt_key == "dimer":
        # Right now Dimer optimization is rectricted to cartesian
        # rotations and translations, even though translation in
        # internals would be possible.
        ts_geom = Geometry(hei_image.atoms, hei_image.cart_coords)
        dimer_kwargs.update({
            "N_raw": cart_hei_tangent,
            "base_name": "dimer",
        })
        dimer_calc = Dimer(ts_calc, **dimer_kwargs)
        ts_geom.set_calculator(dimer_calc)
        ts_opt = PreconLBFGS.PreconLBFGS(ts_geom, **tsopt_kwargs)
    else:
        # Determine which imaginary mode has the highest overlap
        # with the splined HEI tangent.
        eigvals, eigvecs = np.linalg.eigh(ts_geom.hessian)
        neg_inds = eigvals < -1e-4
        eigval_str = np.array2string(eigvals[neg_inds], precision=6)
        print(f"Negative eigenvalues at splined HEI:\n{eigval_str}")
        neg_eigvals = eigvals[neg_inds]
        neg_eigvecs = eigvecs.T[neg_inds]
        ovlps = [np.abs(imag_mode.dot(redund_tangent)) for imag_mode in neg_eigvecs]
        print("Overlaps between HEI tangent and imaginary modes:")
        for i, ov in enumerate(ovlps):
            print(f"\t{i:02d}: {ov:.6f}")
        max_ovlp_ind = np.argmax(ovlps)
        print(f"Imaginary mode {max_ovlp_ind} has highest overlap with splined "
                "HEI tangent."
        )
        max_ovlp = ovlps[max_ovlp_ind]
        if max_ovlp >= ovlp_thresh:
            root = np.argmax(ovlps)
        else:
            root = neg_eigvals.argmin()
            neg_eigval = neg_eigvals[root]
            print(f"Highest overlap {max_ovlp:.6f} is below the threshold "
                  f"of {ovlp_thresh:.6f}. Selecting mode {root} with most "
                  f"negative eigenvalue {neg_eigval:.6f} instead."
            )
        # Use mode with highest overlap as initial root
        tsopt_kwargs["root"] = root
        ts_opt = ts_optimizer(ts_geom, prefix="ts", **tsopt_kwargs)

    ts_opt.run()
    # Restore original calculator for Dimer calculations
    if tsopt_key == "dimer":
        ts_geom.set_calculator(ts_calc)

    print(f"Optimized TS coords:")
    print(ts_geom.as_xyz())
    ts_opt_fn = "ts_opt.xyz"
    with open(ts_opt_fn, "w") as handle:
        handle.write(ts_geom.as_xyz())
    print(f"Wrote TS geometry to '{ts_opt_fn}'")

    if do_hess and not ts_opt.stopped:
        print()
        do_final_hessian(ts_geom, write_imag_modes=True)

    ts_energy = ts_geom.energy
    print()
    print_barrier(ts_energy, first_cos_energy, "TS", "first COS image")
    print_barrier(ts_energy, last_cos_energy, "TS", "last COS image")

    print()
    return ts_geom, ts_opt


def run_calculations(geoms, calc_getter, path, calc_key, calc_kwargs,
                     scheduler=None, assert_track=False):
    print("Running calculations")
    def par_calc(geom):
        geom.calculator.run_calculation(geom.atoms, geom.coords)
        return geom

    for i, geom in enumerate(geoms):
        geom.set_calculator(calc_getter(i))
    if assert_track:
        assert all([geom.calculator.track for geom in geoms]), \
            "'track: True' must be present in calc section."

    if scheduler:
        client =  Client(scheduler, pure=False, silence_logs=False)
        geom_futures = client.map(par_calc, geoms)
        geoms = client.gather(geom_futures)
    else:
        for i, geom in enumerate(geoms):
            start = time.time()
            geom.calculator.run_calculation(geom.atoms, geom.coords)
            if i < (len(geoms)-2):
                try:
                    cur_calculator = geom.calculator
                    next_calculator = geoms[i+1].calculator
                    next_calculator.set_chkfiles(cur_calculator.get_chkfiles())
                except AttributeError:
                    print("Calculators don't support set/get_chkfiles!")
            end = time.time()
            diff = end - start
            print(f"Ran calculation {i+1:02d}/{len(geoms):02d} in {diff:.1f} s.")
            sys.stdout.flush()
    return geoms


# def get_overlapper(run_dict):
    # try:
        # calc_key = run_dict["calc"].pop("type")
    # except KeyError:
        # print("Creating Overlapper without calc_key.")
        # calc_key = None
    # calc_kwargs = run_dict["calc"]
    # cwd = Path(".")
    # ovlp_with = run_dict["overlaps"]["ovlp_with"]
    # prev_n = run_dict["overlaps"]["prev_n"]
    # overlapper = Overlapper(cwd, ovlp_with, prev_n, calc_key, calc_kwargs)
    # return overlapper


# def restore_calculators(run_dict):
    # overlapper = get_overlapper(run_dict)

    # cwd = Path(".")
    # glob = run_dict["overlaps"]["glob"]
    # regex = run_dict["overlaps"]["regex"]
    # # First try globbing
    # if glob:
        # paths = natsorted([p for p in cwd.glob(glob)])
        # if len(paths) == 0:
            # raise Exception("Couldn't find any paths! Are you sure that your "
                           # f"glob '{glob}' is right?")
        # xyz_fns = [list(p.glob("*.xyz"))[0] for p in paths]
        # geoms = [geom_from_xyz_file(xyz) for xyz in xyz_fns]
        # if regex:
            # mobjs = [re.search(regex, str(path)) for path in paths]
            # calc_numbers = [int(mobj[1]) for mobj in mobjs]
            # assert len(calc_numbers) == len(paths)
        # else:
            # calc_numbers = range(len(paths))
        # [overlapper.set_files_from_dir(geom, path, calc_number)
         # for calc_number, geom, path in zip(calc_numbers, geoms, paths)]
    # else:
        # # Otherwise check if geometries are defined in the run_dict
        # if run_dict["xyz"]:
            # geoms = get_geoms(run_dict["xyz"])
        # else:
            # # Else resort to globbing arbitrary xyz files
            # xyz_fns = [str(p) for p in cwd.glob("*.xyz")]
            # geoms = [geom_from_xyz_file(xyz) for xyz in xyz_fns]
        # # geoms = geoms[:2]
        # calc_num = overlapper.restore_calculators(geoms)
        # geoms = geoms[:calc_num]
    # return overlapper, geoms


# def overlaps(run_dict, geoms=None):
    # pickle_path = Path("pickles")
    # if pickle_path.is_file() and confirm_input("Load pickled geoms?"):
        # with open(pickle_path, "rb") as handle:
            # overlapper, *geoms = cloudpickle.load(handle)
        # print(f"Loaded overlap and {len(geoms)} from {str(pickle_path)}.")
    # else:
        # if not geoms:
            # overlapper, geoms = restore_calculators(run_dict)
        # else:
            # overlapper = get_overlapper(run_dict)

        # to_pickle = [overlapper] + geoms
        # with open(pickle_path, "wb") as handle:
            # cloudpickle.dump(to_pickle, handle)

    # ovlp_dict = run_dict["overlaps"]
    # ovlp_type = ovlp_dict["type"]
    # double_mol = ovlp_dict["ao_ovlps"]
    # recursive = ovlp_dict["recursive"]
    # consider_first = ovlp_dict["consider_first"]
    # skip = ovlp_dict["skip"]

    # if ovlp_type == "wf" and double_mol:
        # print("!"*10)
        # print("WFOverlaps with true AO overlaps seem faulty right now!")
        # print("!"*10)


    # overlapper.overlaps_for_geoms(geoms,
                                  # ovlp_type=ovlp_type,
                                  # double_mol=double_mol,
                                  # recursive=recursive,
                                  # consider_first=consider_first,
                                  # skip=skip,)


def run_stocastic(stoc):
    # Fragment
    stoc.run()
    print()

    return stoc


def run_preopt(xyz, calc_getter, preopt_key, preopt_kwargs):
    """Run optimization on first and last geometry in xyz and return
    updated xyz variable containing the optimized ends and any
    intermediate image that was present in the original list."""
    strict = preopt_kwargs.pop("strict", False)
    coord_type = preopt_kwargs.pop("coord_type", "redund")
    preopt = preopt_kwargs.pop("preopt", "both")
    assert preopt in "first last both".split()
    first = (0, "first")
    last = (-1, "last")
    to_preopt = {
        "both": (first, last),
        "first": (first, ),
        "last": (last, ),
    }

    geoms = get_geoms(xyz, coord_type=coord_type)
    assert len(geoms) >= 2, "Need at least two geometries!"

    middle_geoms = geoms[1:-1]
    middle_fn = None
    if len(middle_geoms) > 0:
        middle_fn = "middle_for_preopt.trj"
        write_geoms_to_trj(middle_geoms, middle_fn)

    out_xyz = list()
    for ind, str_ in to_preopt[preopt]:
        geom = geoms[ind]
        prefix = f"{str_}_pre"
        opt_kwargs = preopt_kwargs.copy()
        opt_kwargs.update({
            "prefix": prefix,
            "h5_group_name": prefix,
        })
        _, opt = run_opt(geom, calc_getter, preopt_key, opt_kwargs,
                         title=f"{str_} preoptimization")

        # Continue with next pre-optimization when stopped manually
        if strict and not opt.stopped and not opt.is_converged:
            print(f"Problem in preoptimization of {str_}. Exiting!")
            sys.exit()
        print(f"Preoptimization of {str_} geometry converged!")
        opt_fn = f"{str_}_preopt.xyz"
        shutil.move(opt.final_fn, opt_fn)
        print(f"Saved final preoptimized structure to '{opt_fn}'.")
        out_xyz.append(opt_fn)
        print()

    if preopt == "last":
        fn = "first_not_preopt.xyz"
        with open(fn, "w") as handle:
            handle.write(geoms[0].as_xyz())
        out_xyz.insert(0, fn)
    if middle_fn:
        out_xyz.insert(1, middle_fn)
    if preopt == "first":
        fn = "last_not_preopt.xyz"
        with open(fn, "w") as handle:
            handle.write(geoms[-1].as_xyz())
        out_xyz.append(fn)
    print()

    return out_xyz


def get_opt_cls(opt_key):
    try:
        opt_cls = OPT_DICT[opt_key]
    except KeyError:
        opt_cls = TSOPT_DICT[opt_key]
    return opt_cls


def run_opt(geom, calc_getter, opt_key, opt_kwargs,
            title="Optimization", copy_final_geom=None):
    is_cos = issubclass(type(geom), ChainOfStates.ChainOfStates)

    if is_cos:
        for i, image in enumerate(geom.images):
            image.set_calculator(calc_getter(i))
            title = str(geom)
    else:
        geom.set_calculator(calc_getter(0))

    do_hess = opt_kwargs.pop("do_hess", False)

    opt = get_opt_cls(opt_key)(geom, **opt_kwargs)
    print(highlight_text(f"Running {title}"))
    opt.run()

    # ChainOfStates specific
    if is_cos and (not opt.stopped):
        hei_coords, hei_energy, hei_tangent, hei_frac_index = geom.get_splined_hei()
        floor_ind = floor(hei_frac_index)
        ceil_ind = ceil(hei_frac_index)
        print(f"Splined HEI is at {hei_frac_index:.2f}/{len(geom.images)-1:.2f}, "
              f"between image {floor_ind} and {ceil_ind} (0-based indexing).")
        hei_geom = Geometry(geom.images[0].atoms, hei_coords)
        hei_geom.energy = hei_energy
        hei_fn = "splined_hei.xyz"
        with open(hei_fn, "w") as handle:
            handle.write(hei_geom.as_xyz())
        print(f"Wrote splined HEI to '{hei_fn}'")

    if copy_final_geom and opt.is_converged:
        copy_fn = copy_final_geom
        shutil.copy(opt.final_fn, copy_fn)
        print(f"Copied '{opt.final_fn}' to '{copy_fn}'.")

    if do_hess and (not opt.stopped):
        print()
        prefix = opt_kwargs.get("prefix", "")
        do_final_hessian(geom, write_imag_modes=True, prefix=prefix)
    print()

    return opt.geometry, opt


def run_irc(geom, irc_key, irc_kwargs, calc_getter):
    print(highlight_text(f"Running IRC"))

    # Avoids modifying the supplied geom. When the supplied geom originated
    # from a previous TS optimization we want to retain it unmodified.
    geom = geom.copy()

    calc = calc_getter(0)
    calc.base_name = "irc"
    geom.set_calculator(calc)

    # Recreate geometry with Cartesian coordinates if needed.
    if geom.coord_type != "cart":
        geom = geom.copy_all(coord_type="cart")

    irc = IRC_DICT[irc_key](geom, **irc_kwargs)
    irc.run()

    return irc


def run_endopt(geom, irc, endopt_key, endopt_kwargs, calc_getter):
    print(highlight_text(f"Optimizing IRC ends"))

    # Gather geometries that shall be optimized
    to_opt = list()
    if irc.forward:
        coords = irc.all_coords[0]
        to_opt.append((coords, "forward_end"))
    if irc.backward:
        coords = irc.all_coords[-1]
        to_opt.append((coords, "backward_end"))
    if irc.downhill:
        coords = irc.all_coords[-1]
        to_opt.append((coords, "downhill_end"))

    def to_frozensets(sets):
        return [frozenset(_) for _ in sets]

    separate_fragments = endopt_kwargs.pop("fragments", False)
    # Convert to array for easy indexing with the fragment lists
    atoms = np.array(geom.atoms)
    fragments_to_opt = list()
    for coords, base_name in to_opt:
        c3d = coords.reshape(-1, 3)
        if separate_fragments:
            bond_sets = to_frozensets(get_bond_sets(atoms.tolist(), c3d))
            # Sort atom indices, so the atoms don't become totally scrambled.
            fragments = [sorted(frag) for frag in merge_fragments(bond_sets)]
            # Disable higher fragment counts. I'm looking forward to the day
            # this ever occurs and someone complains :)
            assert len(fragments) < 10, "Something probably went wrong"
            fragment_names = [f"{base_name}_frag{i:03d}"
                              for i, _ in enumerate(fragments)]
            print(f"Found {len(fragments)} fragment(s) at {base_name}")
            for frag_name, frag in zip(fragment_names, fragments):
                print(f"\t{frag_name}: {len(frag)} atoms")
        # Optimize the full geometries, without splitting them into fragments
        else:
            # Atom indices of the fragment atoms
            fragments = [range(len(atoms)), ]
            fragment_names = [base_name, ]
        fragment_atoms = [tuple(atoms[list(frag)]) for frag in fragments]
        fragment_coords = [c3d[frag].flatten() for frag in fragments]
        fragments_to_opt.extend(
            list(zip(fragment_names, fragment_atoms, fragment_coords))
        )
        print()
    to_opt = fragments_to_opt

    coord_type = endopt_kwargs.pop("coord_type", "redund")
    opt_geoms = list()
    for name, atoms, coords in to_opt:
        geom = Geometry(atoms, coords, coord_type=coord_type)

        def wrapped_calc_getter(calc_number):
            calc = calc_getter(calc_number)
            calc.base_name = name
            return calc

        opt_kwargs = endopt_kwargs.copy()
        opt_kwargs.update({
            "prefix": name,
            "h5_group_name": name,
            "dump": True,
        })
        _, opt = run_opt(geom, wrapped_calc_getter, endopt_key, opt_kwargs,
                         title=f"{name} Optimization")
        opt_fn = f"{name}_opt.xyz"
        shutil.move(opt.final_fn, opt_fn)
        print(f"Moved '{opt.final_fn.name}' to '{opt_fn}'.")
        print()
        opt_geoms.append(geom)
    print()
    return opt_geoms


def copy_yaml_and_geometries(run_dict, yaml_fn, destination, new_yaml_fn=None):
    try:
        print(f"Trying to create directory '{destination}' ... ", end="")
        os.mkdir(destination)
        print("done")
    except FileExistsError:
        print("already exists")
    xyzs = run_dict["xyz"]
    print("Copying:")
    # When newlines are present we have an inline xyz formatted string
    if not "\n" in xyzs:
        if isinstance(xyzs, str):
            xyzs = [xyzs, ]
        for xyz in xyzs:
            if xyz.startswith("lib:"):
                continue
            shutil.copy(xyz, destination)
            print("\t", xyz)
    else:
        print("Found inline xyz formatted string. No files to copy!")
    shutil.copy(yaml_fn, destination)
    print("\t", yaml_fn)


def get_defaults(conf_dict):
    # Defaults
    dd = {
        "interpol": {
            "type": None,
            "between": 0,
        },
        "cos": None,
        "calc": {
            "pal": 1,
        },
        "preopt": None,
        "endopt": None,
        "opt": None,
        "tsopt": None,
        # "overlaps": None,
        "glob": None,
        "stocastic": None,
        "xyz": None,
        "coord_type": "cart",
        "shake": None,
        "irc": None,
        "add_prims": None,
        "assert": None,
    }

    mol_opt_defaults = {
        "type": "rfo",
        "dump": True,
        "overachieve_factor": 3,
        "max_cycles": 100,
    }
    cos_opt_defaults = {
        "type": "qm",
        "align": True,
        "dump": True,
    }

    if "cos" in conf_dict:
        dd["cos"] = {
            "type": "neb",
            "fix_ends": True,
        }
        dd["opt"] = cos_opt_defaults.copy()
    # Use a different, more powerful, optimizer when we are not dealing
    # with a COS-optimization.
    elif "opt" in conf_dict:
        dd["opt"] = mol_opt_defaults.copy()
    # elif "overlaps" in conf_dict:
        # dd["overlaps"] = {
            # "type": "tden",
            # "ao_ovlps": None,
            # "glob": None,
            # "recursive": False,
            # "consider_first": None,
            # "skip": 0,
            # "regex": None,
            # "ovlp_with": "previous",
            # "prev_n": 0,
        # }
    elif "stocastic" in conf_dict:
        dd["stocastic"] = {
            "type": "frag",
        }

    if "tsopt" in conf_dict:
        dd["tsopt"] = {
            "type": "rsprfo",
            "dump": True,
            "overachieve_factor": 3,
            "h5_group_name": "tsopt",
        }

    if "preopt" in conf_dict:
        # We can't just copy dd["opt"] because there will probably be
        # some COS specific optimizer, but we just wan't to optimize the
        # (molecular) endpoints.
        dd["preopt"] = mol_opt_defaults.copy()
        dd["preopt"].update({
            # Optimization specific
            # We are a bit more cautious here
            "max_cycles": 100,
            "thresh": "gau_loose",
            "trust_max": 0.3,
            # Preopt specific
            "preopt": "both",
            "strict": False,
            "coord_type": "redund",
        })

    if "endopt" in conf_dict:
        dd["endopt"] = mol_opt_defaults.copy()
        dd["endopt"].update({
            "thresh": "gau",
            "fragments": False,
        })

    if "shake" in conf_dict:
        dd["shake"] = {
            "scale": 0.1,
            "seed": None,
        }

    if "irc" in conf_dict:
        dd["irc"] = {
            "type": "eulerpc",
            "rms_grad_thresh": 1e-3,
        }

    if "assert" in conf_dict:
        dd["assert"] = {}

    return dd


def get_last_calc_cycle():
    def keyfunc(path):
        return re.match("image_\d+.(\d+).out", str(path))[1]
    cwd = Path(".")
    calc_logs = [str(cl) for cl in cwd.glob("image_*.*.out")]
    calc_logs = sorted(calc_logs, key=keyfunc)
    grouped = itertools.groupby(calc_logs, key=keyfunc)
    # Find the last completly finished cycle.
    last_length = 0
    last_calc_cycle = 0
    for calc_cycle, group in grouped:
        cycle_length = len(list(group))
        if cycle_length < last_length:
            # When this is True we have a cycle that has less
            # items than last one, that is an unfinished cycle.
            break
        last_length = cycle_length
        last_calc_cycle = int(calc_cycle)
    if last_calc_cycle == 0:
        print("Can't find any old calculator logs.")
    print(f"Last calculation counter is {last_calc_cycle}.")
    return last_calc_cycle


def setup_run_dict(run_dict):
    org_dict = run_dict.copy()

    # Load defaults to have a sane baseline
    run_dict = get_defaults(run_dict)
    # Update nested entries that are dicts by themselves
    key_set = set(org_dict.keys())
    for key in key_set & set(("cos", "opt", "interpol", "overlaps",
                              "stocastic", "tsopt", "shake", "irc",
                              "preopt", "endopt", "assert", "geom")):
        try:
            run_dict[key].update(org_dict[key])
        except TypeError:
            print(f"Using default values for '{key}' section.")
    # Update non nested entries
    for key in key_set & set(("calc", "xyz", "pal", "coord_type",
                              "add_prims")):
        run_dict[key] = org_dict[key]
    return run_dict


def dry_run(calc, geom):
    atoms, c3d = geom.atoms, geom.coords3d
    inp = calc.prepare_input(atoms, c3d.flatten(), "force")
    if not inp:
        print("Calculator does not use an explicit input file!")
        return
    with open(calc.inp_fn, "w") as handle:
        handle.write(inp)
    print(f"Wrote input to {calc.inp_fn}.")


RunResult = namedtuple(
                "RunResult",
                (
                 "preopt_xyz "
                 "cos cos_opt "
                 "ts_geom ts_opt "
                 "end_geoms irc "
                 "opt_geom opt "
                 "calced_geoms stocastic "
                 "calc_getter "
                ),
)


def main(run_dict, restart=False, yaml_dir="./", scheduler=None,
         dryrun=None):
    if run_dict["interpol"]:
        interpolate = run_dict["interpol"]["type"]
        between = run_dict["interpol"]["between"]
    # Preoptimization prior to COS optimization
    if run_dict["preopt"]:
        preopt_key = run_dict["preopt"].pop("type")
        preopt_kwargs = run_dict["preopt"]
    # Optimization of fragments after IRC integration
    if run_dict["endopt"]:
        endopt_key = run_dict["endopt"].pop("type")
        endopt_kwargs= run_dict["endopt"]
    if run_dict["opt"]:
        opt_key = run_dict["opt"].pop("type")
        opt_kwargs = run_dict["opt"]
    if run_dict["cos"]:
        cos_key = run_dict["cos"].pop("type")
        cos_kwargs = run_dict["cos"]
        cos_kwargs["scheduler"] = scheduler
    if run_dict["stocastic"]:
        stoc_key = run_dict["stocastic"].pop("type")
        stoc_kwargs = run_dict["stocastic"]
    if run_dict["tsopt"]:
        tsopt_key = run_dict["tsopt"].pop("type")
        tsopt_kwargs = run_dict["tsopt"]
    if run_dict["irc"]:
        irc_key = run_dict["irc"].pop("type")
        irc_kwargs = run_dict["irc"]

    xyz = run_dict["xyz"]

    if restart:
        print("Trying to restart calculation. Skipping interpolation.")
        between = 0
        # Load geometries of latest cycle
        cwd = Path(".")
        trjs = [str(trj) for trj in cwd.glob("cycle_*.trj")]
        if len(trjs) == 0:
            print("Can't restart. Found no previous coordinates.")
            sys.exit()
        xyz = natsorted(trjs)[-1]
        last_cycle = int(re.search("(\d+)", xyz)[0])
        print(f"Last cycle was {last_cycle}.")
        print(f"Using '{xyz}' as input geometries.")
        opt_kwargs["last_cycle"] = last_cycle
        last_calc_cycle = get_last_calc_cycle()
        run_dict["calc"]["last_calc_cycle"] = last_calc_cycle

    # Prepare calculator
    calc_key = run_dict["calc"].pop("type")
    calc_kwargs = run_dict["calc"]
    calc_kwargs["out_dir"] = yaml_dir
    calc_getter_kwargs = {
        "base_name": "image",
        "calc_key": calc_key,
        "calc_kwargs": calc_kwargs,
    }
    if calc_key == "oniom":
        geoms = get_geoms(xyz, quiet=True)
        calc_getter_kwargs["iter_dict"] = {"geom": iter(geoms),}
    calc_getter = lambda index: get_calc(index, **calc_getter_kwargs)
    # Create second function that returns a wrapped calculator. This may be
    # useful if we later want to drop the wrapper and use the actual calculator.
    if "calc" in calc_kwargs:
        act_calc_kwargs = calc_kwargs["calc"].copy()
        act_calc_key = act_calc_kwargs.pop("type")
        act_calc_getter = lambda index: get_calc(index, "image",
                                                 act_calc_key, act_calc_kwargs)

    if (not dryrun) and run_dict["preopt"]:
        preopt_xyz = run_preopt(xyz, calc_getter, preopt_key, preopt_kwargs)
        # Update xyz list with optimized endpoint filenames
        xyz = preopt_xyz
        sys.stdout.flush()

    add_prims = run_dict["add_prims"]
    coord_type = run_dict["coord_type"]
    geoms = get_geoms(xyz, interpolate, between, coord_type=coord_type,
                      define_prims=add_prims)
    if between and len(geoms) > 1:
        dump_geoms(geoms, "interpolated")

    if dryrun:
        calc = calc_getter(0)
        dry_run(calc, geoms[0])
        return

    if run_dict["cos"]:
        cos_cls = COS_DICT[cos_key]
        if (issubclass(cos_cls, GrowingChainOfStates)
            or isinstance(cos_cls, type(FreezingString))):
            cos_kwargs["calc_getter"] = get_calc_closure("image", calc_key, calc_kwargs)
        geom = COS_DICT[cos_key](geoms, **cos_kwargs)
    else:
        assert len(geoms) == 1
        geom = geoms[0]

    if run_dict["stocastic"]:
        stoc_kwargs["calc_kwargs"] = calc_kwargs
        stocastic = STOCASTIC_DICT[stoc_key](geom, **stoc_kwargs)
        stocastic = run_stocastic(stocastic)
    # This case will handle most pysisyphus runs. A full run encompasses
    # the following steps:
    #
    #    (0. Preoptimization, already handled)
    #     1. (COS)-Optimization
    #     2. TS-Optimization by TSHessianOptimizer or Dimer method
    #     3. IRC integration
    #     4. Optimization of IRC endpoints
    #
    # Everything can be chained. All functions operate on the 'geom' object,
    # which is propagated along through all functions calls.
    #
    # All keys are present in 'run_dict', but most of the corresponding values will
    # be set to zero.
    elif any([run_dict[key] is not None for key in ("opt", "tsopt", "irc", "endopt")]):

        #######
        # OPT #
        #######

        if run_dict["opt"]:
            if run_dict["shake"]:
                shaked_coords = shake_coords(geom.coords, **run_dict["shake"])
                geom.coords = shaked_coords
            opt_geom, opt = run_opt(geom, calc_getter, opt_key, opt_kwargs)
            # Keep a backup of the optimized geometry
            if isinstance(opt_geom, ChainOfStates.ChainOfStates):
                # Set some variables that are later collected into RunResult
                cos = opt_geom
                cos_opt = opt
                # copy() is not present for ChainOfState objects, so we just keep
                # using the COS object with a different name.
                geom = opt_geom
            else:
                geom = opt_geom.copy()

        #########
        # TSOPT #
        #########

        if run_dict["tsopt"]:
            # Use a separate implementation for TS-Optimizations started from
            # COS-optimizations.
            if isinstance(geom, ChainOfStates.ChainOfStates):
                ts_calc_getter = get_calc_closure(tsopt_key, calc_key, calc_kwargs)
                ts_geom, ts_opt = run_tsopt_from_cos(geom, tsopt_key, tsopt_kwargs,
                                                     ts_calc_getter
                )
            else:
                ts_geom, ts_opt = run_opt(geom, calc_getter, tsopt_key, tsopt_kwargs,
                                          title="TS-Optimization",
                                          copy_final_geom="ts_opt.xyz"
                )
            geom = ts_geom.copy()

        #######
        # IRC #
        #######

        ran_irc = False
        if run_dict["irc"]:
            # After a Dimer run we continue with the actual calculator
            # and not the Dimer calculator.
            if calc_key == "dimer":
                calc_getter = act_calc_getter
            irc = run_irc(geom, irc_key, irc_kwargs, calc_getter)
            ran_irc = True

        ##########
        # ENDOPT #
        ##########

        # Only run 'endopt' when a previous IRC calculation was done
        if ran_irc and run_dict["endopt"]:
            end_geoms = run_endopt(geom, irc, endopt_key, endopt_kwargs, calc_getter)
    # Fallback when no specific job type was specified
    else:
        calced_geoms = run_calculations(geoms, calc_getter, yaml_dir,
                                        calc_key, calc_kwargs, scheduler)

    # We can't use locals() in the dict comprehension, as it runs in its own
    # local scope.
    locals_ = locals()
    results = {key: locals_.get(key, None) for key in RunResult._fields}
    run_result = RunResult(**results)
    return run_result


def check_asserts(results, run_dict):
    print(highlight_text(f"Asserting results"))

    assert_ = run_dict["assert"]
    keys = list(assert_.keys())
    objs_attrs = [key.split(".") for key in keys]
    ref_vals = [assert_[k] for k in keys]
    matches = list()
    for i, ((obj, attr), ref_val) in enumerate(zip(objs_attrs, ref_vals)):
        cur_val = getattr(getattr(results, obj), attr)
        matched = cur_val == pytest.approx(ref_val)
        print(f"{i:02d}: {obj}.{attr}")
        print(f"\tReference: {ref_val}")
        print(f"\tCurrent: {cur_val}")
        print(f"\tMatches: {bool_color(matched)}")
        matches.append(matched)

    assert all(matches)
    print()


def do_clean(force=False):
    """Deletes files from previous runs in the cwd.
    A similar function could be used to store everything ..."""
    cwd = Path(".").resolve()
    rm_globs = (
        "cycle*.trj",
        "interpolated.trj",
        "interpolated.image*.xyz",
        "calculator.log",
        "optimizer.log",
        "tsoptimizer.log",
        "wfoverlap.log",
        "host_*.calculator.log",
        "host_*.wfoverlap.log",
        "wfo_*.out"
        "optimization.trj",
        "cos.log",
        "*.gradient",
        "optimizer_results.yaml",
        # ORCA specific
        "*orca.gbw",
        "*orca.cis",
        "*orca.engrad",
        "*orca.hessian",
        "*orca.inp",
        # OpenMOLCAS specific
        "calculator*.out",
        "calculator*.JobIph",
        "calculator*.RasOrb",
        "*rasscf.molden",
        # Turbomole specific
        "calculator_*.control",
        "calculator_*.coord",
        "calculator_*.mos",
        "calculator_*.ciss_a",
        "calculator*.sing_a",
        "*wavefunction.molden",
        "*input.xyz",
        "*.coord",
        # PySCF specific
        "calculator*.chkfile",
        # WFOverlap specific
        "wfo_*.*.out",
        # XTB specific
        "image*.grad",
        "calculator*.grad",
        "image_*",
        "splined_ts_guess.xyz",
        "dimer_ts.xyz",
        "dimer_pickle",
        "interpolated.geom_*.xyz",
        # Wavefunction overlap
        "wfo_*",
        "image*.molden",
        "jmol.spt",
        "overlap_data.h5",
        "*_CDD.png",
        "*_CDD.cub",
        "internal_coords.log",
        "hei_tangent",
        "optimization.trj",
        "splined_hei.xyz",
        "ts_opt.xyz",
        "final_geometry.xyz",
        "calculated_init_hessian",
        "cur_out",
        # HDF5 files
        "optimization.h5",
        "afir.h5",
        # Optimization files
        "*_optimization.trj",
        # Preopt files
        "first_*",
        "last_*",
        # TSOpt
        "rsirfo*",
        # IRC files
        "irc_*",
        "finished_*",
        # IRC/Endopt files
        "backward_*",
        "forward_*",
        # Misc
        "*.log",
        "*imaginary_mode_*.trj",
        "cart_hei_tangent",
        "ts_calculated_init_cart_hessian",
        "calculated_final_cart_hessian",
        "*final_geometry.xyz",
        "*final_geometries.trj",
        "current_geometry.xyz",
        "*current_geometries.trj",
        "hess_calc_cyc*.h5",
        "ts_hess_calc_cyc*.h5",
        "hess_init_irc.h5",
        "final_hessian.h5",
    )
    to_rm_paths = list()
    for glob in rm_globs:
        to_rm_paths.extend(list(cwd.glob(glob)))
    to_rm_strs = [str(p) for p in to_rm_paths]
    for s in to_rm_strs:
        print(s)

    def delete():
        for p in to_rm_paths:
            try:
                os.remove(p)
                print(f"Deleted {p}")
            except FileNotFoundError:
                pass
    if force:
        delete()
        return
    # If we dont force the cleaning ask for confirmation first
    elif to_rm_paths and confirm_input("Delete these files?"):
        delete()
    else:
        print("No files found for removal.")


def print_header():
    """Generated from https://asciiartgen.now.sh/?s=pysisyphus&style=colossal"""
    logo = """                           d8b                            888
                           Y8P                            888
                                                          888
88888b.  888  888 .d8888b  888 .d8888b  888  888 88888b.  88888b.  888  888 .d8888b
888 "88b 888  888 88K      888 88K      888  888 888 "88b 888 "88b 888  888 88K
888  888 888  888 "Y8888b. 888 "Y8888b. 888  888 888  888 888  888 888  888 "Y8888b.
888 d88P Y88b 888      X88 888      X88 Y88b 888 888 d88P 888  888 Y88b 888      X88
88888P"   "Y88888  88888P' 888  88888P'  "Y88888 88888P"  888  888  "Y88888  88888P'
888           888                            888 888
888      Y8b d88P                       Y8b d88P 888
888       "Y88P"                         "Y88P"  888                            """
    version = f"Version {get_versions()['version']}"
    print(f"{logo}\n\n{version}\n")


def print_bibtex():
    bibtex = textwrap.dedent("""@article{Steinmetzer2020,
        doi = {10.1002/qua.26390},
        url = {https://doi.org/10.1002/qua.26390},
        year = {2020},
        month = aug,
        publisher = {Wiley},
        author = {Johannes Steinmetzer and Stephan Kupfer and Stefanie Gräfe},
        title = {pysisyphus: Exploring potential energy surfaces in ground and excited states},
        journal = {International Journal of Quantum Chemistry}
    }""")
    print(bibtex)


def run_from_dict(run_dict, cwd=None, set_defaults=True, yaml_fn=None, cp=None,
                  scheduler=None, clean=False, fclean=False, version=False,
                  restart=False, dryrun=False):
    if cwd is None:
        cwd = Path(".")

    start_time = time.time()
    print_header()

    init_logging(cwd, scheduler)
    # Load defaults etc.
    if set_defaults:
        run_dict = setup_run_dict(run_dict)
    sys.stdout.flush()

    if cp:
        copy_yaml_and_geometries(run_dict, yaml_fn, cp)
        return
    elif clean:
        do_clean()
        return
    elif fclean:
        do_clean(force=True)
        return
    elif version:
        return

    # Citation
    citation = "If pysisyphus benefitted your research please cite:\n\n" \
               "\thttps://doi.org/10.1002/qua.26390\n\nGood luck!\n"
    print(citation)

    run_dict_without_none = {k: v for k, v in run_dict.items()
                             if v is not None}
    pprint(run_dict_without_none)
    print()
    sys.stdout.flush()

    # if args.overlaps:
        # overlaps(run_dict)
    # elif args.couplings:
        # couplings(args.couplings)
    # elif args.sort_by_overlaps:
        # energies_fn, max_ovlp_inds_fn = args.sort_by_overlaps
        # consider_first = args.consider_first
        # sort_by_overlaps(energies_fn, max_ovlp_inds_fn, consider_first)
    # else:
        # main(run_dict, args.restart, yaml_dir, args.scheduler, args.dryrun)
    run_result = main(run_dict, restart, cwd, scheduler, dryrun)

    if run_dict["assert"] is not None:
        print()
        check_asserts(run_result, run_dict)

    end_time = time.time()
    duration = int(end_time - start_time)
    print(f"pysisyphus run took {duration}s.")

    return run_result


def run():
    args = parse_args(sys.argv[1:])

    # Defaults
    run_dict = {}
    yaml_dir = Path(".")

    if args.yaml:
        yaml_dir = Path(os.path.abspath(args.yaml)).parent
        with open(args.yaml) as handle:
            yaml_str = handle.read()
        run_dict = yaml.load(yaml_str, Loader=yaml.SafeLoader)
    elif args.bibtex:
        print_bibtex()
        return

    run_kwargs = {
        "cwd": yaml_dir,
        "set_defaults": True,
        "yaml_fn": args.yaml,
        "cp": args.cp,
        "scheduler": args.scheduler,
        "clean": args.clean,
        "fclean": args.fclean,
        "version": args.version,
        "restart": args.restart,
        "dryrun": args.dryrun,
    }
    return run_from_dict(run_dict, **run_kwargs)


if __name__ == "__main__":
    run()
