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

import copy
from collections import OrderedDict
import ufl

__all__ = \
  [
    "AssemblyCache",
    "Cache",
    "CacheException",
    "CacheIndex",
    "Constant",
    "DirichletBC",
    "Function",
    "LinearSolverCache",
    "ReplacementFunction",
    "assembly_cache",
    "homogenized",
    "is_homogeneous_bcs",
    "is_static",
    "is_static_bcs",
    "linear_solver_cache",
    "new_id",
    "replaced_form",
    "replaced_function",
    "set_assembly_cache",
    "set_linear_solver_cache",
    "split_action",
    "split_form"
  ]

class CacheException(Exception):
  pass
  
class Constant(base_Constant):
  # Following FEniCS 2017.1.0 API
  def __init__(self, value, cell = None, name = None, static = False):
    base_Constant.__init__(self, value, cell = cell, name = name)
    self.__static = static
  
  def is_static(self):
    return self.__static
  
class Function(base_Function):
  def __init__(self, *args, **kwargs):
    kwargs = copy.copy(kwargs)
    static = kwargs.pop("static", False)
    
    base_Function.__init__(self, *args, **kwargs)
    self.__static = static
  
  def is_static(self):
    return self.__static

def homogenized(bc):
  if isinstance(bc, DirichletBC) and bc.is_homogeneous():
    return bc
  else:
    hbc = DirichletBC(bc, static = is_static_bcs([bc]), homogeneous = is_homogeneous_bcs([bc]))
    hbc.homogenize()
    return hbc

class DirichletBC(base_DirichletBC):
  # Following FEniCS 2017.1.0 API
  def __init__(self, *args, **kwargs):
    if isinstance(args[0], DirichletBC):
      homogeneous = args[0].is_homogeneous()
    else:
      homogeneous = None
      
    kwargs = copy.copy(kwargs)
    static = kwargs.pop("static", False)
    homogeneous = kwargs.pop("homogeneous", homogeneous)
    
    base_DirichletBC.__init__(self, *args, **kwargs)
    
    if homogeneous is None:
      if static:
        b = Function(self.function_space()).vector()
        self.apply(b)
        homogeneous = b.norm("linf") == 0.0
      else:
        homogeneous = False
    
    self.__static = static
    self.__homogeneous = homogeneous
  
  def is_static(self):
    return self.__static
  
  def is_homogeneous(self):
    return self.__homogeneous
  
  def homogenize(self):
    if not self.__homogeneous:
      base_DirichletBC.homogenize(self)
      self.__homogeneous = True

def is_static(expr):
  for c in ufl.algorithms.extract_coefficients(expr):
    if not hasattr(c, "is_static") or not c.is_static():
      return False
  return True

def is_static_bcs(bcs):
  for bc in bcs:
    if not hasattr(bc, "is_static") or not bc.is_static():
      return False
  return True

def is_homogeneous_bcs(bcs):
  for bc in bcs:
    if not hasattr(bc, "is_homogeneous") or not bc.is_homogeneous():
      return False
  return True

def split_form(form):
  def sum_terms(*terms):
    sum = ufl.classes.Zero()
    for term in terms:
      sum += term
    return sum
  def expand(terms):
    new_terms = []
    for term in terms:
      if isinstance(term, ufl.classes.Sum):
        new_terms += expand(term.ufl_operands)
      else:
        new_terms.append(term)
    return new_terms

  static_integrals, non_static_integrals = [], []
  
  for integral in form.integrals():
    integral_args = [integral.integral_type(),
                     integral.ufl_domain(),
                     integral.subdomain_id(),
                     integral.metadata(),
                     integral.subdomain_data()]
  
    static_operands, non_static_operands = [], []
    for operand in expand([integral.integrand()]):
      if is_static(operand):
        static_operands.append(operand)
      else:
        non_static_operands.append(operand)
    if len(static_operands) > 0:
      static_integrals.append(ufl.classes.Integral(sum_terms(*static_operands), *integral_args))
    if len(non_static_operands) > 0:
      non_static_integrals.append(ufl.classes.Integral(sum_terms(*non_static_operands), *integral_args))
  
  static_form = ufl.classes.Form(static_integrals)
  non_static_form = ufl.classes.Form(non_static_integrals)
  
  return static_form, non_static_form

def split_action(form, x):
  if len(form.arguments()) != 1:
    # Not a linear form
    return ufl.classes.Form([]), form
  
  if not x in form.coefficients():
    # No dependence on x
    return ufl.classes.Form([]), form
  
  trial = TrialFunction(x.function_space())
  if x in ufl.algorithms.expand_derivatives(derivative(form, x, du = trial)).coefficients():
    # Non-linear
    return ufl.classes.Form([]), form
  
  try:
    lhs, rhs = system(replace(form, OrderedDict([(x, trial)])))
  except ufl.UFLException:
    # UFL error encountered
    return ufl.classes.Form([]), form
  
  if not is_static(lhs):
    # Non-static bi-linear form
    return ufl.classes.Form([]), form
  
  # Success
  return lhs, -rhs
  
def parameters_key(parameters):
  key = []
  for name in sorted(parameters.keys()):
    sub_parameters = parameters[name]
    if isinstance(sub_parameters, (Parameters, dict)):
      key.append((name, parameters_key(sub_parameters)))
    else:
      key.append((name, sub_parameters))
  return tuple(key)
  
class CacheIndex:
  def __init__(self, index = None):
    self._index = index
  
  def clear(self):
    self.set_index(None)
  
  def index(self):
    return self._index
  
  def set_index(self, index):
    self._index = index

class Cache:
  def __init__(self):
    self._keys = OrderedDict()
    self._cache = []
  
  def __getitem__(self, key):
    return self._cache[key.index()]
  
  def clear(self):
    for value in self._keys.values():
      value.clear()
    self._keys.clear()
    self._cache.clear()
  
  def append(self, key, value):
    self._cache.append(value)
    index = CacheIndex(len(self._cache) - 1)
    self._keys[key] = index
    return index

def new_id():
  return Variable().id()
  
class ReplacementFunction(ufl.classes.Coefficient):
  def __init__(self, x):
    ufl.classes.Coefficient.__init__(self, x.function_space(), count = new_id())
    self.__space = x.function_space()
    self.__id = x.id()
    self.__name = x.name()
    self.__static = is_static(x)
    if hasattr(x, "_tlm_adjoint__tlm_basename"):
      self._tlm_adjoint__tlm_basename = x._tlm_adjoint__tlm_basename
    if hasattr(x, "_tlm_adjoint__tlm_depth"):
      self._tlm_adjoint__tlm_depth = x._tlm_adjoint__tlm_depth
  
  def function_space(self):
    return self.__space
  
  def id(self):
    return self.__id
  
  def name(self):
    return self.__name
  
  def is_static(self):
    return self.__static

def replaced_function(x):
  if isinstance(x, ReplacementFunction):
    return x
  if not hasattr(x, "_tlm_adjoint__ReplacementFunction"):
    x._tlm_adjoint__ReplacementFunction = ReplacementFunction(x)
  return x._tlm_adjoint__ReplacementFunction

def replaced_form(form):
  replace_map = OrderedDict()
  for c in form.coefficients():
    if isinstance(c, base_Function):
      replace_map[c] = replaced_function(c)
  return replace(form, replace_map)

def assemble_key(form, bcs, form_compiler_parameters):  
  return (ufl.algorithms.expand_indices(ufl.algorithms.expand_compounds(ufl.algorithms.expand_derivatives(replaced_form(form)))),
          tuple(bcs), parameters_key(form_compiler_parameters))

class AssemblyCache(Cache):
  def assemble(self, form, bcs = [], form_compiler_parameters = {}):  
    key = assemble_key(form, bcs, form_compiler_parameters)
    index = self._keys.get(key, None)
    if index is None:
      rank = len(form.arguments())
      if rank == 0:
        if len(bcs) > 0:
          raise CacheException("Unexpected boundary conditions for rank 0 form")
        b = assemble(form, form_compiler_parameters = form_compiler_parameters)
      elif rank == 1:
        b = assemble(form, form_compiler_parameters = form_compiler_parameters)
        for bc in bcs:
          bc.apply(b)
      elif rank == 2:
        if len(bcs) > 0:
          test = TestFunction(form.arguments()[0].function_space())
          test_shape = test.ufl_element().value_shape()
          dummy_rhs = inner(test, Constant(0.0 if len(test_shape) == 0 else numpy.zeros(test_shape, dtype = numpy.float64))) * dx
          b, b_bc = assemble_system(form, dummy_rhs, bcs, form_compiler_parameters = form_compiler_parameters)
          if b_bc.norm("linf") > 0.0:
            b = (b, b_bc)
          else:
            b = (b, None)
        else:
          b = (assemble(form, form_compiler_parameters = form_compiler_parameters), None)
      else:
        raise CacheException("Unexpected form rank %i" % rank)
      index = self.append(key, b)
    else:
      b = self[index]
      
    return index, b

def linear_solver_key(form, bcs, linear_solver_parameters):
  return (ufl.algorithms.expand_indices(ufl.algorithms.expand_compounds(ufl.algorithms.expand_derivatives(replaced_form(form)))),
          tuple(bcs), parameters_key(linear_solver_parameters))

class LinearSolverCache(Cache):
  def linear_solver(self, form, bcs = [], linear_solver_parameters = {}):
    linear_solver = linear_solver_parameters["linear_solver"]
    if linear_solver in ["direct", "lu"]:
      linear_solver = "default"
      is_lu_solver = True
    elif linear_solver == "iterative":
      linear_solver = "gmres"
      is_lu_solver = False
    else:
      is_lu_solver = has_lu_solver_method(linear_solver)
    
    key = linear_solver_key(form, bcs, linear_solver_parameters)
    index = self._keys.get(key, None)
    if index is None:
      if is_lu_solver:
        solver = LUSolver(linear_solver)
        solver.parameters.update(linear_solver_parameters.get("lu_solver", {}))
      else:
        solver = KrylovSolver(linear_solver, linear_solver_parameters.get("preconditioner", "default"))
        solver.parameters.update(linear_solver_parameters.get("krylov_solver", {}))
      index = self.append(key, solver)
    else:
      solver = self[index]

    return index, solver

_caches = [AssemblyCache(), LinearSolverCache()]
def assembly_cache():
  return _caches[0]
def set_assembly_cache(assembly_cache):
  _caches[0] = assembly_cache
def linear_solver_cache():
  return _caches[1]
def set_linear_solver_cache(linear_solver_cache):
  _caches[1] = linear_solver_cache