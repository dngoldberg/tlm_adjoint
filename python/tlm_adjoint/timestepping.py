#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright(c) 2018 The University of Edinburgh
#
# This file is part of tlm_adjoint.
# 
# tlm_adjoint is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.
# 
# tlm_adjoint is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
# 
# You should have received a copy of the GNU Lesser General Public License
# along with tlm_adjoint.  If not, see <https://www.gnu.org/licenses/>.

from .base import *

from collections import OrderedDict

from .caches import Constant, DirichletBC, Function
from .equations import AssignmentSolver, Equation, EquationSolver

__all__ = \
  [
    "FinalTimeLevel",
    "N",
    "StaticConstant",
    "StaticDirichletBC",
    "StaticFunction",
    "TimeFunction",
    "TimeLevel",
    "TimeLevels",
    "TimeSystem",
    "TimesteppingException",
    "n"
  ]

# Aim for a degree of consistency with timestepping API, as per git revision
# cddfbc0a6769df17bfd78a4488bca581d7793286 (dolfin-adjoint branch
# timestepping_2017.1.0)

class TimesteppingException(Exception):
  pass

class BaseTimeLevel:
  def __init__(self, order, i):
    self._order = order
    self._i = i
  
  def __hash__(self):
    return hash((self._order, self._i))
  
  def __eq__(self, other):
    if isinstance(other, BaseTimeLevel):
      return self._order == other._order and self._i == other._i
    else:
      return NotImplemented
  
  def __lt__(self, other):
    if isinstance(other, BaseTimeLevel):
      if self._order < other._order:
        return True
      elif self._order > other._order:
        return False
      else:
        return self._i < other._i
    else:
      return NotImplemented
  
  def __gt__(self, other):
    if isinstance(other, BaseTimeLevel):
      if self._order > other._order:
        return True
      elif self._order < other._order:
        return False
      else:
        return self._i > other._i
    else:
      return NotImplemented
  
  def __ne__(self, other):
    return not self == other
  
  def __le__(self, other):
    return not self > other
  
  def __ge__(self, other):
    return not self < other

class InitialTimeLevel(BaseTimeLevel):
  def __init__(self, arg = 0):
    BaseTimeLevel.__init__(self, order = -1, i = arg)
      
  def __add__(self, other):
    return InitialTimeLevel(self._i + other)
  
  def __sub__(self, other):
    return InitialTimeLevel(self._i - other)

class TimeLevel(BaseTimeLevel):
  def __init__(self, arg = 0):
    BaseTimeLevel.__init__(self, order = 0, i = arg)
      
  def __add__(self, other):
    return TimeLevel(self._i + other)
  
  def __sub__(self, other):
    return TimeLevel(self._i - other)

class FinalTimeLevel(BaseTimeLevel):
  def __init__(self, arg = 0):
    BaseTimeLevel.__init__(self, order = 1, i = arg)
      
  def __add__(self, other):
    return FinalTimeLevel(self._i + other)
  
  def __sub__(self, other):
    return FinalTimeLevel(self._i - other)

n = TimeLevel()
N = FinalTimeLevel()

class TimeLevels:
  def __init__(self, levels, cycle_map):
    levels = tuple(sorted(list(set(levels))))    
    cycle_map = OrderedDict(sorted([(target_level, source_level) for target_level, source_level in cycle_map.items()], key = lambda i : i[0]))
    
    self._levels = levels
    self._cycle_map = cycle_map

  def __iter__(self):
    for level in self._levels:
      yield level
  
  def __len__(self):
    return len(self._levels)

class TimeFunction:
  def __init__(self, levels, *args, **kwargs):
    # Note that this keeps references to the Function objects on each time level
    self._fns = OrderedDict()
    for level in levels:
      fn = base_Function(*args, **kwargs)
      fn._tlm_adjoint__tfn = self
      fn._tlm_adjoint__level = level
      self._fns[level] = fn
      
      initial_level = InitialTimeLevel(level._i)
      initial_fn = self._fns[initial_level] = fn.copy(deepcopy = False)
      initial_fn._tlm_adjoint__tfn = self
      initial_fn._tlm_adjoint__level = initial_level
      
      final_level = FinalTimeLevel(level._i)
      final_fn = self._fns[final_level] = fn.copy(deepcopy = False)
      final_fn._tlm_adjoint__tfn = self
      final_fn._tlm_adjoint__level = final_level
      
    self._levels = levels
    self._cycle_eqs = None
  
  def __getitem__(self, key):
    if isinstance(key, BaseTimeLevel):
      return self._fns[key]
    else:
      return self._fns[InitialTimeLevel(key)]
    
  def __len__(self):
    return len(self._fns)
  
  def levels(self):
    return self._levels
  
  def cycle(self, manager = None):
    if self._cycle_eqs is None:
      self._cycle_eqs = [AssignmentSolver(self[source_level], self[target_level])
                           for target_level, source_level in self._levels._cycle_map.items()]
    for eq in self._cycle_eqs:
      eq.solve(manager = manager, replace = False)
 
class StaticConstant(Constant):
  def __init__(self, *args, **kwargs):
    Constant.__init__(self, static = True, *args, **kwargs)
 
class StaticFunction(Function):
  def __init__(self, *args, **kwargs):
    Function.__init__(self, static = True, *args, **kwargs)
 
class StaticDirichletBC(DirichletBC):
  def __init__(self, *args, **kwargs):
    DirichletBC.__init__(self, static = True, *args, **kwargs)

class TimeSystem:
  def __init__(self):
    self._state = "initial"
    self._initial_eqs = []
    self._timestep_eqs = []
    self._final_eqs = []
    self._sorted_eqs = None
    self._tfns = None
  
  def add_assignment(self, y, x):  
    self.add_solve(y, x)
  
  def add_solve(self, *args, **kwargs):
    if self._state != "initial":
      raise TimesteppingException("Invalid state")
      
    if len(args) == 1 and isinstance(args[0], Equation):
      eq = args[0]
    elif len(args) == 2 and isinstance(args[0], base_Function) and isinstance(args[1], base_Function) and hasattr(args[1], "_tlm_adjoint__tfn"):
     eq = AssignmentSolver(args[0], args[1])
    else:
     eq = EquationSolver(*args, **kwargs)
  
    X = eq.X()
    level = X[0]._tlm_adjoint__level
    for x in X[1:]:
      if not isinstance(x._tlm_adjoint__level, level):
        raise TimesteppingException("Inconsistent time levels")
    if isinstance(level, TimeLevel):
      self._timestep_eqs.append(eq)
    elif isinstance(level, InitialTimeLevel):
      self._initial_eqs.append(eq)
    elif isinstance(level, FinalTimeLevel):
      self._final_eqs.append(eq)
    else:
      raise TimesteppingException("Invalid time level: %s" % level)
  
  def assemble(self, initialise = True):
    self._state = "assembled"
  
    if self._sorted_eqs is None:
      for eqs in [self._initial_eqs, self._timestep_eqs, self._final_eqs]:
        x_ids = set()
        for eq in eqs:
          for x in eq.X():
            x_id = x.id()
            if x_id in x_ids:
              raise TimesteppingException("Duplicate solve")
            x_ids.add(x_id)
        del(x_ids)

      # Dependency resolution
      def add_eq_deps(eq, eq_xs, eqs, parent_ids = None):
        X = eq.X()
        process = False
        for x in X:
          if x in eq_xs:
            process = True
            break
        if not process:
          return
        if parent_ids is None:
          parent_ids = set()
        for x in X:
          parent_ids.add(x.id())
        for dep in eq.dependencies():
          if not dep in X and hasattr(dep, "_tlm_adjoint__tfn"):
            if dep.id() in parent_ids:
              raise TimesteppingException("Circular dependency")
            elif dep in eq_xs:
              add_eq_deps(eq_xs[dep], eq_xs, eqs, parent_ids)
        eqs.append(eq)
        del(eq_xs[x])
        for x in X:
          parent_ids.remove(x.id())

      self._sorted_eqs = [[], [], []]
      for i, eqs in enumerate([self._initial_eqs, self._timestep_eqs, self._final_eqs]):
        eq_xs = OrderedDict()
        for eq in eqs:
          for x in eq.X():
            eq_xs[x] = eq
        for eq in eqs:
          add_eq_deps(eq, eq_xs, self._sorted_eqs[i])
      
      self._tfns = []
      for eq in self._sorted_eqs[1]:
        for x in eq.X():
          x_tfn = x._tlm_adjoint__tfn
          if not x_tfn in self._tfns:
            self._tfns.append(x_tfn)
  
    if initialise:
      self.initialise()
      
    return self
  
  def initialise(self, manager = None):
    if self._state != "assembled":
      raise TimesteppingException("Invalid state")      
    self._state = "initialised"
    
    for eq in self._sorted_eqs[0]:
      eq.solve(manager = manager, replace = True)
    
    self._initial_eqs = []
    self._sorted_eqs[0] = []
    
    for tfn in self._tfns:
      for level in tfn.levels():
        AssignmentSolver(tfn[level._i], tfn[level])._post_annotate(manager = manager, replace = True)
      
  def timestep(self, s = 1, manager = None):
    if self._state == "initial":
      self.assemble(initialise = True)
    elif not self._state in ["initialised", "timestepping"]:
      raise TimesteppingException("Invalid state")
    self._state = "timestepping"
  
    for n in range(s):
      # Timestep solve
      for eq in self._sorted_eqs[1]:
        eq.solve(manager = manager, replace = False)
      # Timestep cycle
      for tfn in self._tfns:
        tfn.cycle(manager = manager)
  
  def finalise(self, manager = None):
    if self._state == "initial":
      self.assemble(initialise = True)
    elif not self._state in ["initialised", "timestepping"]:
      raise TimesteppingException("Invalid state")
    self._state = "final"
  
    for eq in self._timestep_eqs:
      eq.replace(manager = manager)
    self._timestep_eqs = []
    self._sorted_eqs[1] = []
    for tfn in self._tfns:
      if not tfn._cycle_eqs is None:
        for eq in tfn._cycle_eqs:
          eq.replace(manager = manager)
        tfn._cycle_eqs = None
        
    for tfn in self._tfns:
      for level in tfn.levels():
        AssignmentSolver(tfn[level], tfn[FinalTimeLevel(level._i)])._post_annotate(manager = manager, replace = True)
    self._tfns = None
    
    for eq in self._sorted_eqs[2]:
      eq.solve(manager = manager, replace = True)
    
    self._final_eqs = []
    self._sorted_eqs = None