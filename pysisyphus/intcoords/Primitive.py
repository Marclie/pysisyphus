import abc
from math import exp

import numpy as np

from pysisyphus.elem_data import COVALENT_RADII as CR


class Primitive(metaclass=abc.ABCMeta):

    def __init__(self, indices, periodic=False, calc_kwargs=None):
        self.indices = list(indices)
        self.periodic = periodic
        if calc_kwargs is None:
            calc_kwargs = ()
        self.calc_kwargs = calc_kwargs

    @staticmethod
    def parallel(u, v, thresh=1e-6):
        dot = u.dot(v) / (np.linalg.norm(u) * np.linalg.norm(v))
        return (1 - abs(dot)) < thresh

    @abc.abstractmethod
    def _calculate(*, coords3d, indices, gradient, **kwargs):
        pass

    @abc.abstractmethod
    def _weight(self, atoms, coords3d, f_damping):
        pass

    def weight(self, atoms, coords3d, f_damping=0.12):
        return self._weight(atoms, coords3d, f_damping)

    @staticmethod
    def rho(atoms, coords3d, indices):
        i, j = indices
        distance = np.linalg.norm(coords3d[i]-coords3d[j])
        cov_rad_sum = CR[atoms[i].lower()] + CR[atoms[j].lower()]
        return exp(-(distance / cov_rad_sum - 1))

    def calculate(self, coords3d, indices=None, gradient=False):
        if indices is None:
            indices = self.indices

        # Gather calc_kwargs
        calc_kwargs = {key: getattr(self, key) for key in self.calc_kwargs}

        return self._calculate(
                    coords3d=coords3d,
                    indices=indices,
                    gradient=gradient,
                    **calc_kwargs,
        )

    def jacobian(self, coords3d, indices=None):
        if indices is None:
            indices = self.indices

        # Gather calc_kwargs
        calc_kwargs = {key: getattr(self, key) for key in self.calc_kwargs}

        return self._jacobian(
                    coords3d=coords3d,
                    indices=indices,
                    **calc_kwargs,
        )

    def __str__(self):
        return f"{self.__class__.__name__}({self.indices})"

    def __repr__(self):
        return self.__str__()
