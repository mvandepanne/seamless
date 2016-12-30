from collections import OrderedDict
import functools
import copy
from .context import context, get_active_context
from contextlib import contextmanager as _pystdlib_contextmanager

#TODO: implement lib system, and let pins connect to CellProxies instead of
# cells. CellProxies work exactly the same as cells
# (pass-through attribute access), except that they can be forked
#TODO: get the source of the macro, store this in a cell, and make sure that
# 1. it gets properly captured by the lib system
# 2. if the source of the macro is different from the lib cell content,
# fork any cellproxies linking to the old lib cell, then update the lib cell,
# and issue a warning

#TODO: with_context = False should *still* connect to active context!

_macro_mode = False

def get_macro_mode():
    return _macro_mode

def set_macro_mode(macro_mode):
    global _macro_mode
    _macro_mode = macro_mode

def _parse_init_args(type=None, with_context=True):
    return type, with_context

def _parse_type_args(type):
    if type is None:
        return None
    if isinstance(type, tuple) or isinstance(type, str):
        return dict(
         _order=["_arg1"],
         _required=["_arg1"],
         _default={},
         _arg1=type,
        )
    if not isinstance(type, dict):
        raise TypeError(type.__class__)
    order = []
    required = set()
    default = {}
    ret = dict(_order=order, _required=required, _default=default)
    last_nonreq = None
    for k in type:
        if k.startswith("_"): assert k.startswith("_arg"), k
        v = type[k]
        is_req = True
        if isinstance(v, dict) and \
         (v.get("optional", False) or "default" in v):
            is_req = False
        if isinstance(type, OrderedDict):
            order.append(k)
            if is_req and last_nonreq:
                exc = "Cannot specify required argument '{0}' after non-required argument '{1}'"
                raise Exception(exc.format(k, last_nonreq))

        vtype = v
        if isinstance(v, dict):
            vtype = v["type"]
            if "default" in v:
                #TODO: checking regarding type <=> default
                #TODO: check that the default can be pickled
                default[k] = v["default"]
        ret[k] = vtype

        if is_req:
            required.add(k)
        else:
            last_nonreq = k
    ret = copy.deepcopy(ret)
    return ret

def _resolve(a):
    from .cell import Cell
    from ..dtypes import parse
    if isinstance(a, Cell):
        return parse(a.dtype, a._data, trusted=True)
    else:
        return a

def _resolve_type_args(type_args, args0, kwargs0):
    """
    #TODO: get cells that have been resolved
    When macro object is created and attached to context X, verify that all those
     cells and X have a common ancestor (._root)
    """
    args = [_resolve(a) for a in args0]
    kwargs = {k: _resolve(v) for k, v in kwargs0.items()}
    if type_args is None:
        return args, kwargs

    #TODO: take and adapt corresponding routine from Hive
    args2, kwargs2 = [], {}
    order = type_args["_order"]
    assert len(args) <= len(order)
    positional_done = set()
    for anr in range(len(args)):
        argname = order[anr]
        arg = args[anr]
        #TODO: type validation
        if argname.startswith("_arg"):
            args2.append(arg)
            positional_done.add(argname)
        else:
            kwargs2[argname] = arg
    for argname in kwargs:
        assert not argname.startswith("_"), argname #not supported
        assert argname in type_args, (argname, [v for v in type_args.keys() if not v.startswith("_")])
        arg = kwargs[argname]
        #TODO: type validation
        kwargs2[argname] = arg

    default = type_args["_default"]
    required = type_args["_required"]
    for argname in required:
        if argname.startswith("_arg"):
            assert argname in positional_done, argname #TODO: error message
        else:
            assert argname in kwargs2, argname #TODO: error message
    for argname in type_args:
        if argname.startswith("_"):
            continue
        if argname in kwargs2 and kwargs2[argname] is not None:
            continue
        arg_default = default.get(argname, None)
        kwargs2[argname] = arg_default
    return args2, kwargs2


def _func_macro(func, type_args, with_context, *args, **kwargs):
    args2, kwargs2 = _resolve_type_args(type_args, args, kwargs)
    previous_macro_mode = get_macro_mode()
    if with_context:
        ctx = context(parent=get_active_context(), active_context=False)
        try:
            set_macro_mode(True)
            ret = func(ctx, *args2, **kwargs2)
            if ret is not None:
                raise TypeError("Context macro must return None")
            return ctx
        finally:
            set_macro_mode(previous_macro_mode)
    else:
        try:
            set_macro_mode(True)
            ret = func(*args2, **kwargs2)
        finally:
            set_macro_mode(previous_macro_mode)
        return ret

def macro(*args, **kwargs):
    """
    Macro decorator

    type: a single type tuple, or a dict/OrderedDict
      if type is a dict, then
        every key is a parameter name
        every value is either a type tuple, or
         a dict, where:
            "type" is the type tuple,
            "optional" (optional) is a boolean
            "default" (optional) is a default value
        the type dict is parsed from **kwargs (keyword arguments)
        unspecified optional arguments default to None, unless "default" is
         specified
      if type is an OrderedDict, then
       as above, but
       the type dict is parsed from *args (positional arguments)
        and **kwargs (keyword arguments)

    As an additional value for type tuple, "self" is allowed. This indicates
     that the macro is a method decorator, and the first argument
     is a bound object. In this case, the macro source is never stored

    with_context: bool
      if True, the function is passed a context object as additional
       first parameter, and is expected to return None
      if False, the function is expected to return a cell or process.
       This cell or process must be added manually to the context
       The returned cell or process is marked as "owner" of any other cells
       or processes created by the macro. When the owner is deleted, the owned
       cells and processes are deleted as well
       (TODO!)

    Example 1:
    @macro
    defines a macro with with_context = True, no type checking

    Example 2:
    @macro("str")
    defines a macro with a single argument, which must be of type "str",
     and with_context is True.

    Example 2:
    @macro({
      "spam": { "type":"str", "optional":True },
      "ham": ("code", "python"),
      "eggs": "int"
    })
    defines a macro with a three arguments. The arguments must be defined as
     keyword arguments, and "spam" is optional


    """
    if len(args) == 1 and callable(args[0]) and not len(kwargs):
        type, with_context = _parse_init_args()
        func = args[0]
        return functools.update_wrapper(functools.partial(
         _func_macro,
         func,
         type,
         with_context
        ),func)
    else:
        type, with_context = _parse_init_args(*args, **kwargs)
        type_args = _parse_type_args(type)
        def func_macro_wrapper(func):
            return functools.update_wrapper(functools.partial(
             _func_macro,
             func,
             type_args,
             with_context
            ), func)
        return func_macro_wrapper
