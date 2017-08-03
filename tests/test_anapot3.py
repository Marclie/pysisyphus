#!/usr/bin/env python3

import copy

import numpy as np
from pytest import approx

from pysisyphus.AnimPlot import AnimPlot
from pysisyphus.calculators.AnaPot3 import AnaPot3
from pysisyphus.cos.NEB import NEB
from pysisyphus.cos.SimpleZTS import SimpleZTS
from pysisyphus.Geometry import Geometry
from pysisyphus.optimizers.BFGS import BFGS
from pysisyphus.optimizers.FIRE import FIRE
from pysisyphus.optimizers.SteepestDescent import SteepestDescent
from pysisyphus.optimizers.NaiveSteepestDescent import NaiveSteepestDescent

KWARGS = {
    "images": 10,
    "max_cycles": 5,
    #"convergence": {
    #    "max_step_thresh": 3e-3,
    #    "rms_step_thresh": 7e-4,
    #},
}


def get_geoms():
    initial = np.array((-0.5, 0.5, 0))
    final = np.array((0.5, 0.5, 0))
    coords = (initial, final)
    atoms = ("H")
    geoms = [Geometry(atoms, c) for c in coords]
    return geoms


def run_cos_opt(cos, Opt, images, **kwargs):
    cos.interpolate(images)
    opt = Opt(cos, **kwargs)
    for img in cos.images:
        img.set_calculator(AnaPot3())
    opt.run()

    return opt


def animate(opt):
    xlim = (-1.5, 1.5)
    ylim = (-0.5, 1.5)
    levels = (0, 2, 20)
    interval = 750
    ap = AnimPlot(
            AnaPot3(), opt, xlim=xlim, ylim=ylim,
            levels=levels, interval=interval
    )
    ap.animate()


def test_steepest_descent_neb():
    kwargs = copy.copy(KWARGS)
    #kwargs["max_cycles"] = 27
    neb = NEB(get_geoms())
    opt = run_cos_opt(neb, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt


def test_steepest_descent_neb_more_images():
    kwargs = copy.copy(KWARGS)
    #kwargs["max_cycles"] = 24
    kwargs["images"] = 10
    neb = NEB(get_geoms())
    opt = run_cos_opt(neb, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt



def test_fire_neb():
    kwargs = copy.copy(KWARGS)
    #kwargs["max_cycles"] = 21
    neb = NEB(get_geoms())
    opt = run_cos_opt(neb, FIRE, **kwargs)
    
    assert(opt.rms_steps[-1] == approx(0.006848, rel=1e-4))

    return opt


def test_bfgs_neb():
    kwargs = copy.copy(KWARGS)
    kwargs["max_cycles"] = 18
    neb = NEB(get_geoms())
    opt = run_cos_opt(neb, BFGS, **kwargs)

    assert(opt.is_converged)

    return opt


def test_bfgs_neb_more_images():
    kwargs = copy.copy(KWARGS)
    kwargs["max_cycles"] = 18
    kwargs["images"] = 10
    neb = NEB(get_geoms())
    opt = run_cos_opt(neb, BFGS, **kwargs)

    assert(opt.is_converged)

    return opt


def test_equal_szts():
    kwargs = copy.copy(KWARGS)
    #kwargs["max_cycles"] = 26
    convergence = {
        "max_force_thresh": 0.1,
    }
    kwargs["convergence"] = convergence
    szts_equal = SimpleZTS(get_geoms(), param="equal")
    opt = run_cos_opt(szts_equal, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt


def test_equal_szts_more_images():
    kwargs = copy.copy(KWARGS)
    kwargs["max_cycles"] = 28
    kwargs["images"] = 10
    convergence = {
        "max_force_thresh": 0.1,
    }
    kwargs["convergence"] = convergence
    szts_equal = SimpleZTS(get_geoms(), param="equal")
    opt = run_cos_opt(szts_equal, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt


def test_energy_szts():
    kwargs = copy.copy(KWARGS)
    kwargs["max_cycles"] = 27
    convergence = {
        "max_force_thresh": 0.1,
    }
    kwargs["convergence"] = convergence
    szts_energy = SimpleZTS(get_geoms(), param="energy")
    opt = run_cos_opt(szts_energy, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt


def test_energy_szts_more_images():
    kwargs = copy.copy(KWARGS)
    kwargs["max_cycles"] = 28
    kwargs["images"] = 10
    convergence = {
        "max_force_thresh": 0.1,
    }
    kwargs["convergence"] = convergence
    szts_energy = SimpleZTS(get_geoms(), param="energy")
    opt = run_cos_opt(szts_energy, SteepestDescent, **kwargs)

    assert(opt.is_converged)

    return opt


if __name__ == "__main__":
    # Steepest Descent
    opt = test_steepest_descent_neb()
    #opt = test_steepest_descent_neb_more_images()

    # FIRE
    #opt = test_fire_neb()

    # BFGS
    #opt = test_bfgs_neb()
    #opt = test_bfgs_neb_more_images()

    # SimpleZTS
    #opt = test_equal_szts()
    #opt = test_equal_szts_more_images()
    #opt = test_energy_szts()
    #opt = test_energy_szts_more_images()

    animate(opt)
