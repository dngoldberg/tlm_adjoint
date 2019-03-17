#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# For tlm_adjoint copyright information see ACKNOWLEDGEMENTS in the tlm_adjoint
# root directory

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

from .backend import *
from .backend_interface import *

from .equations import AssignmentSolver, EquationSolver, ProjectionSolver
from .tlm_adjoint import annotation_enabled, tlm_enabled

from collections import OrderedDict
import copy
import ufl

__all__ = \
  [
    "OverrideException",
    
    "LinearVariationalSolver",
    "NonlinearVariationalProblem",
    "NonlinearVariationalSolver",
    "KrylovSolver",
    "LUSolver",
    "assemble",
    "assemble_system",
    "project",
    "solve"
  ]

class OverrideException(Exception):
  pass

def parameters_dict_equal(parameters_a, parameters_b):
  for key_a in parameters_a:
    value_a = parameters_a[key_a]
    if not key_a in parameters_b:
      return False
    value_b = parameters_b[key_a]
    if isinstance(value_a, (Parameters, dict)):
      if not isinstance(value_b, (Parameters, dict)):
        return False
      elif not parameters_dict_equal(value_a, value_b):
        return False
    elif value_a != value_b:
      return False
  for key_b in parameters_b:
    if not key_b in parameters_a:
      return False
  return True

# Aim for compatibility with FEniCS 2018.1.0 API

def assemble(form, tensor = None, form_compiler_parameters = None, add_values = False, *args, **kwargs):
  b = backend_assemble(form, tensor = tensor,
    form_compiler_parameters = form_compiler_parameters,
    add_values = add_values, *args, **kwargs)
  if tensor is None:
    tensor = b
      
  if not isinstance(tensor, float):
    form_compiler_parameters_ = copy_parameters_dict(parameters["form_compiler"])
    if not form_compiler_parameters is None:
      update_parameters_dict(form_compiler_parameters_, form_compiler_parameters)
    form_compiler_parameters = form_compiler_parameters_
  
    if add_values and hasattr(tensor, "_tlm_adjoint__form"):
      if tensor._tlm_adjoint__bcs != []:
        raise OverrideException("Non-matching boundary conditions")
      elif not parameters_dict_equal(tensor._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
        raise OverrideException("Non-matching form compiler parameters")
      tensor._tlm_adjoint__form += form
    else:
      tensor._tlm_adjoint__form = form
      tensor._tlm_adjoint__bcs = []
      tensor._tlm_adjoint__form_compiler_parameters = form_compiler_parameters
    
  return tensor
  
def assemble_system(A_form, b_form, bcs = None, x0 = None,
  form_compiler_parameters = None, add_values = False,
  finalize_tensor = True, keep_diagonal = False, A_tensor = None, b_tensor = None, *args, **kwargs):
  if not x0 is None:
    raise OverrideException("Non-linear boundary condition case not supported")
    
  A, b = backend_assemble_system(A_form, b_form, bcs = bcs, x0 = x0,
    form_compiler_parameters = form_compiler_parameters,
    add_values = add_values, finalize_tensor = finalize_tensor,
    keep_diagonal = keep_diagonal, A_tensor = A_tensor, b_tensor = b_tensor,
    *args, **kwargs)
  if A_tensor is None:
    A_tensor = A
  if b_tensor is None:
    b_tensor = b
  if bcs is None:
    bcs = []
  elif isinstance(bcs, backend_DirichletBC):
    bcs = [bcs]

  form_compiler_parameters_ = copy_parameters_dict(parameters["form_compiler"])
  if not form_compiler_parameters is None:
    update_parameters_dict(form_compiler_parameters_, form_compiler_parameters)
  form_compiler_parameters = form_compiler_parameters_
    
  if add_values and hasattr(A_tensor, "_tlm_adjoint__form"):
    if A_tensor._tlm_adjoint__bcs != bcs:
      raise OverrideException("Non-matching boundary conditions")
    elif not parameters_dict_equal(A_tensor._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
      raise OverrideException("Non-matching form compiler parameters")
    A_tensor._tlm_adjoint__form += A_form
  else:
    A_tensor._tlm_adjoint__form = A_form
    A_tensor._tlm_adjoint__bcs = list(bcs)
    A_tensor._tlm_adjoint__form_compiler_parameters = form_compiler_parameters
  
  if add_values and hasattr(b_tensor, "_tlm_adjoint__form"):
    if b_tensor._tlm_adjoint__bcs != bcs:
      raise OverrideException("Non-matching boundary conditions")
    elif not parameters_dict_equal(b_tensor._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
      raise OverrideException("Non-matching form compiler parameters")
    b_tensor._tlm_adjoint__form += b_form
  else:
    b_tensor._tlm_adjoint__form = b_form
    b_tensor._tlm_adjoint__bcs = list(bcs)
    b_tensor._tlm_adjoint__form_compiler_parameters = form_compiler_parameters
  
  return A_tensor, b_tensor

def solve(*args, **kwargs):
  kwargs = copy.copy(kwargs)
  annotate = kwargs.pop("annotate", None)
  tlm = kwargs.pop("tlm", None)
  
  if annotate is None:
    annotate = annotation_enabled()
  if tlm is None:
    tlm = tlm_enabled()
  if annotate or tlm:
    if isinstance(args[0], ufl.classes.Equation):
      eq, x, bcs, J, tol, M, form_compiler_parameters, solver_parameters = extract_args(*args, **kwargs)
      if not tol is None or not M is None:
        raise OverrideException("Adaptive solves not supported")
      lhs, rhs = eq.lhs, eq.rhs
      if isinstance(lhs, ufl.classes.Form) and isinstance(rhs, ufl.classes.Form) and \
        (x in lhs.coefficients() or x in rhs.coefficients()):
        x_old = function_new(x, name = "x_old")
        AssignmentSolver(x, F).solve(annotate = annotate, tlm = tlm)
        lhs = ufl.replace(lhs, OrderedDict([(x, x_old)]))
        rhs = ufl.replace(rhs, OrderedDict([(x, x_old)]))
        eq = lhs == rhs
      EquationSolver(eq, x, bcs, J = J,
        form_compiler_parameters = form_compiler_parameters,
        solver_parameters = solver_parameters, cache_jacobian = False,
        cache_rhs_assembly = False).solve(annotate = annotate, tlm = tlm)
    else:
      A, x, b = args[:3]
      solver_parameters = {}
      solver_parameters["linear_solver"] = "default" if len(args) < 4 else args[3]
      solver_parameters["preconditioner"] = "default" if len(args) < 5 else args[4]
      
      bcs = A._tlm_adjoint__bcs
      if bcs != b._tlm_adjoint__bcs:
        raise OverrideException("Non-matching boundary conditions")
      form_compiler_parameters = A._tlm_adjoint__form_compiler_parameters
      if not parameters_dict_equal(b._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
        raise OverrideException("Non-matching form compiler parameters")
      
      A = A._tlm_adjoint__form
      x = x._tlm_adjoint__function
      b = b._tlm_adjoint__form
      A_x_dep = x in ufl.algorithms.extract_coefficients(A)
      b_x_dep = x in ufl.algorithms.extract_coefficients(b)
      if A_x_dep or b_x_dep:
        x_old = function_new(x, name = "x_old")
        AssignmentSolver(x, x_old).solve(annotate = annotate, tlm = tlm)
        if A_x_dep: A = ufl.replace(A, OrderedDict([(x, x_old)]))
        if b_x_dep: b = ufl.replace(b, OrderedDict([(x, x_old)]))
        
      EquationSolver(A == b, x,
        bcs, solver_parameters = solver_parameters,
        form_compiler_parameters = form_compiler_parameters,
        cache_jacobian = False, cache_rhs_assembly = False).solve(annotate = annotate, tlm = tlm)
  else:
    backend_solve(*args, **kwargs)

def project(v, V = None, bcs = None, mesh = None, function = None,
  solver_type = "lu", preconditioner_type = "default",
  form_compiler_parameters = None, annotate = None, tlm = None):
  if bcs is None:
    bcs = []
  elif isinstance(bcs, backend_DirichletBC):
    bcs = [bcs]
      
  if annotate is None:
    annotate = annotation_enabled()
  if tlm is None:
    tlm = tlm_enabled()
  if annotate or tlm:
    if V is None:
      raise OverrideException("Function space required")
    if function is None:
      x = Function(V)
    else:
      x = function
    ProjectionSolver(v, x, bcs,
      solver_parameters = {"linear_solver":solver_type, "preconditioner":preconditioner_type},
      form_compiler_parameters = {} if form_compiler_parameters is None else form_compiler_parameters,
      cache_jacobian = False, cache_rhs_assembly = False).solve(annotate = annotate, tlm = tlm)
      # ?? Other solver parameters ?
    return x
  else:
    return backend_project(v, V = V, bcs = bcs, mesh = mesh, function = function,
      solver_type = solver_type, preconditioner_type = preconditioner_type,
      form_compiler_parameters = form_compiler_parameters)

_orig_DirichletBC_apply = backend_DirichletBC.apply
def _DirichletBC_apply(self, *args):
  _orig_DirichletBC_apply(self, *args)
  if (len(args) > 1 and not isinstance(args[0], backend_Matrix)) or len(args) > 2:
    return

  if isinstance(args[0], backend_Matrix):
    A = args[0]
    if len(args) > 1:
      b = args[1]
    else:
      b = None
  else:
    A = None
    b = args[0]

  if not A is None and hasattr(A, "_tlm_adjoint__bcs") and not self in A._tlm_adjoint__bcs:
    A._tlm_adjoint__bcs.append(self)
  if not b is None and hasattr(b, "_tlm_adjoint__bcs") and not self in b._tlm_adjoint__bcs:
    b._tlm_adjoint__bcs.append(self)
backend_DirichletBC.apply = _DirichletBC_apply

_orig_Function_assign = backend_Function.assign
def _Function_assign(self, rhs, annotate = None, tlm = None):
  return_value = _orig_Function_assign(self, rhs)
  if not is_function(rhs):
    return return_value
  
  if annotate is None:
    annotate = annotation_enabled()
  if tlm is None:
    tlm = tlm_enabled()
  if annotate or tlm:
    AssignmentSolver(rhs, self).solve(annotate = annotate, tlm = tlm)
  return return_value
backend_Function.assign = _Function_assign

_orig_Function_vector = backend_Function.vector
def _Function_vector(self):
  return_value = _orig_Function_vector(self)
  return_value._tlm_adjoint__function = self
  return return_value
backend_Function.vector = _Function_vector

_orig_Matrix_mul = backend_Matrix.__mul__
def _Matrix_mul(self, other):
  return_value = _orig_Matrix_mul(self, other)
  if hasattr(self, "_tlm_adjoint__form") and hasattr(other, "_tlm_adjoint__function") and len(self._tlm_adjoint__bcs) == 0:
    return_value._tlm_adjoint__form = ufl.action(self._tlm_adjoint__form, coefficient = other._tlm_adjoint__function)
    return_value._tlm_adjoint__bcs = []
    return_value._tlm_adjoint__form_compiler_parameters = self._tlm_adjoint__form_compiler_parameters
  return return_value
backend_Matrix.__mul__ = _Matrix_mul

class LUSolver(backend_LUSolver):
  def __init__(self, *args):
    backend_LUSolver.__init__(self, *args)
    if len(args) >= 1 and isinstance(args[0], backend_Matrix):
      self.__A = args[0]
      self.__linear_solver = args[1] if len(args) >= 2 else "default"
    elif len(args) >= 2 and isinstance(args[1], backend_Matrix):
      self.__A = args[1]
      self.__linear_solver = args[2] if len(args) >= 3 else "default"
    elif len(args) >= 1 and isinstance(args[0], str):
      self.__linear_solver = args[0]  # FEniCS < 2018.1.0 compatibility
    else:
      self.__linear_solver = args[1] if len(args) >= 2 else "default"
      
  def set_operator(self, A):
    backend_LUSolver.set_operator(self, A)
    self.__A = A

  def solve(self, *args, annotate = None, tlm = None):
    backend_LUSolver.solve(self, *args)
    
    if annotate is None:
      annotate = annotation_enabled()
    if tlm is None:
      tlm = tlm_enabled()
    if annotate or tlm:
      if isinstance(args[0], backend_Matrix):
        A, x, b = args
        self.__A = A
      else:
        A = self.__A
        x, b = args
        
      bcs = A._tlm_adjoint__bcs
      if bcs != b._tlm_adjoint__bcs:
        raise OverrideException("Non-matching boundary conditions")
      form_compiler_parameters = A._tlm_adjoint__form_compiler_parameters
      if not parameters_dict_equal(b._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
        raise OverrideException("Non-matching form compiler parameters")
      eq = EquationSolver(A._tlm_adjoint__form == b._tlm_adjoint__form, x._tlm_adjoint__function,
        bcs, solver_parameters = {"linear_solver":self.__linear_solver, "lu_solver":self.parameters},
        form_compiler_parameters = form_compiler_parameters, cache_jacobian = False, cache_rhs_assembly = False)
      eq._post_process(annotate = annotate, tlm = tlm)

class KrylovSolver(backend_KrylovSolver):
  def __init__(self, *args):
    backend_KrylovSolver.__init__(self, *args)
    if len(args) >= 1 and isinstance(args[0], backend_Matrix):
      self.__A = args[0]
      self.__linear_solver = args[1] if len(args) >= 2 else "default"
      self.__preconditioner = args[2] if len(args) >= 3 else "default"
    elif len(args) >= 2 and isinstance(args[1], backend_Matrix):
      self.__A = args[1]
      self.__linear_solver = args[2] if len(args) >= 3 else "default"
      self.__preconditioner = args[3] if len(args) >= 4 else "default"
    elif len(args) >= 1 and isinstance(args[0], str):
      self.__linear_solver = args[0]
      self.__preconditioner = args[1] if len(args) >= 2 else "default"
    else:
      self.__linear_solver = args[1] if len(args) >= 2 else "default"
      self.__preconditioner = args[2] if len(args) >= 3 else "default"
      
  def set_operator(self, A):
    backend_KrylovSolver.set_operator(self, A)
    self.__A = A

  def set_operators(self, *args, **kwargs):
    raise OverrideException("Preconditioners not supported")

  def solve(self, *args, annotate = None, tlm = None):
    if annotate is None:
      annotate = annotation_enabled()
    if tlm is None:
      tlm = tlm_enabled()
    if annotate or tlm:
      if isinstance(args[0], backend_Matrix):
        A, x, b = args
        self.__A = None
      else:
        A = self.__A
        x, b = args
        
      bcs = A._tlm_adjoint__bcs
      if bcs != b._tlm_adjoint__bcs:
        raise OverrideException("Non-matching boundary conditions")
      form_compiler_parameters = A._tlm_adjoint__form_compiler_parameters
      if not parameters_dict_equal(b._tlm_adjoint__form_compiler_parameters, form_compiler_parameters):
        raise OverrideException("Non-matching form compiler parameters")
      eq = EquationSolver(A._tlm_adjoint__form == b._tlm_adjoint__form, x._tlm_adjoint__function,
        bcs, solver_parameters = {"linear_solver":self.__linear_solver, "preconditioner":self.__preconditioner, "krylov_solver":self.parameters},
        form_compiler_parameters = form_compiler_parameters, cache_jacobian = False, cache_rhs_assembly = False)

      eq._pre_process(annotate = annotate)
      backend_KrylovSolver.solve(self, *args)
      eq._post_process(annotate = annotate, tlm = tlm)
    else:
      backend_KrylovSolver.solve(self, *args)
      
class LinearVariationalSolver(backend_LinearVariationalSolver):
  def __init__(self, problem):
    backend_LinearVariationalSolver.__init__(self, problem)
    self.__problem = problem
  
  def solve(self, annotate = None, tlm = None):
    if annotate is None:
      annotate = annotation_enabled()
    if tlm is None:
      tlm = tlm_enabled()
    if annotate or tlm:
      EquationSolver(self.__problem.a_ufl == self.__problem.L_ufl,
        self.__problem.u_ufl, self.__problem.bcs(),
        solver_parameters = self.parameters,
        form_compiler_parameters = self.__problem.form_compiler_parameters,
        cache_jacobian = False, cache_rhs_assembly = False).solve(annotate = annotate, tlm = tlm)
    else:
      backend_LinearVariationalSolver.solve(self)

class NonlinearVariationalProblem(backend_NonlinearVariationalProblem):
  def __init__(self, F, u, bcs = None, J = None,
    form_compiler_parameters = None):      
    backend_NonlinearVariationalProblem.__init__(self, F, u, bcs = bcs, J = J,
      form_compiler_parameters = form_compiler_parameters)
    if bcs is None:
      self._tlm_adjoint__bcs = []
    elif isinstance(bcs, backend_DirichletBC):
      self._tlm_adjoint__bcs = [bcs]
    else:
      self._tlm_adjoint__bcs = list(bcs)
      
  def set_bounds(self, *args, **kwargs):
    raise OverrideException("Bounds not supported")
    
class NonlinearVariationalSolver(backend_NonlinearVariationalSolver):
  def __init__(self, problem):
    backend_NonlinearVariationalSolver.__init__(self, problem)
    self.__problem = problem
  
  def solve(self, annotate = None, tlm = None):
    if annotate is None:
      annotate = annotation_enabled()
    if tlm is None:
      tlm = tlm_enabled()
    if annotate or tlm:
      eq = EquationSolver(self.__problem.F_ufl == 0,
        self.__problem.u_ufl, self.__problem._tlm_adjoint__bcs,
        J = self.__problem.J_ufl,
        solver_parameters = self.parameters,
        form_compiler_parameters = self.__problem.form_compiler_parameters,
        cache_jacobian = False, cache_rhs_assembly = False)
        
      eq._pre_process(annotate = annotate)
      return_value = backend_NonlinearVariationalSolver.solve(self)
      eq._post_process(annotate = annotate, tlm = tlm)
      return return_value
    else:
      return backend_NonlinearVariationalSolver.solve(self)
