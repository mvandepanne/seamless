import seamless
from seamless.core import macro_mode_on
from seamless.core import context, cell, transformer, pytransformercell, pythoncell

with macro_mode_on():
    ctx = context(toplevel=True)
    ctx.cell1 = cell().set(1)
    ctx.cell2 = cell().set(2)
    ctx.result = cell()
    ctx.tf = transformer({
        "a": "input",
        "b": "input",
        "testmodule": ("input", "ref", "module", "python"),
        "c": "output",
    })
    ctx.cell1.connect(ctx.tf.a)
    ctx.cell2.connect(ctx.tf.b)
    ctx.code = pytransformercell().set("""
print("macro execute")
print(testmodule)
print(testmodule.q)
from .testmodule import q
print(q)
import sys
print([m for m in sys.modules if m.find("testmodule") > -1])
c = a + b
print("/macro execute")""")
    ctx.code.connect(ctx.tf.code)
    ctx.tf.c.connect(ctx.result)

    ctx.testmodule = pythoncell().set("q = 10")
    ctx.testmodule.connect(ctx.tf.testmodule)

ctx.equilibrate()
print(ctx.result.value)
ctx.cell1.set(10)
ctx.equilibrate()
print(ctx.result.value)
ctx.code.set("c = a + b + 1000")
ctx.equilibrate()
print(ctx.result.value)
print(ctx.status())
