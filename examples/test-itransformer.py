from seamless import context
from seamless.lib.filelink import link
from seamless.lib.itransformer import itransformer
from seamless.lib.gui.basic_display import display
from seamless.lib.gui.basic_editor import edit
ctx = context()
ctx.itf = itransformer({
    "i": {"pin": "input", "dtype": "int"},
    "outp": {"pin": "output", "dtype": "json"},
})
link(ctx.itf.code.cell(), ".", "test-itransformer_pycell.py")
link(ctx.itf.ed.code_start.cell())
display(ctx.itf.outp.cell())
edit(ctx.itf.i.cell().set(100))