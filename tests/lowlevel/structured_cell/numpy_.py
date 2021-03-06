import seamless
from seamless.core import macro_mode_on
from seamless.core import context, cell, transformer, StructuredCell
import numpy as np

with macro_mode_on():
    ctx = context(toplevel=True)
    ctx.mount("/tmp/mount-test", persistent=None) #directory remains, but empty

    ctx.inp_struc = context(name="inp_struc",context=ctx)
    ctx.inp_struc.storage = cell("text")
    ctx.inp_struc.form = cell("json")
    ctx.inp_struc.data = cell("mixed",
        form_cell = ctx.inp_struc.form,
        storage_cell = ctx.inp_struc.storage,
    )
    ctx.inp = StructuredCell(
        "inp",
        ctx.inp_struc.data,
        storage = ctx.inp_struc.storage,
        form = ctx.inp_struc.form,
        schema = None,
        buffer = None,
        inchannels = None,
        outchannels = [("a",), ("b",), ("data",)]
    ).export()
    ctx.tf = transformer({
        "a": "input",
        "b": "input",
        "data": "input",
        "c": "output"
    })

    ctx.inp.connect_outchannel(("a",), ctx.tf.a)
    ctx.inp.connect_outchannel(("b",), ctx.tf.b)
    ctx.inp.connect_outchannel(("data",), ctx.tf.data)
    ctx.tf.code.cell().set("c = a * data + b")

    ctx.result = cell("array").export()
    #ctx.result.mount(persistent=True)
    ctx.tf.c.connect(ctx.result)


ctx.equilibrate()
print(ctx.tf.status())
print(ctx.result.value)

#ctx.inp.set({})
inp = ctx.inp.handle
inp["a"] = 10
inp["b"] = 12
inp["data"] = np.arange(100)

ctx.equilibrate()
print(ctx.tf.status())

print(ctx.result.value)

#shell = ctx.tf.shell()
