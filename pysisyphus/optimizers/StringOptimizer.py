# [1] https://aip.scitation.org/doi/abs/10.1063/1.3664901
#     Behn, 2011, Freezing string method
# [2] https://aip.scitation.org/doi/pdf/10.1063/1.4804162
#     Zimmerman, 2013, Growing string with interpolation and optimization
#                      in internal coordiantes

import numpy as np

from pysisyphus.constants import ANG2BOHR
from pysisyphus.helpers import procrustes
from pysisyphus.optimizers.Optimizer import Optimizer
from pysisyphus.optimizers.closures import bfgs_multiply


class StringOptimizer(Optimizer):

    def __init__(self, geometry, gamma=1.25, max_step=0.1,
                 stop_in_when_full=-1, **kwargs):
        super().__init__(geometry, max_step=max_step, **kwargs)

        assert self.is_cos, \
            "StringOptimizer is only intended to be used with COS objects."

        # gamma = 1.25 Hartree/Bohr² ~ 5 Hartree/Angstrom²
        self.gamma = float(gamma)
        self.stop_in_when_full = int(stop_in_when_full)

        # Add one as we later subtract 1 before we check if this value
        # is 0.
        self.stop_in = self.stop_in_when_full + 1
        self.is_cart_opt = self.geometry.coord_type == "cart"

    def prepare_opt(self):
        if self.align and self.is_cart_opt:
            procrustes(self.geometry)

    def reset(self):
        pass

    def restrict_step_components(self, steps):
        too_big = np.abs(steps) > self.max_step
        self.log(f"Found {np.sum(too_big)} big step components.")
        signs = np.sign(steps[too_big])
        steps[too_big] = signs * self.max_step
        return steps

    def check_convergence(self, *args, **kwargs):
        # Normal convergence check with gradients etc.
        converged = super().check_convergence(*args, **kwargs)

        if self.geometry.fully_grown:
            self.stop_in -= 1
            self.log(f"String is fully grown. Stopping in {self.stop_in} cycles.")

        full_stop = self.geometry.fully_grown and (self.stop_in == 0)
        # full_stop will take precedence when True
        return full_stop or converged

    def optimize(self):
        # Raises IndexError in cycle 0 and evaluates to False when
        # string size changed (grew) from previous cycle.
        try:
            string_size_changed = self.geometry.coords.size != self.coords[-2].size
        except IndexError:
            string_size_changed = True

        if self.align and string_size_changed and self.is_cart_opt:
            procrustes(self.geometry)
            self.log("Aligned string.")

        forces = self.geometry.forces
        self.energies.append(self.geometry.energy)
        self.forces.append(forces)

        self.log(f"norm(forces)={np.linalg.norm(forces)*ANG2BOHR:.6f} hartree/Å")

        sd_step = forces / self.gamma
        # Steepest descent in the beginning
        if (self.cur_cycle == 0) or string_size_changed:
            step = sd_step
            self.log("Taking steepest descent step.")
        # Conjugate Gradient later one
        else:
            cur_norm = np.linalg.norm(forces)
            prev_norm = np.linalg.norm(self.forces[-2])
            quot = min(cur_norm**2 / prev_norm**2, 1)
            step = sd_step + quot*self.steps[-1]
            self.log("Taking conjugate gradient step.")

        step = self.restrict_step_components(step)

        return step
