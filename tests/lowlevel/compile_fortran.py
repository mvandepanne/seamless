from seamless.compiler import compile
from seamless.compiler.cffi import cffi
from seamless.compiler.build_extension import build_extension_cffi

######################################################################
# 1: set up compiled module
######################################################################

code = """
function add(a, b) result(r) bind(C)
    use iso_c_binding
    implicit none
    integer(c_int), VALUE:: a, b
    real(c_float) r
    r = a + b
end function
"""
module = {
    "target": "debug",
    "objects": {
        "main": {
            "code": code,
            "language": "f90",
        },
    },
    "public_header": {
        "language": "c",
        "code": "float add(int a, int b);"
    }
}

######################################################################
# 2: compile it to binary module
######################################################################

compiler_verbose = True
import tempfile, os
tempdir = tempfile.gettempdir() + os.sep + "compile_fortran"
binary_module = compile(module, tempdir, compiler_verbose=compiler_verbose)

######################################################################
# 3: build and test extension directly
######################################################################

module_name = build_extension_cffi(binary_module, compiler_verbose=compiler_verbose)
import sys
testmodule = sys.modules[module_name].lib
print(testmodule.add(2,3))

######################################################################
# 4: test the mixed serialization protocol on the binary module
######################################################################

from seamless.mixed.get_form import get_form
from seamless.mixed.io import to_stream, from_stream
from seamless.mixed.io.util import is_identical_debug

storage, form = get_form(binary_module)
x = to_stream(binary_module, storage, form)
binary_module2 = from_stream(x, storage, form)

assert (is_identical_debug(binary_module, binary_module2))

######################################################################
# 5: test it in a context
######################################################################

from seamless.core import context, cell, transformer, macro_mode_on
with macro_mode_on():
    ctx = context(toplevel=True)
    ctx.module_storage = cell("text")
    ctx.module_form = cell("json")
    ctx.module = cell("mixed", form_cell=ctx.module_form, storage_cell=ctx.module_storage)
    ctx.module.set(binary_module, auto_form=True)
    tf = ctx.tf = transformer({
        "a": ("input", "ref", "json"),
        "b": ("input", "ref", "json"),
        "testmodule": ("input", "ref", "binary_module"),
        "result": ("output", "ref", "json"),
    })
    ctx.module.connect(tf.testmodule)
    tf.a.cell().set(2)
    tf.b.cell().set(3)
    tf.code.cell().set("""
from .testmodule import lib
print("ADD", lib.add(a,b))
result = testmodule.lib.add(a,b)
    """)
    ctx.result = cell("json")
    ctx.tf.result.connect(ctx.result)

ctx.equilibrate()
print(ctx.result.value)
